"""Internal message format (docs/prior-agent-notes.md, "新项目的决定" 1).

The loop only sees these types. Each provider format gets its own converter that turns
a list of Message into a request and a response into a ModelReply.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolCallBlock:
    id: str
    name: str
    # None: the converter could not parse the arguments (cut off or bad JSON). Never run it.
    arguments: dict[str, Any] | None


@dataclass(frozen=True)
class ToolResultBlock:
    call_id: str  # the ToolCallBlock.id this result answers
    content: str
    is_error: bool = False


Block = TextBlock | ToolCallBlock | ToolResultBlock


@dataclass
class Message:
    # "tool" holds every result for one assistant message, in the order of its calls.
    role: Literal["user", "assistant", "tool"]
    content: list[Block]


# end: the model finished; tool_calls: it wants tools run; max_tokens: output was cut off;
# other: anything the converter does not recognise.
StopReason = Literal["end", "tool_calls", "max_tokens", "other"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelReply:
    message: Message  # role "assistant"
    stop: StopReason
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolError(Exception):
    """A failure the tool expected. Its message is safe to show the model: no token, no traceback."""


class ChatModel(Protocol):
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        """Send one request. allow_tools=False asks for a plain answer; the tools still describe the history."""
        ...


class Tools(Protocol):
    def specs(self) -> list[ToolSpec]:
        """The tools the model may call. Anything not listed here must not run."""
        ...

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """Validate arguments and run one tool. Raises ToolError for expected failures."""
        ...


def text_of(message: Message) -> str:
    """All text blocks of a message joined together."""
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


def tool_calls_of(message: Message) -> list[ToolCallBlock]:
    return [block for block in message.content if isinstance(block, ToolCallBlock)]
