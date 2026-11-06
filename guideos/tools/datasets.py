"""GuideOS's own dataset calls on the domain's registered tool server:
import a file a participant uploaded (file question of an assessment or
the "Messdaten" panel after completion) and delete a dataset again. The
model is never involved; only the reference the server returns is kept."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from collections.abc import Mapping
from typing import Any

from guideos.tools.models import DatasetRef, ToolError, ToolResult, mask_identifiers
from guideos.tools.service import ToolService

logger = logging.getLogger(__name__)

DEFAULT_OWNER = "local"
IMPORT_TIMEOUT = 180.0
# Tool errors that mean the server could not be used at all, as opposed to
# a professional finding about the file.
UNAVAILABLE_CODES = frozenset({"tool_server_unavailable", "tool_timeout"})


def owner_for(participant_id: str | None) -> str:
    """The owner GuideOS sets on every dataset call: the participant id, or
    the local process without pilot login."""
    return participant_id or DEFAULT_OWNER


def decode_text(data: bytes) -> str:
    """UTF-8 (BOM removed) or, when that fails, ISO-8859-1 as used by the
    older spreadsheet exports; never a lossy decode."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("iso-8859-1")


def format_options(tools, pack, question=None) -> list[str]:
    """The format hints the import tool accepts, "auto" first: from the
    input schema of the pack's dataset tool or the tool of a file question
    (the registered server's `format_hint` enum). ["auto"] without tools,
    without the pack block or when the registration is unreadable."""
    options = ["auto"]
    if tools is None or pack.tools is None:
        return options
    try:
        specs = tools.specs_for(pack.domain_id)
    except Exception:  # noqa: BLE001 - the store error surfaces when the import runs
        return options
    tool_name = pack.tools.dataset_tool
    if question is not None and question.upload is not None and question.upload.tool:
        tool_name = question.upload.tool
    spec = next((item for item in specs if item.name == tool_name), None)
    if spec is None:
        return options
    enum = (spec.input_schema.get("properties") or {}).get("format_hint", {}).get("enum")
    if isinstance(enum, list) and enum:
        return [str(item) for item in enum]
    return options


def basename(filename: str) -> str:
    """File name without path and without identifiers GuideOS must not keep."""
    name = PureWindowsPath(PurePosixPath(filename.strip()).name).name
    return mask_identifiers(name) or "messdaten.csv"


class DatasetImporter:
    def __init__(self, tools: ToolService, timeout: float = IMPORT_TIMEOUT) -> None:
        self.tools = tools
        self.timeout = timeout

    def import_text(
        self,
        domain_id: str,
        participant_id: str | None,
        tool: str,
        filename: str,
        content: str,
        format_hint: str = "auto",
        arguments: dict[str, Any] | None = None,
        field_key: str | None = None,
    ) -> tuple[DatasetRef, ToolResult]:
        """Call the import tool with the decoded file text and return the
        reference GuideOS keeps. Raises ToolError (the server's professional
        message, or tool_output_invalid without a dataset id)."""
        safe_name = basename(filename)
        result = self.tools.call(
            domain_id, owner_for(participant_id), tool,
            {"filename": safe_name, "content": content, "format_hint": format_hint, **(arguments or {})},
            timeout=self.timeout,
        )
        structured = result.structured_content or {}
        dataset_id = structured.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ToolError("tool_output_invalid", "Der Berechnungsdienst hat keine Datensatzkennung geliefert.")
        reference = DatasetRef(
            dataset_id=dataset_id,
            content_hash=str(structured.get("content_hash") or ""),
            filename=result.call.arguments.get("filename", safe_name),
            imported_at=datetime.now(UTC),
            server_name=result.server_name,
            tool_name=tool,
            summary=result.summary,
            details={key: value for key, value in structured.items() if key != "provenance"},
            field_key=field_key,
        )
        return reference, result

    def analyze(
        self, domain_id: str, participant_id: str | None, tool: str, dataset_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """One step of the pack's first analysis of a dataset GuideOS just
        imported. Raises ToolError; the caller records the failure."""
        return self.tools.call(
            domain_id, owner_for(participant_id), tool, {"dataset_id": dataset_id, **(arguments or {})},
            timeout=self.timeout,
        )

    def delete(self, domain_id: str, participant_id: str | None, tool: str, dataset_id: str) -> bool:
        """Best effort: a failure is logged with the dataset id so that it can
        be deleted later; the caller goes on."""
        try:
            self.tools.call(domain_id, owner_for(participant_id), tool, {"dataset_id": dataset_id})
        except ToolError as exc:
            logger.warning("dataset_delete_failed domain_id=%s dataset_id=%s code=%s",
                           domain_id, dataset_id, exc.code)
            return False
        logger.info("dataset_deleted domain_id=%s dataset_id=%s", domain_id, dataset_id)
        return True


def step_arguments(step, answers: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments of an analysis step: the fixed ones plus those taken
    from the answers (field_key -> stored value) through the step's map, or
    its default; an argument without usable answer and without default is
    left out, so the tool applies its own default."""
    arguments = dict(step.arguments)
    for name, spec in step.arguments_from_answers.items():
        value = answers.get(spec.field_key)
        resolved = None
        if value is not None:
            key = str(value).lower() if isinstance(value, bool) else str(value)
            resolved = spec.map.get(key) if spec.map is not None else value
        if resolved is None and spec.has_default():
            resolved = spec.default
        if resolved is not None:
            arguments[name] = resolved
    return arguments
