"""Public queries, a local preview, and one write that only the user's popup answer can start."""

from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

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


class EpisodeApproval(BaseModel):
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
    用户拒绝、取消或客户端不支持弹窗时不写入。写入后读回核对，返回status：
    verified已核实；failed未生效；unknown结果未知，不要重试，请用户在本机运行check核对。
    """
    if not (isinstance(answer, AcceptedElicitation) and answer.data.confirm):
        return {"plan_id": plan_id, "user_decision": answer.action, "write_attempted": False}
    with _safe_tool_errors():
        return {**_execution_service().approve_and_execute(plan_id), "user_decision": "accept",
                "write_attempted": True}


if __name__ == "__main__":
    mcp.run(transport="stdio")
