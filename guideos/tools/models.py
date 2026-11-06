import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

SERVER_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_SERVER_NAME = re.compile(SERVER_NAME_PATTERN)
OWNER_FIELD = "owner"
# Long identifiers (meter, customer or contract numbers, optionally with a
# two-letter prefix such as a country code) appear in export file names.
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2})?\d{12,}(?![0-9])")


def mask_identifiers(filename: str) -> str:
    """A file name without identifiers GuideOS must not keep: runs of twelve
    or more digits, with an optional two-letter prefix, become "<prefix>…"
    (dates and short numbers stay)."""
    return _IDENTIFIER.sub(lambda match: (match.group(1) or "") + "…", filename)

TOOL_ERROR_CODES = (
    "tool_server_unavailable", "tool_timeout", "tool_error", "tool_output_invalid",
    "tool_not_allowed", "tool_arguments_invalid", "tool_budget_exceeded", "model_without_tools",
)


class ToolsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSpec(ToolsModel):
    """A tool a server offers, as cached from list_tools."""

    name: str
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    server_name: str

    def parameters_for_model(self) -> dict[str, Any]:
        """The input schema without the owner field, which GuideOS fills."""
        schema = dict(self.input_schema)
        properties = {key: value for key, value in dict(schema.get("properties") or {}).items()
                      if key != OWNER_FIELD}
        schema["properties"] = properties
        if "required" in schema:
            schema["required"] = [name for name in schema["required"] if name != OWNER_FIELD]
        return schema

    def for_model(self) -> dict[str, Any]:
        """The definition in the function format Ollama and OpenAI use."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_for_model(),
            },
        }


class ToolServer(ToolsModel):
    """A registered MCP server of a domain, independent of pack versions."""

    server_id: str
    domain_id: str
    name: str = Field(pattern=SERVER_NAME_PATTERN)
    url: str
    token: str = ""
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    tool_specs: list[ToolSpec] = Field(default_factory=list)
    tools_fetched_at: datetime | None = None
    server_version: str | None = None
    updated_at: datetime

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("Die Server-URL muss mit http:// oder https:// beginnen.")
        return value

    @classmethod
    def create(cls, domain_id: str, name: str, url: str, token: str = "",
               enabled: bool = True, now: datetime | None = None) -> "ToolServer":
        if not _SERVER_NAME.fullmatch(name):
            raise ValueError("Der Servername darf nur Kleinbuchstaben, Ziffern, - und _ enthalten.")
        return cls(server_id=f"srv_{uuid4().hex}", domain_id=domain_id, name=name, url=url,
                   token=token, enabled=enabled, updated_at=now or datetime.now(UTC))

    def allowed_specs(self) -> list[ToolSpec]:
        allowed = set(self.allowed_tools)
        return [spec for spec in self.tool_specs if spec.name in allowed]


class ToolCall(ToolsModel):
    """A call as the model requested it, without the owner."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(ToolsModel):
    """A successful call: the structured output, the server's summary text and
    the calculation_id from its provenance block, if any."""

    call: ToolCall
    server_name: str
    structured_content: dict[str, Any] | None = None
    summary: str = ""
    calculation_id: str | None = None
    duration_ms: int = 0


class DatasetRef(ToolsModel):
    """The participant's dataset on a tool server, as GuideOS remembers it on
    the profile: ids and the import summary, never the data itself."""

    dataset_id: str
    content_hash: str = ""
    filename: str
    imported_at: datetime
    server_name: str
    tool_name: str
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    # The file question the dataset answers, None for the panel after completion.
    field_key: str | None = None


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if code not in TOOL_ERROR_CODES:
            raise ValueError(f"Unbekannter Fehlercode {code!r}.")
        self.code = code
        self.message = message
        self.details = details or {}
