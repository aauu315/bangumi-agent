"""Public and account reads, a local preview, and one write that only the user's popup answer can start."""

from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from bangumi_agent import api_client

mcp = MCPServer("Bangumi Agent")

# Parameters several tools share. These descriptions reach the model through the tool schema.
SubjectId = Annotated[int, Field(description="条目ID，来自搜索结果或条目链接；不是章节ID，也不是第几集", gt=0)]
Offset = Annotated[int, Field(description="跳过的记录数，不是页码", ge=0)]
EpisodeType = Annotated[int | None, Field(
    description="章节种类：0本篇、1特别篇、2 OP、3 ED、4预告宣传、5 MAD、6其他；null表示全部", ge=0, le=6)]


@mcp.tool()
def search_subjects(
    keyword: Annotated[str, Field(description="搜索关键词，不能是空白")],
    limit: Annotated[int, Field(description="本次最多返回多少条", ge=1, le=20)] = 10,
    offset: Offset = 0,
) -> dict:
    """按关键词搜索公开的 Bangumi 条目，返回一页候选。

    适用于用户提供作品名称或关键词、尚不知道条目 ID 的情况。
    返回 total、limit、offset 和 data；data 中每项包含 id、name、name_cn、type、url。
    搜索可能返回同名作品及不同类型条目，应结合名称和类型确认目标；
    无法确定时询问用户，再用 get_subject 查询详情。
    本工具只读，无需账户凭据，不修改收藏。
    """
    try:
        return api_client.search_subjects(keyword, limit, offset)
    except (ValueError, api_client.BangumiApiError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def get_subject(subject_id: SubjectId) -> dict:
    """按 Bangumi 条目 ID 查询公开详情。

    返回名称、类型、简介、日期、集数、评分、图片和条目链接等字段；部分字段可能为空。
    eps 和 total_episodes 是作品信息，不是本人的观看进度。
    本工具只读，无需账户凭据，不查询或修改个人收藏。
    条目不存在或请求失败时返回工具错误，不能当作查询成功。
    """
    try:
        return api_client.get_subject(subject_id)
    except (ValueError, api_client.BangumiApiError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def preview_episode_watched(
    subject_id: SubjectId,
    episode_id: Annotated[int, Field(description="章节ID，来自get_episodes；不是第几集", gt=0)],
) -> dict:
    """只读核对本人收藏及章节归属，创建单章节准备标记看过的本地草稿。

    仅在用户明确选定目标后调用；默认使用本机.secrets/bangumi-token.txt，
    不接受或要求用户在聊天传Token。计划有效期15分钟，保存账号、修改前后状态。
    返回创建时的快照，不代表当前实时状态。本工具不会批准或执行修改；
    已看过、未收藏条目或跨条目章节将拒绝。只写本地SQLite，不修改Bangumi。
    """
    from pathlib import Path
    import sqlite3
    from .account_client import AccountClient, AccountReadError
    from .settings import Settings
    from .storage import PlanStore
    from .progress import ProgressPreview

    root = Path(__file__).resolve().parents[2]
    try:
        client = AccountClient(Settings.from_token_file(root / ".secrets/bangumi-token.txt"))
        plan = ProgressPreview(client, PlanStore(root / "state/progress.sqlite3")).create(
            subject_id, episode_id,
        )
        return {**plan, "remote_write_performed": False, "snapshot_only": True}
    except (ValueError, AccountReadError) as exc:
        raise ToolError(str(exc)) from None
    except (sqlite3.Error, OSError):
        raise ToolError("本地数据库无法读取或保存") from None
    except Exception:
        raise ToolError("预览失败；已隐藏原始异常，请运行离线测试检查") from None


@mcp.tool()
def preview_watched_up_to(
    subject_id: SubjectId,
    episode_id: Annotated[int, Field(description="用户看到的那一集的章节ID，来自get_episodes；不是第几集", gt=0)],
) -> dict:
    """创建“看到第N集”的本地草稿，用于“我看到第8集了”“这部前8集都看了”。只标记单独一集用preview_episode_watched。

    程序按网站“看到”的规则选集：只算本篇；第1集到这一集中未收藏和想看的改成看过；已看过、已抛弃的跳过并列出。
    确定是哪一集：一般按本季序号（ep）；数字明显超出本季时按总序号（sort）；不确定就问用户。
    多季作品优先选用户标记为在看的那一季，第一季已看过时用户说的通常是后面一季。
    返回items（要改的集和修改前状态）、skipped（跳过的集）。默认使用本机Token。
    本工具不会批准或执行修改，只写本地SQLite；执行用execute_episode_plan。
    """
    with _safe_tool_errors():
        plan = _progress_preview().create_up_to(subject_id, episode_id)
        return {**plan, "remote_write_performed": False, "snapshot_only": True}


@mcp.tool()
def preview_episode_change(
    subject_id: SubjectId,
    episode_ids: Annotated[list[int], Field(
        description="要改的章节ID列表，来自get_episodes；一个ID就是改一集，最多50个，都要属于这部作品；不是第几集",
        min_length=1, max_length=50)],
    target_state: Annotated[int, Field(description="目标状态：0未收藏、1想看、2看过、3抛弃", ge=0, le=3)],
) -> dict:
    """创建把指定几集改成某个状态的本地草稿，用于“把第3集标成抛弃”“撤销第5集的看过”“这几集改回想看”。

    撤销就是把状态改回去，用本工具。用户说“看到第N集”时用preview_watched_up_to，它会按规则选集。
    OP、ED、SP也可以改。有ID不属于这部作品时整份计划被拒绝；已是目标状态的集会跳过并列出；作品要已收藏，抛弃等状态也可以改，弹窗会显示条目状态。
    返回items（要改的集和修改前状态）、skipped（跳过的集）、subject_type（条目收藏状态：1想看、2看过、3在看、4搁置、5抛弃）。默认使用本机Token。
    本工具不会批准或执行修改，只写本地SQLite；执行用execute_episode_plan。
    """
    with _safe_tool_errors():
        plan = _progress_preview().create_change(subject_id, episode_ids, target_state)
        return {**plan, "remote_write_performed": False, "snapshot_only": True}


NO_POPUP = "当前客户端无法弹窗确认，未修改Bangumi；可在本机用 progress_preview.py approve 和 execute 完成"


def _omit_root_title(schema: dict) -> None:
    schema.pop("title", None)


class EpisodeApproval(BaseModel):
    # Codex Desktop on Windows rejects a root "title" and answers cancel with no popup
    # (openai/codex#46003), so send only the property-level labels.
    model_config = ConfigDict(json_schema_extra=_omit_root_title)

    confirm: bool = Field(default=True, title="确认修改", description="确认执行上面这一次修改")


def _execution_service():
    from pathlib import Path
    from .account_client import AccountClient
    from .confirmation import ExecutionService
    from .episode_writer import BangumiEpisodeWriter
    from .settings import Settings
    from .storage import PlanStore

    root = Path(__file__).resolve().parents[2]
    settings = Settings.from_token_file(root / ".secrets/bangumi-token.txt")
    return ExecutionService(AccountClient(settings), PlanStore(root / "state/progress.sqlite3"),
                            BangumiEpisodeWriter(settings))


@contextmanager
def _safe_tool_errors():
    """Turn failures into short tool errors; raw exceptions could carry the token."""
    import sqlite3
    from .account_client import AccountReadError

    try:
        yield
    except ToolError:
        raise
    except (ValueError, AccountReadError) as exc:
        raise ToolError(str(exc)) from None
    except (sqlite3.Error, OSError):
        raise ToolError("本地数据库无法读取或保存") from None
    except Exception:
        raise ToolError("执行失败；已隐藏原始异常，请在本机运行status查看计划状态") from None


def _ask_user(plan_id: str, ctx: Context) -> Elicit[EpisodeApproval]:
    """Runs before the tool body. The SDK sends the popup and fills in the user's answer."""
    from .confirmation import approval_message

    capabilities = ctx.client_capabilities
    popup = capabilities.elicitation if capabilities else None
    if popup is None or (popup.form is None and popup.url is not None):
        raise ToolError(NO_POPUP)
    with _safe_tool_errors():
        plan = _execution_service().plan_for_approval(plan_id)
    return Elicit(approval_message(plan), EpisodeApproval)


@mcp.tool()
def execute_episode_plan(
    plan_id: Annotated[str, Field(description="计划ID，来自preview_episode_watched")],
    answer: Annotated[ElicitationResult[EpisodeApproval], Resolve(_ask_user)],
) -> dict:
    """请用户确认并执行一份已创建的预览计划，把其中的章节改成计划里的目标状态。

    本工具会让客户端直接向用户弹窗显示具体修改，
    只有用户本人点击确认才会写入Bangumi；不能代替用户确认，转述用户的同意也不算确认。
    未确认或客户端不支持弹窗时不写入。popup_result为decline或cancel时，也可能是客户端
    没有显示弹窗就自动回答，不要说成用户拒绝，请询问用户是否看到了弹窗。写入后读回核对，
    返回status：verified已核实；failed未生效；unknown结果未知，不要重试，请用户在本机运行check核对；
    partial部分完成，items中每集的result为applied已改、not_applied没改成、changed_elsewhere被其他操作改动
    （不要覆盖），把具体集数告诉用户；用户要补上时重新创建预览。
    """
    if not (isinstance(answer, AcceptedElicitation) and answer.data.confirm):
        return {"plan_id": plan_id, "popup_result": answer.action, "write_attempted": False}
    with _safe_tool_errors():
        return {**_execution_service().approve_and_execute(plan_id), "popup_result": "accept",
                "write_attempted": True}


def _public_read(read, *args) -> dict:
    with _safe_tool_errors():
        try:
            return read(*args)
        except api_client.BangumiApiError as exc:
            raise ToolError(str(exc)) from None


def _progress_preview():
    from pathlib import Path
    from .progress import ProgressPreview
    from .storage import PlanStore

    root = Path(__file__).resolve().parents[2]
    return ProgressPreview(_account_client(), PlanStore(root / "state/progress.sqlite3"))


def _account_client():
    from pathlib import Path
    from .account_client import AccountClient
    from .settings import Settings

    root = Path(__file__).resolve().parents[2]
    return AccountClient(Settings.from_token_file(root / ".secrets/bangumi-token.txt"))


@mcp.tool()
def get_episodes(
    subject_id: SubjectId,
    limit: Annotated[int, Field(description="本次最多返回多少集", ge=1, le=40)] = 40,
    offset: Offset = 0,
    episode_type: EpisodeType = 0,
    include_summary: Annotated[bool, Field(description="是否返回单集简介；很占篇幅，用户问剧情时才打开")] = False,
) -> dict:
    """查询一个条目的章节列表，用来把“第几集”换成标记进度需要的章节ID。

    不含个人观看状态；要看本人看到哪一集用get_my_episode_states。
    返回total、next_offset和data；data每项含episode_id、episode_type（章节种类，取值同参数）、sort（总序号；SP可能是18.5这样的小数，OP、ED从1单独计）、ep（本季第几集，非本篇为0）、
    name、name_cn、airdate、duration。长篇作品用offset直接跳到附近，例如本篇第500集用offset=499附近。
    本工具只读，无需账户凭据。
    """
    return _public_read(api_client.list_episodes, subject_id, limit, offset, episode_type, include_summary)


@mcp.tool()
def get_calendar(
    weekday: Annotated[int | None, Field(description="1星期一～7星期日；不传表示今天（按本机日期）", ge=1, le=7)] = None,
) -> dict:
    """查询某一天正在放送的动画，用于“今天播什么”“周三有什么新番”。

    返回weekday、weekday_cn、total和data；data每项含subject_id、name、name_cn、air_date（开播日）、
    score、url。这是每周放送表，不是某一集的具体更新时间。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_calendar, weekday)


@mcp.tool()
def get_subject_characters(
    subject_id: SubjectId,
    limit: Annotated[int, Field(description="本次最多返回多少个角色", ge=1, le=50)] = 20,
    offset: Offset = 0,
) -> dict:
    """查询条目的角色和配音演员，用于“这部有哪些角色”“某角色是谁配的”。

    制作人员（导演、编剧等）用get_subject_persons。
    返回total、next_offset和data；data每项含character_id、name、relation（主角、配角等）、
    actors（person_id、name）。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_characters, subject_id, limit, offset)


@mcp.tool()
def get_subject_persons(
    subject_id: SubjectId,
    relation: Annotated[str | None, Field(description="职位关键词，按包含匹配，例如“导演”“脚本”“动画制作”")] = None,
    limit: Annotated[int, Field(description="本次最多返回多少人", ge=1, le=50)] = 30,
    offset: Offset = 0,
) -> dict:
    """查询条目的制作人员，用于“导演是谁”“谁写的脚本”“哪家公司做的”。

    配音演员用get_subject_characters。人员可能有几百条，能确定职位时请传relation筛选。
    返回total、next_offset和data；data每项含person_id、name、relation、eps（参与集数，可能为空）。
    本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_persons, subject_id, relation, limit, offset)


@mcp.tool()
def get_subject_relations(subject_id: SubjectId) -> dict:
    """查询与条目相关联的其他条目，用于“有没有续作”“前传是哪部”“原作是什么”。

    返回total和data；data每项含subject_id、name、name_cn、relation（续集、前传、书籍等）、
    type（1书籍、2动画、3音乐、4游戏、6三次元）、url。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_relations, subject_id)


