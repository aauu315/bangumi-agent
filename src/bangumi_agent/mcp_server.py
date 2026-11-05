"""Public and account reads, a local preview, and one write that only the user's popup answer can start."""

from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from bangumi_agent import api_client

mcp = MCPServer("Bangumi Agent")


@mcp.tool()
def search_subjects(keyword: str, limit: int = 10, offset: int = 0) -> dict:
    """按关键词搜索公开的 Bangumi 条目，返回一页候选。
    
        适用于用户提供作品名称或关键词、尚不知道条目 ID 的情况。
        keyword：搜索关键词，不能是空白。
        limit：本次最多返回多少条，范围 1～20，默认 10。
        offset：跳过的记录数，必须非负，默认 0；不是页码。
    
        返回 total、limit、offset 和 data。
        data 中每项包含 id、name、name_cn、type、url。
        搜索可能返回同名作品及不同类型条目，应结合名称和类型
        确认目标；无法确定时询问用户，再用 get_subject 查询详情。
        本工具只读，无需账户凭据，不修改收藏。
    """
    try:
        return api_client.search_subjects(keyword, limit, offset)
    except (ValueError, api_client.BangumiApiError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def get_subject(subject_id: int) -> dict:
    """按 Bangumi 条目 ID 查询公开详情。
    
        subject_id：正整数条目 ID，应来自搜索结果或明确的条目链接，
        不能使用章节 ID、集数或搜索结果的数组下标代替。
    
        返回名称、类型、简介、日期、集数、评分、图片和条目链接等字段；
        部分字段可能为空。
        eps 和 total_episodes 是作品信息，不是本人的观看进度。
        本工具只读，无需账户凭据，不查询或修改个人收藏。
        条目不存在或请求失败时返回工具错误，不能当作查询成功。
    """
    try:
        return api_client.get_subject(subject_id)
    except (ValueError, api_client.BangumiApiError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def preview_episode_watched(subject_id: int, episode_id: int) -> dict:
    """只读核对本人收藏及章节归属，创建单章节准备标记看过的本地草稿。

    subject_id、episode_id必须是正整数网站ID；episode_id不是第几集。
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
    plan_id: str,
    answer: Annotated[ElicitationResult[EpisodeApproval], Resolve(_ask_user)],
) -> dict:
    """请用户确认并执行一份已创建的预览计划，把该章节标记为看过。

    plan_id来自preview_episode_watched。本工具会让客户端直接向用户弹窗显示具体修改，
    只有用户本人点击确认才会写入Bangumi；不能代替用户确认，转述用户的同意也不算确认。
    未确认或客户端不支持弹窗时不写入。popup_result为decline或cancel时，也可能是客户端
    没有显示弹窗就自动回答，不要说成用户拒绝，请询问用户是否看到了弹窗。写入后读回核对，
    返回status：verified已核实；failed未生效；unknown结果未知，不要重试，请用户在本机运行check核对。
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


def _account_client():
    from pathlib import Path
    from .account_client import AccountClient
    from .settings import Settings

    root = Path(__file__).resolve().parents[2]
    return AccountClient(Settings.from_token_file(root / ".secrets/bangumi-token.txt"))


@mcp.tool()
def get_episodes(subject_id: int, limit: int = 40, offset: int = 0, episode_type: int | None = 0,
                 include_summary: bool = False) -> dict:
    """查询一个条目的章节列表，用来把“第几集”换成标记进度需要的章节ID。

    不含个人观看状态；要看本人看到哪一集用get_my_episode_states。
    subject_id：条目ID，不是章节ID。limit：1～40，默认40。offset：跳过的记录数，不是页码。
    episode_type：0本篇（默认）、1特别篇、2 OP、3 ED、4预告宣传、5 MAD、6其他；None表示全部。
    include_summary：是否返回单集简介，默认否，用户问剧情时才打开，因为简介很占篇幅。
    返回total、next_offset和data；data每项含episode_id、sort（总序号）、ep（本季第几集）、
    name、name_cn、airdate、duration。长篇作品用offset直接跳到附近，例如本篇第500集用offset=499附近。
    本工具只读，无需账户凭据。
    """
    return _public_read(api_client.list_episodes, subject_id, limit, offset, episode_type, include_summary)


@mcp.tool()
def get_calendar(weekday: int | None = None) -> dict:
    """查询某一天正在放送的动画，用于“今天播什么”“周三有什么新番”。

    weekday：1星期一～7星期日；不传表示今天（按本机日期）。
    返回weekday、weekday_cn、total和data；data每项含subject_id、name、name_cn、air_date（开播日）、
    score、url。这是每周放送表，不是某一集的具体更新时间。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_calendar, weekday)


@mcp.tool()
def get_subject_characters(subject_id: int, limit: int = 20, offset: int = 0) -> dict:
    """查询条目的角色和配音演员，用于“这部有哪些角色”“某角色是谁配的”。

    制作人员（导演、编剧等）用get_subject_persons。limit：1～50，默认20。offset：跳过的记录数。
    返回total、next_offset和data；data每项含character_id、name、relation（主角、配角等）、
    actors（person_id、name）。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_characters, subject_id, limit, offset)


@mcp.tool()
def get_subject_persons(subject_id: int, relation: str | None = None, limit: int = 30,
                        offset: int = 0) -> dict:
    """查询条目的制作人员，用于“导演是谁”“谁写的脚本”“哪家公司做的”。

    配音演员用get_subject_characters。人员可能有几百条，能确定职位时请传relation筛选。
    relation：职位关键词，按包含匹配，例如“导演”“脚本”“动画制作”。limit：1～50，默认30。
    返回total、next_offset和data；data每项含person_id、name、relation、eps（参与集数，可能为空）。
    本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_persons, subject_id, relation, limit, offset)


@mcp.tool()
def get_subject_relations(subject_id: int) -> dict:
    """查询与条目相关联的其他条目，用于“有没有续作”“前传是哪部”“原作是什么”。

    返回total和data；data每项含subject_id、name、name_cn、relation（续集、前传、书籍等）、
    type（1书籍、2动画、3音乐、4游戏、6三次元）、url。本工具只读，无需账户凭据。
    """
    return _public_read(api_client.get_subject_relations, subject_id)


@mcp.tool()
def get_my_collections(collection_type: int = 3, subject_type: int = 2, limit: int = 20,
                       offset: int = 0, include_comment: bool = False) -> dict:
    """查询当前Token本人的收藏列表，用于“我在看什么”“我想看什么”“我看过哪些”。

    collection_type：1想看、2看过、3在看（默认）、4搁置、5抛弃。
    subject_type：1书籍、2动画（默认）、3音乐、4游戏、6三次元。limit：1～50。offset：跳过的记录数。
    include_comment：是否同时返回短评、评分、标签和私密标记，默认否，用户问到时才打开。
    返回total、complete、next_offset和data；data每项含subject_id、name、name_cn、ep_status（看到第几集）、
    vol_status、url。需要本机Token（不用用户在聊天里提供），只读，不修改收藏。
    """
    with _safe_tool_errors():
        return _account_client().get_my_collections(limit, offset, subject_type, collection_type,
                                                    include_comment)


@mcp.tool()
def get_my_episode_states(subject_id: int, limit: int = 40, offset: int = 0,
                          episode_type: int | None = 0, include_summary: bool = False) -> dict:
    """查询当前Token本人在某条目每一集的观看状态，用于“这部我看到第几集”“下一集是哪集”。

    type：0未收藏、1想看、2看过、3抛弃。条目必须已收藏，否则报错。
    limit：1～50，默认40。offset：跳过的记录数。episode_type：0本篇（默认），None表示全部。
    include_summary：是否返回单集简介，默认否。
    返回total、complete、next_offset和data；data每项含episode_id、type、sort、ep、name、name_cn。
    需要本机Token（不用用户在聊天里提供），只读，不修改收藏；要标记看过用preview_episode_watched。
    """
    with _safe_tool_errors():
        return _account_client().get_my_episode_states(subject_id, limit, offset, episode_type,
                                                       include_summary)


if __name__ == "__main__":
    mcp.run(transport="stdio")
