"""REST: measurement data for a profile (upload, read, delete), the first
analysis and completion steps of a pack with tools, answers derived from the
data and tool results in advisory answers, with the tool server and the model
as doubles. The pack is the inline tools pack of tests/pack_fixtures.py,
installed as the active version of its domain."""

import json

from fastapi.testclient import TestClient

import guideos.adapters.fastapi as fastapi_adapter
from guideos.adapters.fastapi import create_app
from guideos.tools.models import ToolCall, ToolResult, ToolServer, ToolSpec
from guideos.tools.service import ToolService
from guideos.tools.store import LocalToolServerStore
from tests.pack_fixtures import TOOLS_DOMAIN, TOOLS_PACK_ANSWERS, install_tools_pack, tools_pack_data
from tests.tool_fakes import (
    ANALYZE_SPEC,
    DELETE_SPEC,
    IMPORT_SPEC,
    SERIES_SPEC,
    FakeToolClient,
    FakeToolProvider,
    SeriesToolClient,
)

DOMAIN = TOOLS_DOMAIN
CSV = "datum;betrag\n2025-01-02;12,50\n"
UPLOAD = {"filename": "Kassabuch-2025.csv", "content": CSV, "format_hint": "auto"}

# The analysis tools of the tools pack's first analysis (after_import) and
# its completion step; analyze_windows stays unregistered on purpose.
TIERS_SPEC = ToolSpec(
    name="analyze_tiers", description="Stufen je Monat. Voraussetzung: dataset_id.", server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                                  "threshold": {"type": "number", "default": 10},
                                                  "minimum": {"type": "number", "default": 2}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"totals": {"type": "object"}}, "required": ["totals"]},
)
CHARACTERISTICS_SPEC = ToolSpec(
    name="analyze_characteristics", description="Merkmale. Voraussetzung: dataset_id.", server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"characteristics": {"type": "object"}},
                   "required": ["characteristics"]},
)
COSTS_SPEC = ToolSpec(
    name="calculate_costs", description="Kosten. Voraussetzung: dataset_id.", server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                                  "comparison": {"type": "string"}, "model": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"totals": {"type": "object"}}, "required": ["totals"]},
)
TIERS_OUTPUT = {"dataset_id": "ds_1", "totals": {"threshold": "10", "months_above": 9},
                "provenance": {"calculation_id": "calc-tiers-1", "core_version": "0.1.0",
                               "assumptions": ["Demo-Schwelle."], "limitations": []}}
TIERS_SUMMARY = "Stufen je Monat (Schwelle 10,00): 9 Monate über der Schwelle."
CHARACTERISTICS_OUTPUT = {
    "dataset_id": "ds_1",
    "characteristics": {"totals": {"annual_eur": "27999.6", "months": 12},
                        "volunteers": {"situation": "tight", "confidence": "medium"}},
    "provenance": {"calculation_id": "calc-char-1", "core_version": "0.1.0", "assumptions": [], "limitations": []},
}
CHARACTERISTICS_SUMMARY = "Merkmale des Kassabuchs: Jahresausgaben 27 999,60 € aus zwölf Monaten."
COSTS_OUTPUT = {"dataset_id": "ds_1", "totals": {"total_eur": "412.30"},
                "provenance": {"calculation_id": "calc-costs-1", "core_version": "0.1.0",
                               "assumptions": [], "limitations": []}}
COSTS_SUMMARY = "Kosten: 412,30 €."
ANALYSIS_SPECS = [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, TIERS_SPEC, CHARACTERISTICS_SPEC, COSTS_SPEC]