@mcp.tool()
def get_my_collections(
    collection_type: Annotated[int, Field(description="收藏状态：1想看、2看过、3在看、4搁置、5抛弃", ge=1, le=5)] = 3,
    subject_type: Annotated[int, Field(description="条目类型：1书籍、2动画、3音乐、4游戏、6三次元")] = 2,
    limit: Annotated[int, Field(description="本次最多返回多少条", ge=1, le=50)] = 20,
    offset: Offset = 0,
    include_comment: Annotated[bool, Field(description="是否同时返回短评、评分、标签和私密标记；用户问到时才打开")] = False,
) -> dict:
    """查询当前Token本人的收藏列表，用于“我在看什么”“我想看什么”“我看过哪些”。用户询问我看到第几集类似问题应使用“get_my_episode_states”

    返回total、complete、next_offset和data；data每项含subject_id、name、name_cn、ep_status（一共看过的集数，会包括OP、ED等非正片，涉及到用户可能不按顺序看以及非正片标记的问题，所以本参数不能体现用户看到第几集）、
    vol_status、url。需要本机Token（不用用户在聊天里提供），只读，不修改收藏。
    """
    with _safe_tool_errors():
        return _account_client().get_my_collections(limit, offset, subject_type, collection_type,
                                                    include_comment)


