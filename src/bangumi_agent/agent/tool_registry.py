"""The agent's tools: the MCP tools from mcp_server.py, called in-process (no MCP client or subprocess).

Only names in AGENT_TOOLS are offered. execute_episode_plan is never one of them: the model can
create previews, and only the user's own confirmation in the interface can execute them.
"""
import asyncio
import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from mcp.server.mcpserver.exceptions import UnexpectedToolError
from mcp.types import CallToolResult, TextContent

from .messages import ToolError, ToolSpec

# Listed one by one so a new MCP tool is not handed to the model by accident.
AGENT_TOOLS = (
    "search_subjects", "get_subject", "get_episodes", "get_calendar",
    "get_subject_characters", "get_subject_persons", "get_subject_relations",
    "get_my_collections", "get_my_collection", "get_my_episode_states",
    "preview_episode_watched", "preview_watched_up_to", "preview_episode_change",
)


class McpTools:
    """Tools for run_task. Arguments are validated by the MCP tool's own schema."""

    def __init__(self, server: MCPServer | None = None, allowed: tuple[str, ...] = AGENT_TOOLS):
        if server is None:
            from ..mcp_server import mcp
            server = mcp
        self._server = server
        self._allowed = allowed
        self._specs: list[ToolSpec] | None = None

    def specs(self) -> list[ToolSpec]:
        if self._specs is None:
            listed = {tool.name: tool for tool in asyncio.run(self._server.list_tools())}
            missing = [name for name in self._allowed if name not in listed]
            if missing:
                raise ValueError(f"MCP 服务里没有这些工具：{missing}")
            self._specs = [ToolSpec(name, listed[name].description or "", dict(listed[name].input_schema))
                           for name in self._allowed]
        return self._specs

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self._allowed:
            raise ToolError(f"不允许调用工具 {name}")
        try:
            result = asyncio.run(self._server.call_tool(name, arguments))
        except UnexpectedToolError:
            # Subclass of McpToolError, and its text may carry the original exception: hide it.
            raise RuntimeError("tool crashed") from None
        except McpToolError as exc:
            raise ToolError(str(exc)) from None  # validation errors and the tools' own safe messages
        if not isinstance(result, CallToolResult):
            raise ToolError("这个工具需要用户在界面上补充输入，当前不能由模型调用")
        text = "\n".join(part.text for part in result.content if isinstance(part, TextContent))
        if result.is_error:
            raise ToolError(text or "工具返回错误")
        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content, ensure_ascii=False)
        return text