class AnalysisToolClient(FakeToolClient):
    """Answers the tier, characteristics and cost steps of the tools pack."""

    OUTPUTS = {"analyze_tiers": (TIERS_OUTPUT, TIERS_SUMMARY),
               "analyze_characteristics": (CHARACTERISTICS_OUTPUT, CHARACTERISTICS_SUMMARY),
               "calculate_costs": (COSTS_OUTPUT, COSTS_SUMMARY)}

    def call(self, server, name, arguments, timeout):
        if name not in self.OUTPUTS:
            return super().call(server, name, arguments, timeout)
        self.calls.append((name, dict(arguments)))
        output, summary = self.OUTPUTS[name]
        return ToolResult(call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != "owner"}),
                          server_name=server.name, structured_content=output, summary=summary,
                          calculation_id=output["provenance"]["calculation_id"], duration_ms=3)

    def list_tools(self, server):
        return ANALYSIS_SPECS


def _client(tmp_path, monkeypatch, provider=None, with_server=True, tool_client=None, specs=None,
            pack_overrides=None):
    install_tools_pack(tmp_path, monkeypatch, **(pack_overrides or {}))
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    if with_server:
        specs = specs or [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC]
        server = ToolServer.create(DOMAIN, "demo", "http://127.0.0.1:8600/mcp", "token-0123456789abcdef")
        store.save(server.model_copy(update={
            "tool_specs": specs, "allowed_tools": [spec.name for spec in specs],
        }))
    tool_client = tool_client or FakeToolClient()
    monkeypatch.setattr(fastapi_adapter, "tool_service_from_env", lambda: ToolService(store, tool_client))
    provider = provider or FakeToolProvider(source_ids=[])
    monkeypatch.setattr(fastapi_adapter, "get_provider", lambda name: provider)
    api = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json",
                                profile_path=tmp_path / "profiles.json"))
    return api, tool_client, provider


def _complete(api, headers=None):
    headers = headers or {}
    started = api.post("/api/v1/assessment-sessions", json={"domain_id": DOMAIN}, headers=headers).json()
    session_id = started["session_id"]
    for question_id, value in TOOLS_PACK_ANSWERS:
        api.post(f"/api/v1/assessment-sessions/{session_id}/inputs",
                 json={"kind": "submit_answer", "question_id": question_id, "value": value}, headers=headers)
    completed = api.post(f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"},
                         headers=headers).json()
    assert completed["status"] == "completed", completed
    return session_id


def _upload(api, headers=None, filename="export.csv"):
    return api.post(f"/api/v1/profiles/{DOMAIN}/dataset", headers=headers or {}, json={
        "filename": filename, "content": CSV, "format_hint": "auto",
    }).json()