@mcp.tool()
def get_my_collection(subject_id: SubjectId) -> dict:
    """查询当前Token本人对某一部作品的收藏记录，用于“这部我收藏了吗”“我给这部写过短评吗”“我给这部打了几分”“这部一共标了几集”。

    用户询问我看到第几集、下一集是哪集，应使用get_my_episode_states。
    返回subject_id、type（收藏状态：1想看、2看过、3在看、4搁置、5抛弃）、name、ep_status（一共看过的集数，会包括OP、ED等非正片，不能体现用户看到第几集）、
    vol_status、comment（短评）、rate（评分，0表示未评分）、tags、private（是否私密）、url。
    没有收藏时返回工具错误。需要本机Token（不用用户在聊天里提供），只读，不修改收藏。
    """
    with _safe_tool_errors():
        return _account_client().get_my_collection(subject_id)


@mcp.tool()
def get_my_episode_states(
    subject_id: SubjectId,
    limit: Annotated[int, Field(description="本次最多返回多少集", ge=1, le=50)] = 40,
    offset: Offset = 0,
    episode_type: EpisodeType = 0,
    include_summary: Annotated[bool, Field(description="是否返回单集简介；很占篇幅，用户问剧情时才打开")] = False,
) -> dict:
    """查询当前Token本人在某条目每一集的观看状态，用于“这部我看到第几集”“下一集是哪集”。

    条目必须已收藏，否则报错。
    返回total、complete、next_offset和data；data每项含episode_id、type（观看状态：0未收藏、1想看、2看过、3抛弃）、
    episode_type（章节种类：0本篇、1特别篇、2 OP、3 ED、4预告宣传、5 MAD、6其他）、sort、ep、name、name_cn。
    需要本机Token（不用用户在聊天里提供），只读，不修改收藏；要标记看过用preview_episode_watched。
    """
    with _safe_tool_errors():
        return _account_client().get_my_episode_states(subject_id, limit, offset, episode_type,
                                                       include_summary)


if __name__ == "__main__":
    mcp.run(transport="stdio")
