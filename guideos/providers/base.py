from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from guideos.chat import Message


class ProviderError(Exception):
    """A failed provider request; `code` names machine-readable causes."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ModelWithoutToolsError(ProviderError):
    """The selected model cannot call tools (Ollama: "does not support tools")."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="model_without_tools")


@dataclass
class ToolCallRequest:
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurn:
    """One answer of a tool-capable model: text, or tool calls the application
    executes; `message` is the raw assistant message to append to the
    transcript before the tool results."""

    text: str
    calls: list[ToolCallRequest] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    display_name: str
    default_endpoint: str
    default_model: str

    def stream_chat(
        self,
        endpoint: str,
        model: str,
        messages: list[Message],
    ) -> Iterator[str]:
        ...

    def generate_structured(
        self,
        endpoint: str,
        model: str,
        messages: list[Message],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def list_models(self, endpoint: str) -> list[str]:
        """Names of the models the endpoint currently serves."""
        ...

    def chat_with_tools(
        self,
        endpoint: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ToolTurn:
        """One non-streaming turn at temperature 0 with tool definitions in the
        function format; raises ModelWithoutToolsError for models that cannot
        call tools."""
        ...

    def embed(self, endpoint: str, model: str, texts: list[str]) -> list[list[float]]:
        """One embedding vector per text, in input order."""
        ...

    def rerank(self, endpoint: str, model: str, query: str, texts: list[str]) -> list[float]:
        """One relevance score per text for the query, in input order;
        higher is more relevant."""
        ...