def test_upload_read_and_delete_dataset(tmp_path, monkeypatch):
    # The fake server answers the tier, characteristics and cost steps of the
    # pack's analyses; the windows step is not registered and fails.
    api, tool_client, _ = _client(tmp_path, monkeypatch, tool_client=AnalysisToolClient(), specs=ANALYSIS_SPECS)
    before = _upload(api)
    assert before["ok"] is False and before["errors"][0]["code"] == "profile_not_found"
    _complete(api)

    uploaded = _upload(api, filename="C:\\\\Daten\\\\AT0000000000000000000000000000000-Kassabuch-2025.csv")
    assert uploaded["ok"] is True, uploaded
    dataset = uploaded["profile"]["dataset"]
    assert dataset["dataset_id"] == "ds_1"
    assert dataset["filename"] == "AT…-Kassabuch-2025.csv"
    assert tool_client.calls[0][1]["owner"] == "local"

    fetched = api.get(f"/api/v1/profiles/{DOMAIN}/dataset").json()
    assert fetched["ok"] is True and fetched["dataset"]["dataset_id"] == "ds_1"
    assert [r["call"]["name"] for r in fetched["tool_results"]] == [
        "analyze_tiers", "analyze_characteristics", "calculate_costs"]
    profile = api.get(f"/api/v1/profiles/{DOMAIN}").json()["profile"]
    assert profile["dataset"]["dataset_id"] == "ds_1"
    assert "Messdaten" in profile["report"]["content"]

    conversations = api.get("/api/v1/conversations").json()["conversations"]
    result_chat = next(c for c in conversations if c["title"] == "Ergebnis")
    # Every step of the first analysis runs right after the import, the
    # completion step follows with the profile's answers.
    announcements = [m for m in result_chat["messages"] if m["content"].startswith(("Messdaten importiert", "Erste Analyse"))]
    imported, tiers, windows, characteristics, costs = announcements[:5]
    assert imported["content"].startswith("Messdaten importiert")
    assert imported["tool_results"][0]["call"]["name"] == "import_dataset"
    assert tiers["content"].startswith("Erste Analyse: Stufen je Monat") and "Demo-Schwelle." in tiers["content"]
    assert windows["content"].startswith("Erste Analyse: Die erste Analyse konnte nicht erstellt werden")
    assert characteristics["content"].startswith("Erste Analyse: Merkmale des Kassabuchs")
    assert costs["content"].startswith("Erste Analyse: Kosten: 412,30 €") and "Richtwerte dieser Demo." in costs["content"]
    assert tool_client.calls[1] == ("analyze_tiers", {"dataset_id": "ds_1", "threshold": 10, "minimum": 2,
                                                      "owner": "local"})
    assert tool_client.calls[2][0] == "analyze_characteristics"
    assert tool_client.calls[3] == ("calculate_costs", {"dataset_id": "ds_1", "comparison": "standard",
                                                        "model": "sports_default", "owner": "local"})
    assert profile["tool_results"][0]["call"]["name"] == "analyze_tiers"

    deleted = api.delete(f"/api/v1/profiles/{DOMAIN}/dataset").json()
    assert deleted["ok"] is True and deleted["dataset"] is None and deleted["tool_results"] == []
    assert tool_client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})
    again = api.delete(f"/api/v1/profiles/{DOMAIN}/dataset").json()
    assert again["ok"] is False and again["errors"][0]["code"] == "invalid_state"

    invalid = api.post(f"/api/v1/profiles/{DOMAIN}/dataset", json={"filename": "x.csv"}).json()
    assert invalid["ok"] is False and invalid["errors"][0]["code"] == "invalid_input"


def test_advisory_message_carries_tool_results(tmp_path, monkeypatch):
    provider = FakeToolProvider(turns=[[("analyze_dataset", {"dataset_id": "ds_1"})], "fertig"],
                                source_ids=["T1"])
    api, tool_client, _ = _client(tmp_path, monkeypatch, provider=provider)
    _complete(api)
    assert _upload(api)["ok"] is True
    created = api.post("/api/v1/conversations", json={"domain_id": DOMAIN}).json()
    conversation_id = created["conversation"]["conversation_id"]

    answered = api.post(f"/api/v1/conversations/{conversation_id}/messages",
                        json={"text": "Wie hoch war mein Höchstwert im Jänner?"}).json()

    assert answered["ok"] is True, answered
    message = answered["conversation"]["messages"][-1]
    assert [r["call"]["name"] for r in message["tool_results"]] == ["analyze_dataset"]
    assert message["sections"][0]["sources"][0]["kind"] == "tool"
    assert tool_client.calls[-1][1] == {"dataset_id": "ds_1", "owner": "local"}
    profile = api.get(f"/api/v1/profiles/{DOMAIN}").json()["profile"]
    assert [r["call"]["name"] for r in profile["tool_results"]] == ["analyze_dataset"]

    # Deleting the profile deletes the dataset on the server first.
    assert api.delete(f"/api/v1/profiles/{DOMAIN}").json()["ok"] is True
    assert tool_client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})


