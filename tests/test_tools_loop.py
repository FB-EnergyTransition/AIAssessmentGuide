"""guideos.tools.loop and guideos.tools.service with provider and client
doubles: the model chooses tools, GuideOS checks, executes and feeds results
back until the model answers with text or a budget stops the loop."""

import json
from datetime import UTC, datetime

import pytest

from guideos.providers.base import ModelWithoutToolsError, ToolCallRequest, ToolTurn
from guideos.tools.guard import ToolGuard
from guideos.tools.loop import run_tool_loop
from guideos.tools.models import ToolError, ToolResult, ToolServer, ToolSpec, ToolCall
from guideos.tools.service import ToolService
from guideos.tools.store import LocalToolServerStore

PEAKS = ToolSpec(
    name="analyze_dataset", description="Spitzen. Voraussetzung: dataset_id.",
    server_name="demo",
    input_schema={"type": "object", "additionalProperties": False,
                  "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"months": {"type": "array"}}, "required": ["months"]},
)
DELETE = ToolSpec(
    name="delete_dataset", description="Löscht.", server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"},
                                                  "dataset_id": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
)
SERVER = ToolServer.create(domain_id="verein-demo", name="demo",
                           url="http://127.0.0.1:8600/mcp", token="token-0123456789abcdef")


class ScriptedProvider:
    """Answers chat_with_tools from a list of turns; records the messages and
    tools it was given."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def chat_with_tools(self, endpoint, model, messages, tools):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            return ToolTurn(text=turn, calls=[], message={"role": "assistant", "content": turn})
        calls = [ToolCallRequest(name=name, arguments=arguments) for name, arguments in turn]
        return ToolTurn(text="", calls=calls, message={
            "role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls],
        })


class FakeClient:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def call(self, server, name, arguments, timeout):
        self.calls.append((server.name, name, dict(arguments), timeout))
        if name in self.failures:
            raise self.failures[name]
        return ToolResult(
            call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != "owner"}),
            server_name=server.name, summary=f"{name} ok",
            structured_content={"months": [{"month": "2025-01", "peak_kw": "8"}],
                                "provenance": {"calculation_id": f"calc-{name}"}},
            calculation_id=f"calc-{name}", duration_ms=5,
        )

    def list_tools(self, server):
        return [PEAKS, DELETE]


def _messages():
    return [{"role": "system", "content": "Policy"}, {"role": "user", "content": "Wie hoch war meine Spitze?"}]


def test_loop_executes_allowed_calls_and_stops_at_the_text_answer():
    provider = ScriptedProvider([
        [("analyze_dataset", {"dataset_id": "ds_1"})],
        "Deine Spitze lag bei 8 kW (T1).",
    ])
    client = FakeClient()
    guard = ToolGuard([PEAKS], participant_id="teilnehmer-1", max_calls=6, max_seconds=90)

    outcome = run_tool_loop(provider, "http://ollama", "modell", _messages(), guard, client,
                            {"demo": SERVER})

    assert outcome.stop_reason == "text"
    assert outcome.final_text == "Deine Spitze lag bei 8 kW (T1)."
    assert [r.call.name for r in outcome.tool_results] == ["analyze_dataset"]
    assert outcome.tool_results[0].call.arguments == {"dataset_id": "ds_1"}
    assert client.calls == [("demo", "analyze_dataset",
                             {"dataset_id": "ds_1", "owner": "teilnehmer-1"}, 60.0)]
    # The model saw only the allowed tool, without the owner field.
    tools = provider.requests[0]["tools"]
    assert [t["function"]["name"] for t in tools] == ["analyze_dataset"]
    assert "owner" not in tools[0]["function"]["parameters"]["properties"]
    # Second request: transcript carries the assistant call and the tool message.
    second = provider.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_name"] == "analyze_dataset"
    assert json.loads(second[-1]["content"])["months"][0]["peak_kw"] == "8"
    assert outcome.transcript == second


def test_loop_feeds_guard_errors_back_and_stops_on_budget_or_unavailable_server():
    provider = ScriptedProvider([
        [("delete_dataset", {"dataset_id": "ds_1"}),
         ("analyze_dataset", {"dataset_id": "ds_1", "owner": "fremd"})],
        [("analyze_dataset", {"dataset_id": "ds_1"})],
        [("analyze_dataset", {"dataset_id": "ds_1"})],
        "nie erreicht",
    ])
    client = FakeClient()
    guard = ToolGuard([PEAKS], participant_id="p", max_calls=3, max_seconds=90)

    outcome = run_tool_loop(provider, "e", "m", _messages(), guard, client, {"demo": SERVER})

    assert outcome.stop_reason == "budget"
    assert outcome.final_text == ""
    assert client.calls[0][1] == "analyze_dataset" and len(client.calls) == 1
    first_reply = provider.requests[1]["messages"]
    tool_messages = [m for m in first_reply if m["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"])["error"] == "tool_not_allowed"
    assert json.loads(tool_messages[1]["content"])["error"] == "tool_arguments_invalid"
    assert [r.call.name for r in outcome.tool_results] == ["analyze_dataset"]
    assert outcome.error is not None and outcome.error.code == "tool_budget_exceeded"

    unavailable = FakeClient(failures={
        "analyze_dataset": ToolError("tool_server_unavailable", "Server weg.")})
    outcome = run_tool_loop(
        ScriptedProvider([[("analyze_dataset", {"dataset_id": "ds_1"})], "x"]),
        "e", "m", _messages(), ToolGuard([PEAKS], participant_id="p"), unavailable,
        {"demo": SERVER},
    )
    assert outcome.stop_reason == "error" and outcome.error.code == "tool_server_unavailable"
    assert outcome.tool_results == []


def test_loop_reports_a_model_without_tools_and_an_empty_toolset():
    provider = ScriptedProvider([ModelWithoutToolsError("Modell kann keine Werkzeuge.")])
    outcome = run_tool_loop(provider, "e", "m", _messages(), ToolGuard([PEAKS], participant_id="p"),
                            FakeClient(), {"demo": SERVER})
    assert outcome.stop_reason == "model_without_tools" and outcome.tool_results == []

    outcome = run_tool_loop(ScriptedProvider(["sollte nicht gefragt werden"]), "e", "m", _messages(),
                            ToolGuard([], participant_id="p"), FakeClient(), {})
    assert outcome.stop_reason == "no_tools"


def test_service_registers_refreshes_and_filters_specs(tmp_path):
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    client = FakeClient()
    service = ToolService(store, client, clock=lambda: datetime(2026, 10, 19, 12, tzinfo=UTC))

    server = service.register("verein-demo", "demo", "http://127.0.0.1:8600/mcp",
                              "token-0123456789abcdef")
    assert server.tool_specs == [] and service.specs_for("verein-demo") == []

    refreshed = service.refresh_tools("verein-demo", "demo")
    assert [s.name for s in refreshed.tool_specs] == ["analyze_dataset", "delete_dataset"]
    assert refreshed.tools_fetched_at == datetime(2026, 10, 19, 12, tzinfo=UTC)
    assert service.specs_for("verein-demo") == []  # nothing allowed yet

    allowed = service.set_allowed_tools("verein-demo", "demo", ["analyze_dataset"])
    assert allowed.allowed_tools == ["analyze_dataset"]
    assert [s.name for s in service.specs_for("verein-demo")] == ["analyze_dataset"]
    with pytest.raises(ToolError) as excinfo:
        service.set_allowed_tools("verein-demo", "demo", ["gibt_es_nicht"])
    assert excinfo.value.code == "tool_not_allowed"

    # Re-registering keeps the allowed tools and the cache, disabling hides the specs.
    again = service.register("verein-demo", "demo", "http://127.0.0.1:8601/mcp",
                             "token-0123456789abcdef", enabled=False)
    assert again.url == "http://127.0.0.1:8601/mcp" and again.allowed_tools == ["analyze_dataset"]
    assert service.specs_for("verein-demo") == []
    service.register("verein-demo", "demo", "http://127.0.0.1:8601/mcp",
                     "token-0123456789abcdef", enabled=True)
    assert service.servers_by_name("verein-demo")["demo"].url.endswith("8601/mcp")

    # A GuideOS-driven call injects the owner and respects the allowlist.
    result = service.call("verein-demo", "teilnehmer-1", "analyze_dataset",
                          {"dataset_id": "ds_1"})
    assert result.calculation_id == "calc-analyze_dataset"
    assert client.calls[-1][2]["owner"] == "teilnehmer-1"
    with pytest.raises(ToolError) as excinfo:
        service.call("verein-demo", "teilnehmer-1", "delete_dataset", {"dataset_id": "ds_1"})
    assert excinfo.value.code == "tool_not_allowed"

    outcome = service.run_loop(
        "verein-demo", "teilnehmer-1",
        ScriptedProvider([[("analyze_dataset", {"dataset_id": "ds_1"})], "Antwort"]),
        "e", "m", _messages(), max_calls=2, max_seconds=30,
    )
    assert outcome.stop_reason == "text" and len(outcome.tool_results) == 1
    assert service.remove("verein-demo", "demo") is True
    assert service.list_servers("verein-demo") == []
