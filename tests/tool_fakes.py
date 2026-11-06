"""Shared test doubles for the MCP tool layer: tool specs of a small demo
server (import, delete, analyse, series), a scripted tool client and a
scripted advisory provider. Names and outputs are deliberately generic."""

from guideos.context.prompt import AdvisoryAnswer
from guideos.providers.base import ToolCallRequest, ToolTurn
from guideos.tools.models import ToolCall, ToolResult, ToolSpec

SERVER_NAME = "demo"

ANALYZE_SPEC = ToolSpec(
    name="analyze_dataset", description="Kennzahlen je Monat. Voraussetzung: dataset_id.",
    server_name=SERVER_NAME,
    input_schema={"type": "object", "additionalProperties": False,
                  "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                 "period_from": {"anyOf": [{"type": "string", "format": "date"},
                                                           {"type": "null"}], "default": None},
                                 "threshold": {"anyOf": [{"type": "number"}, {"type": "null"}],
                                               "default": None}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"months": {"type": "array"}}, "required": ["months"]},
)
IMPORT_SPEC = ToolSpec(
    name="import_dataset", description="Importiert eine CSV.", server_name=SERVER_NAME,
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "filename": {"type": "string"},
                                                  "content": {"type": "string"},
                                                  "format_hint": {"type": "string", "enum": ["auto", "csv"]}},
                  "required": ["owner", "filename", "content"]},
    output_schema={"type": "object", "properties": {"dataset_id": {"type": "string"}},
                   "required": ["dataset_id"]},
)
DELETE_SPEC = ToolSpec(
    name="delete_dataset", description="Löscht.", server_name=SERVER_NAME,
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"deleted": {"type": "boolean"}}, "required": ["deleted"]},
)
SERIES_SPEC = ToolSpec(
    name="get_series", description="Zeitreihen. Voraussetzung: dataset_id.", server_name=SERVER_NAME,
    input_schema={"type": "object", "additionalProperties": False,
                  "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                 "resolution": {"type": "string", "enum": ["day", "quarter_hour"], "default": "day"},
                                 "period_from": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "default": None},
                                 "period_to": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}], "default": None}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"points": {"type": "array"}, "unit": {"type": "string"},
                                                   "resolution": {"type": "string"}}, "required": ["points"]},
)
ANALYZE_OUTPUT = {"dataset_id": "ds_1", "months": [{"month": "2025-01", "peak": "8", "complete": True}],
                  "provenance": {"calculation_id": "calc-analyze-1", "core_version": "0.1.0",
                                 "assumptions": [], "limitations": []}}
ANALYZE_SUMMARY = "Monatswerte: 2025-01: Höchstwert 8,00 am 15.01.2025 18:30 (vollständig)"
IMPORT_OUTPUT = {"dataset_id": "ds_1", "content_hash": "c" * 64, "period_from": "2025-01-01",
                 "period_to": "2025-01-31", "interval_count": 2976, "source_format": "csv",
                 "quality": {"usable": True, "complete_months": ["2025-01"]}}
IMPORT_SUMMARY = "Messdaten importiert: 2976 Werte von 01.01.2025 bis 31.01.2025."


class FakeToolClient:
    def __init__(self):
        self.calls = []
        self.fail_with = None

    def call(self, server, name, arguments, timeout):
        self.calls.append((name, dict(arguments)))
        if self.fail_with is not None:
            raise self.fail_with
        if name == ANALYZE_SPEC.name:
            output, summary = ANALYZE_OUTPUT, ANALYZE_SUMMARY
        elif name == IMPORT_SPEC.name:
            output = {**IMPORT_OUTPUT, "dataset_id": f"ds_{len(self.calls)}"}
            summary = IMPORT_SUMMARY
        else:
            output, summary = {"deleted": True}, "Datensatz gelöscht."
        return ToolResult(call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != "owner"}),
                          server_name=server.name, structured_content=output, summary=summary,
                          calculation_id=(output.get("provenance") or {}).get("calculation_id"), duration_ms=3)

    def list_tools(self, server):
        return [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC]


class SeriesToolClient(FakeToolClient):
    """Adds the read-only series tool for the REST series endpoint."""

    def call(self, server, name, arguments, timeout):
        if name != SERIES_SPEC.name:
            return super().call(server, name, arguments, timeout)
        self.calls.append((name, dict(arguments)))
        output = {"dataset_id": arguments["dataset_id"], "direction": "import",
                  "resolution": arguments.get("resolution", "day"),
                  "unit": "Stück/h" if arguments.get("resolution") == "quarter_hour" else "Stück",
                  "period_from": "2025-01-01", "period_to": "2025-01-02",
                  "points": [{"start": "2025-01-01T00:00:00+01:00", "value": "12.5", "complete": True},
                             {"start": "2025-01-02T00:00:00+01:00", "value": "11.0", "complete": False}],
                  "provenance": {"calculation_id": "calc-series-1", "core_version": "0.1.0",
                                 "assumptions": [], "limitations": []}}
        return ToolResult(call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != "owner"}),
                          server_name=server.name, structured_content=output, summary="Zeitreihe mit 2 Punkten.",
                          calculation_id="calc-series-1", duration_ms=2)

    def list_tools(self, server):
        return [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, SERIES_SPEC]


class FakeToolProvider:
    """Advisory model double: the tool turns are scripted, the structured
    answer cites what the test asks for."""

    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, turns=None, source_ids=("T1",), text="Dein höchster Monatswert lag bei 8."):
        self.turns = list(turns or [])
        self.source_ids = list(source_ids)
        self.text = text
        self.tool_requests = []
        self.structured_requests = []

    def stream_chat(self, endpoint, model, messages):
        raise AssertionError("nicht erwartet")

    def generate_structured(self, endpoint, model, messages, schema):
        if schema.get("title") == "ProfileChangeProposal":
            return {"field_key": None, "value": None, "feedback_recommendation_id": None,
                    "feedback_stance": None}
        assert schema == AdvisoryAnswer.model_json_schema()
        self.structured_requests.append(messages)
        return {"sections": [{"text": self.text, "source_ids": self.source_ids}]}

    def chat_with_tools(self, endpoint, model, messages, tools):
        self.tool_requests.append({"messages": messages, "tools": tools})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            return ToolTurn(text=turn, calls=[], message={"role": "assistant", "content": turn})
        calls = [ToolCallRequest(name=n, arguments=a) for n, a in turn]
        return ToolTurn(text="", calls=calls, message={"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in calls]})