def test_upload_is_scoped_by_participant_and_refused_without_tools(tmp_path, monkeypatch):
    api, tool_client, _ = _client(tmp_path, monkeypatch)
    anna = {"X-GuideOS-Participant": "anna"}
    bert = {"X-GuideOS-Participant": "bert"}
    _complete(api, anna)
    uploaded = _upload(api, anna)
    assert uploaded["ok"] is True
    assert tool_client.calls[0][1]["owner"] == "anna"
    assert api.get(f"/api/v1/profiles/{DOMAIN}/dataset", headers=bert).json()["errors"][0]["code"] == "profile_not_found"
    assert api.get(f"/api/v1/profiles/{DOMAIN}/dataset", headers=anna).json()["dataset"]["dataset_id"] == "ds_1"

    # The bundled verein-demo has no tools block.
    started = api.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=anna).json()
    session_id = started["session_id"]
    for question_id, value in [("club_type", "sports"), ("founded_year", 1980),
                               ("members_count", 120), ("funding_source", "public_grants"),
                               ("volunteer_situation", "tight"), ("annual_budget_eur", None)]:
        api.post(f"/api/v1/assessment-sessions/{session_id}/inputs",
                 json={"kind": "submit_answer", "question_id": question_id, "value": value}, headers=anna)
    api.post(f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}, headers=anna)
    refused = api.post("/api/v1/profiles/verein-demo/dataset", headers=anna,
                       json={"filename": "x.csv", "content": "a,b\n"}).json()
    assert refused["ok"] is False and refused["errors"][0]["code"] == "invalid_state"

    # Without an allowed server the pack's dataset tool is not available.
    api2, _, _ = _client(tmp_path / "b", monkeypatch, with_server=False)
    _complete(api2)
    refused = _upload(api2)
    assert refused["ok"] is False and refused["errors"][0]["code"] == "tool_error"
    assert "nicht freigegeben" in refused["errors"][0]["message"]
    assert json.dumps(refused)  # serialisable


def test_profile_view_shows_measurement_derived_answers(tmp_path, monkeypatch):
    # The tools pack derives records_kept and annual_budget_eur as answers; this
    # version also proposes volunteer_situation from the characteristics.
    tools = tools_pack_data()["tools"]
    tools["derive"].append({"field_key": "volunteer_situation", "tool": "analyze_characteristics",
                            "pointer": "/characteristics/volunteers/situation", "mode": "propose",
                            "confidence_pointer": "/characteristics/volunteers/confidence",
                            "note": "Viele Buchungen stammen von wenigen Personen."})
    api, tool_client, _ = _client(tmp_path, monkeypatch, tool_client=AnalysisToolClient(), specs=ANALYSIS_SPECS,
                                  pack_overrides={"tools": tools})
    session_id = api.post("/api/v1/assessment-sessions", json={"domain_id": DOMAIN}).json()["session_id"]

    def submit(question_id, value):
        return api.post(f"/api/v1/assessment-sessions/{session_id}/inputs",
                        json={"kind": "submit_answer", "question_id": question_id, "value": value}).json()

    submit("dataset_available", "yes")
    uploaded = submit("dataset_file", UPLOAD)
    assert uploaded["ok"], uploaded
    assert uploaded["answers"]["annual_budget_eur"] == {
        "question_id": "annual_budget_eur", "field_key": "annual_budget_eur",
        "raw_answer": "Aus den Messdaten abgeleitet", "normalized_value": 28000, "confidence": 1.0,
        "origin": "measurement", "calculation_id": "calc-char-1"}
    assert uploaded["answers"]["records_kept"]["normalized_value"] == "complete"
    assert uploaded["answers"]["records_kept"]["origin"] == "measurement"
    assert uploaded["current_question"]["question_id"] == "club_type" and uploaded["proposal"] is None
    for question_id, value in (("club_type", "sports"), ("founded_year", 1962), ("members_count", 120)):
        submit(question_id, value)
    answered = submit("funding_source", "public_grants")
    assert answered["proposal"] == {
        "question_id": "volunteer_situation", "field_key": "volunteer_situation", "normalized_value": "tight",
        "tool": "analyze_characteristics", "calculation_id": "calc-char-1", "confidence": "medium",
        "note": "Viele Buchungen stammen von wenigen Personen."}
    submit("volunteer_situation", "tight")
    response = submit("second_file", None)
    assert response["status"] == "review"
    completed = api.post(f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}).json()
    assert completed["status"] == "completed"
    # The completion step ran with the mapped answer.
    assert tool_client.calls[-1] == ("calculate_costs", {"dataset_id": "ds_1", "comparison": "standard",
                                                         "model": "sports_default", "owner": "local"})

    profile = api.get(f"/api/v1/profiles/{DOMAIN}").json()
    assert profile["ok"], profile
    answer = profile["profile"]["answers"]["annual_budget_eur"]
    assert (answer["origin"], answer["calculation_id"], answer["normalized_value"]) == ("measurement", "calc-char-1", 28000)
    assert profile["profile"]["answers"]["club_type"]["origin"] == "assessment"
    assert "aus den Messdaten abgeleitet" in profile["profile"]["report"]["content"]
    assert profile["profile"]["dataset"]["field_key"] == "dataset_file"
    assert [r["call"]["name"] for r in profile["profile"]["tool_results"]] == [
        "analyze_tiers", "analyze_characteristics", "calculate_costs"]


def test_series_endpoint_calls_the_read_only_tool_with_the_participant_as_owner(tmp_path, monkeypatch):
    """GET /profiles/{domain_id}/series asks the pack's series_tool for the
    profile's dataset; the browser never names the owner or the tool."""
    api, client, _ = _client(tmp_path, monkeypatch, tool_client=SeriesToolClient(),
                             specs=[ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, SERIES_SPEC])
    headers = {"X-GuideOS-Participant": "anna"}
    _complete(api, headers)
    before = api.get(f"/api/v1/profiles/{DOMAIN}/series", headers=headers).json()
    assert before["ok"] is False and before["errors"][0]["code"] == "no_dataset"

    assert _upload(api, headers)["ok"] is True
    days = api.get(f"/api/v1/profiles/{DOMAIN}/series", headers=headers).json()
    quarter = api.get(f"/api/v1/profiles/{DOMAIN}/series?resolution=quarter_hour&period_from=2025-01-15&period_to=2025-01-15",
                      headers=headers).json()
    invalid = api.get(f"/api/v1/profiles/{DOMAIN}/series?resolution=hour", headers=headers).json()
    foreign = api.get(f"/api/v1/profiles/{DOMAIN}/series", headers={"X-GuideOS-Participant": "bernd"}).json()

    assert days["ok"] is True and days["resolution"] == "day" and days["unit"] == "Stück"
    assert days["calculation_id"] == "calc-series-1"
    assert [p["value"] for p in days["points"]] == ["12.5", "11.0"] and days["points"][1]["complete"] is False
    series_calls = [c for c in client.calls if c[0] == "get_series"]
    assert series_calls[0][1]["owner"] == "anna" and series_calls[0][1]["dataset_id"].startswith("ds_")
    assert series_calls[1][1] == {"owner": "anna", "dataset_id": series_calls[0][1]["dataset_id"],
                                  "resolution": "quarter_hour", "period_from": "2025-01-15", "period_to": "2025-01-15"}
    assert quarter["ok"] is True and quarter["unit"] == "Stück/h"
    assert invalid["ok"] is False and invalid["errors"][0]["code"] == "invalid_input"
    assert foreign["ok"] is False and foreign["errors"][0]["code"] == "profile_not_found"


def test_series_endpoint_without_series_tool_in_the_pack_says_so(tmp_path, monkeypatch):
    api, _, _ = _client(tmp_path, monkeypatch, tool_client=SeriesToolClient(),
                        specs=[ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, SERIES_SPEC])
    headers = {"X-GuideOS-Participant": "anna"}
    _complete(api, headers)
    assert _upload(api, headers)["ok"] is True
    original = fastapi_adapter.load_domain_pack_version

    def without_series_tool(domain_id, version):
        pack = original(domain_id, version)
        return pack.model_copy(update={"tools": pack.tools.model_copy(update={"series_tool": None})})

    monkeypatch.setattr(fastapi_adapter, "load_domain_pack_version", without_series_tool)

    missing = api.get(f"/api/v1/profiles/{DOMAIN}/series", headers=headers).json()

    assert missing["ok"] is False and missing["errors"][0]["code"] == "series_unavailable"
