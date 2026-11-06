"""Load profile first (concept decisions of 20.10.2026): the first analysis
runs in the orchestrator right after the import of the advisory dataset,
the pack's derivations turn its figures into answers (origin measurement)
or proposals, metric rules compare the figures, and the profile and the
result chat carry the results on without computing them again."""

from datetime import UTC, datetime

from guideos.assessment.contract import to_contract_response
from guideos.assessment.models import MEASUREMENT_RAW_ANSWER
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.domains.loader import validate_domain_pack_data
from guideos.profiles.repository import LocalProfileRepository
from guideos.rules.engine import evaluate_rule_metrics, evaluate_rules
from guideos.tools.datasets import DatasetImporter
from guideos.tools.models import ToolError, ToolServer, ToolSpec
from guideos.tools.service import ToolService
from guideos.tools.store import LocalToolServerStore
from tests.tool_fakes import DELETE_SPEC, IMPORT_SPEC, ANALYZE_SPEC, FakeToolClient, FakeToolProvider

CSV = "timestamp,value\n2025-01-01T00:00,0.1\n"
UPLOAD = {"filename": "Jahresverbrauch-2025.csv", "content": CSV, "format_hint": "auto"}

TIERS_SPEC = ToolSpec(
    name="analyze_tiers", description="Leistungsstufen. Voraussetzung: dataset_id.",
    server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                                  "threshold_kw": {"type": "number", "default": 10},
                                                  "minimum_kw": {"type": "number", "default": 2}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"totals": {"type": "object"}}, "required": ["totals"]},
)
CHARACTERISTICS_SPEC = ToolSpec(
    name="analyze_characteristics", description="Merkmale. Voraussetzung: dataset_id.",
    server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"characteristics": {"type": "object"}},
                   "required": ["characteristics"]},
)
TIERS_OUTPUT = {
    "dataset_id": "ds_1",
    "months": [{"month": "2025-01", "peak_kw": "12.4", "kw_up_to_threshold": "10", "kw_above_threshold": "2.4"}],
    "totals": {"threshold_kw": "10", "minimum_kw": "2", "months_total": 12, "months_complete": 12,
               "months_above": 9, "kw_months_above_threshold": "19.888", "max_above_kw": "3.516"},
    "provenance": {"calculation_id": "calc-tiers-1", "core_version": "0.3.0", "assumptions": ["§ 6."],
                   "limitations": []},
}
TIERS_SUMMARY = "Leistungsstufen je Monat (Schwelle 10,00 kW, mindestens 2,00 kW): 2025-01: Spitze 12,40 kW"
CHARACTERISTICS_OUTPUT = {
    "dataset_id": "ds_1",
    "characteristics": {
        "energy": {"measured_kwh": "5249.524", "measured_days": 365, "annual_kwh": "5249.524",
                   "annual_months": ["2025-01", "2025-12"], "estimated_annual_kwh": None},
        "load": {"base_load_kw": "0.180", "average_load_kw": "0.599", "max_peak_kw": "13.516",
                 "max_peak_at": "2025-03-29T15:00:00+01:00", "peak_to_average_ratio": "22.6"},
        "charging": {"detected": True, "confidence": "high", "counted_sessions": 31, "power_kw": "10.8",
                     "weekly_kwh": "11.0", "charging_window": "evening_fixed", "peaks_in_sessions": 7},
        "seasonal": {"heat_pump_hint": "high", "night_ratio": "3.4"},
        "pv": {"hint": "medium", "zero_import_share_percent": "68.1"},
    },
    "provenance": {"calculation_id": "calc-char-1", "core_version": "0.3.0", "assumptions": ["Plateaus."],
                   "limitations": ["Koinzidenz."]},
}
CHARACTERISTICS_SUMMARY = "Profilmerkmale: Jahresenergie 5249,52 kWh aus zwölf vollständigen Monaten"


class AnalysisToolClient(FakeToolClient):
    def __init__(self, source_format="netz_noe", characteristics=None, fail=()):
        super().__init__()
        self.source_format = source_format
        self.characteristics = characteristics or CHARACTERISTICS_OUTPUT
        self.fail = set(fail)

    def call(self, server, name, arguments, timeout):
        if name in self.fail:
            self.calls.append((name, dict(arguments)))
            raise ToolError("tool_error", f"{name} scheitert.", {"code": "boom"})
        if name == "analyze_tiers":
            self.calls.append((name, dict(arguments)))
            return self._result(name, arguments, TIERS_OUTPUT, TIERS_SUMMARY)
        if name == "analyze_characteristics":
            self.calls.append((name, dict(arguments)))
            return self._result(name, arguments, self.characteristics, CHARACTERISTICS_SUMMARY)
        result = super().call(server, name, arguments, timeout)
        if name == "import_dataset":
            result.structured_content["source_format"] = self.source_format
        return result

    def _result(self, name, arguments, output, summary):
        from guideos.tools.models import ToolCall, ToolResult
        return ToolResult(call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != "owner"}),
                          server_name="demo", structured_content=output, summary=summary,
                          calculation_id=output["provenance"]["calculation_id"], duration_ms=3)

    def list_tools(self, server):
        return [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, TIERS_SPEC, CHARACTERISTICS_SPEC]


def _pack(*, derive=None, rules=None, after_import=None, at_completion=None):
    return validate_domain_pack_data({
        "domain_id": "profile-first", "version": "1.0.0", "name": "Kassabuch zuerst", "description": "Test",
        "default_locale": "de",
        "questions": [
            {"question_id": "dataset_available", "field_key": "dataset_available",
             "text": "Hast du dein Kassabuch als Datei?", "field_type": "select", "required": True,
             "options": [{"value": "yes", "label": "Ja"}, {"value": "no", "label": "Nein"},
                         {"value": "unknown", "label": "Weiß nicht"}]},
            {"question_id": "dataset_file", "field_key": "dataset_file", "text": "Kassabuch hochladen",
             "field_type": "file", "required": True, "allow_unknown": True,
             "visible_if": {"field_key": "dataset_available", "operator": "equals", "value": "yes"},
             "upload": {}},
            {"question_id": "grid_operator", "field_key": "grid_operator", "text": "Netzbetreiber?",
             "field_type": "select", "required": True,
             "options": [{"value": "netz_noe", "label": "Netz NÖ"}, {"value": "other", "label": "Anderer"}]},
            {"question_id": "smart_meter_mode", "field_key": "smart_meter_mode", "text": "Smart Meter?",
             "field_type": "select", "required": True,
             "options": [{"value": "none", "label": "Keiner"}, {"value": "quarter_hour", "label": "Viertelstunden"}]},
            {"question_id": "annual_electricity_kwh", "field_key": "annual_electricity_kwh", "text": "kWh im Jahr?",
             "field_type": "number", "required": True, "allow_unknown": True,
             "validation": {"minimum": 100, "maximum": 100000}},
            {"question_id": "electric_vehicle", "field_key": "electric_vehicle", "text": "E-Auto?",
             "field_type": "boolean", "required": True},
            {"question_id": "wallbox_kw", "field_key": "wallbox_kw", "text": "Ladeleistung?",
             "field_type": "number", "required": True,
             "visible_if": {"field_key": "electric_vehicle", "operator": "equals", "value": True},
             "validation": {"minimum": 1, "maximum": 22}},
            {"question_id": "ev_charging_window", "field_key": "ev_charging_window", "text": "Wann?",
             "field_type": "select", "required": True,
             "visible_if": {"field_key": "electric_vehicle", "operator": "equals", "value": True},
             "options": [{"value": "evening_fixed", "label": "Abends"}, {"value": "anytime", "label": "Egal"}]},
        ],
        "rules": rules if rules is not None else [
            {"rule_id": "peak-above-tier", "field_key": "dataset_file", "operator": "gte", "value": 1,
             "metric": {"tool": "analyze_tiers", "pointer": "/totals/months_above",
                        "label": "Monate mit Spitze über 10 kW"},
             "reason_code": "PEAK_ABOVE_TIER", "recommendation_ids": ["reduce-peaks"]},
            {"rule_id": "no-quarter-hour", "field_key": "smart_meter_mode", "operator": "equals", "value": "none",
             "reason_code": "NO_QUARTER_HOUR_DATA", "recommendation_ids": ["enable-data"]},
        ],
        "recommendations": [
            {"recommendation_id": "reduce-peaks", "priority": 1, "title": "Spitzen senken",
             "message": "Der Anteil über 10 kW wird höher bepreist."},
            {"recommendation_id": "enable-data", "priority": 2, "title": "Viertelstundenwerte freischalten",
             "message": "Opt-in im Portal."},
        ],
        "tools": {
            "enabled": True,
            "after_import": after_import if after_import is not None else [
                {"tool": "analyze_tiers", "arguments": {"threshold_kw": 10, "minimum_kw": 2},
                 "note": "Hinweis zu § 6."},
                {"tool": "analyze_characteristics", "note": "Hinweis zu Merkmalen."},
            ],
            **({"at_completion": at_completion} if at_completion is not None else {}),
            "derive": derive if derive is not None else [
                {"field_key": "smart_meter_mode", "tool": "import_dataset", "value": "quarter_hour"},
                {"field_key": "grid_operator", "tool": "import_dataset", "pointer": "/source_format",
                 "map": {"netz_noe": "netz_noe"}},
                {"field_key": "annual_electricity_kwh", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/energy/annual_kwh", "decimals": 0},
                {"field_key": "annual_electricity_kwh", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/energy/estimated_annual_kwh", "decimals": 0, "mode": "propose",
                 "note": "Hochrechnung der gemessenen Tage auf ein Jahr."},
                {"field_key": "electric_vehicle", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/charging/detected", "map": {"true": True}, "mode": "propose",
                 "when": {"pointer": "/characteristics/charging/confidence", "operator": "in",
                          "value": ["high", "medium"]},
                 "confidence_pointer": "/characteristics/charging/confidence",
                 "note": "In den Messdaten wurden regelmäßige Muster erkannt."},
                {"field_key": "wallbox_kw", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/charging/power_kw", "decimals": 1, "mode": "propose",
                 "when": {"pointer": "/characteristics/charging/confidence", "operator": "in",
                          "value": ["high", "medium"]}},
                {"field_key": "ev_charging_window", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/charging/charging_window", "mode": "propose",
                 "when": {"pointer": "/characteristics/charging/confidence", "operator": "equals",
                          "value": "high"}},
            ],
        },
    })


def analysis_tools(tmp_path, pack, client=None):
    """A tool service whose fake client answers the import and both analysis
    tools (the energy-windows tool stays unavailable: a failed step)."""
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = ToolServer.create(pack.domain_id, "demo", "http://127.0.0.1:8600/mcp", "token-0123456789abcdef")
    store.save(server.model_copy(update={
        "tool_specs": [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC, TIERS_SPEC, CHARACTERISTICS_SPEC],
        "allowed_tools": ["import_dataset", "delete_dataset", "analyze_dataset",
                          "analyze_tiers", "analyze_characteristics"],
    }))
    client = client or AnalysisToolClient()
    return ToolService(store, client, clock=lambda: datetime(2026, 10, 20, 12, tzinfo=UTC)), client


def _setup(tmp_path, pack=None, client=None):
    pack = pack or _pack()
    tools, client = analysis_tools(tmp_path, pack, client)
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles, dataset_importer=DatasetImporter(tools))
    response = orchestrator.start()
    return orchestrator, client, tools, assessments, profiles, response.session_id


def _upload(orchestrator, session_id):
    orchestrator.submit(session_id, "dataset_available", "yes")
    return orchestrator.submit(session_id, "dataset_file", UPLOAD)


def test_import_runs_the_first_analysis_and_derives_answers_and_proposals(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)

    response = _upload(orchestrator, session_id)

    assert response.validation_errors == []
    assert [call[0] for call in client.calls] == [
        "import_dataset", "analyze_tiers", "analyze_characteristics"]
    assert client.calls[1][1] == {"owner": "local", "dataset_id": "ds_1", "threshold_kw": 10, "minimum_kw": 2}
    session = assessments.get(session_id)
    assert [r.call.name for r in session.tool_results] == ["analyze_tiers", "analyze_characteristics"]
    events = [event.event_type for event in session.events]
    assert events.count("analysis_completed") == 2 and "answer_derived" in events and "answer_proposed" in events

    # Derived answers: constant, mapped import field, rounded figure.
    for field_key, value, calculation_id in (
        ("smart_meter_mode", "quarter_hour", None), ("grid_operator", "netz_noe", None),
        ("annual_electricity_kwh", 5250, "calc-char-1"),
    ):
        answer = response.answers[field_key]
        assert answer.normalized_value == value, field_key
        assert answer.origin == "measurement" and answer.raw_answer == MEASUREMENT_RAW_ANSWER
        assert answer.calculation_id == calculation_id
    # The derived questions are skipped: the next open one is the EV question, with its proposal.
    assert response.current_question.question_id == "electric_vehicle"
    assert response.proposal.field_key == "electric_vehicle" and response.proposal.normalized_value is True
    assert response.proposal.confidence == "high" and response.proposal.tool == "analyze_characteristics"
    assert response.proposal.note.startswith("In den Messdaten")
    assert set(session.proposals) == {"electric_vehicle", "wallbox_kw", "ev_charging_window"}  # no extrapolation
    assert session.proposals["wallbox_kw"].normalized_value == 10.8
    assert session.proposals["ev_charging_window"].normalized_value == "evening_fixed"

    # The participant confirms the proposal; the next proposal follows.
    response = orchestrator.submit(session_id, "electric_vehicle", True)
    assert response.answers["electric_vehicle"].origin == "user"
    assert response.current_question.question_id == "wallbox_kw" and response.proposal.normalized_value == 10.8
    response = orchestrator.submit(session_id, "wallbox_kw", 11)
    assert response.proposal.field_key == "ev_charging_window"
    response = orchestrator.submit(session_id, "ev_charging_window", "anytime")
    assert response.status == "review" and response.proposal is None

    contract = to_contract_response(response)
    assert contract.answers["annual_electricity_kwh"].origin == "measurement"
    assert contract.answers["annual_electricity_kwh"].calculation_id == "calc-char-1"
    assert contract.answers["wallbox_kw"].origin == "user" and contract.proposal is None


def test_proposals_need_their_condition_and_a_user_answer_is_never_overwritten(tmp_path):
    weak = {**CHARACTERISTICS_OUTPUT, "characteristics": {
        **CHARACTERISTICS_OUTPUT["characteristics"],
        "charging": {"detected": True, "confidence": "low", "power_kw": "3.7", "charging_window": "anytime"},
        "energy": {**CHARACTERISTICS_OUTPUT["characteristics"]["energy"], "annual_kwh": None,
                   "annual_months": [], "estimated_annual_kwh": "4782"},
    }}
    orchestrator, _, _, assessments, _, session_id = _setup(tmp_path, client=AnalysisToolClient(
        source_format="generic_de", characteristics=weak))

    response = _upload(orchestrator, session_id)

    session = assessments.get(session_id)
    # Fewer than twelve complete months: no annual answer, but the extrapolation as a proposal.
    assert set(session.proposals) == {"annual_electricity_kwh"}
    assert session.proposals["annual_electricity_kwh"].normalized_value == 4782
    assert session.proposals["annual_electricity_kwh"].confidence is None
    assert session.proposals["annual_electricity_kwh"].note.startswith("Hochrechnung")
    assert "annual_electricity_kwh" not in response.answers and "grid_operator" not in response.answers
    assert response.answers["smart_meter_mode"].origin == "measurement"
    assert response.current_question.question_id == "grid_operator" and response.proposal is None

    # The participant answers the consumption; a new upload keeps that answer.
    orchestrator.submit(session_id, "grid_operator", "other")
    orchestrator.submit(session_id, "annual_electricity_kwh", 4000)
    response = orchestrator.submit(session_id, "electric_vehicle", False)
    assert response.status == "review"
    response = orchestrator.revise(session_id, "dataset_file", {**UPLOAD, "filename": "neu.csv"})
    assert response.answers["annual_electricity_kwh"].normalized_value == 4000
    assert response.answers["annual_electricity_kwh"].origin == "user"
    assert response.answers["grid_operator"].origin == "user"


def test_skipping_or_replacing_the_dataset_clears_derivations_and_reruns_the_analysis(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)
    _upload(orchestrator, session_id)
    assert orchestrator.submit(session_id, "electric_vehicle", False).status == "review"

    response = orchestrator.revise(session_id, "dataset_file", None)

    session = assessments.get(session_id)
    assert session.tool_results == [] and session.proposals == {}
    assert "annual_electricity_kwh" not in response.answers and "smart_meter_mode" not in response.answers
    assert response.status == "in_progress" and response.current_question.question_id == "grid_operator"
    assert client.calls[-1][0] == "delete_dataset"

    # The participant answers what the derivation no longer covers (the
    # consumption as unknown), then uploads again in the review: the analysis
    # runs again, "unknown" is filled from the data, own answers stand.
    orchestrator.submit(session_id, "grid_operator", "other")
    orchestrator.submit(session_id, "smart_meter_mode", "none")
    assert orchestrator.submit(session_id, "annual_electricity_kwh", None).status == "review"
    response = orchestrator.revise(session_id, "dataset_file", UPLOAD)
    assert [r.call.name for r in assessments.get(session_id).tool_results] == [
        "analyze_tiers", "analyze_characteristics"]
    assert response.answers["annual_electricity_kwh"].origin == "measurement"
    assert response.answers["annual_electricity_kwh"].normalized_value == 5250
    assert response.answers["smart_meter_mode"].normalized_value == "none"
    assert response.answers["grid_operator"].origin == "user"
    # Revising with the stored reference (file kept) changes nothing.
    calls_before = len(client.calls)
    orchestrator.revise(session_id, "dataset_file", response.answers["dataset_file"].normalized_value)
    assert len(client.calls) == calls_before
    assert assessments.get(session_id).answers["annual_electricity_kwh"].origin == "measurement"


def test_a_failing_analysis_step_is_recorded_and_the_other_steps_still_run(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(
        tmp_path, client=AnalysisToolClient(fail={"analyze_tiers"}))

    response = _upload(orchestrator, session_id)

    assert response.validation_errors == []
    session = assessments.get(session_id)
    assert [r.call.name for r in session.tool_results] == ["analyze_characteristics"]
    failed = next(event for event in session.events if event.event_type == "analysis_failed")
    assert failed.payload == {"tool": "analyze_tiers", "code": "tool_error"}
    assert response.answers["annual_electricity_kwh"].normalized_value == 5250


def test_metric_rules_compare_tool_figures_and_cite_the_calculation(tmp_path):
    orchestrator, client, tools, assessments, profiles, session_id = _setup(tmp_path)
    _upload(orchestrator, session_id)
    orchestrator.submit(session_id, "electric_vehicle", False)
    session = assessments.get(session_id)

    evaluation = evaluate_rules(orchestrator.pack, session)
    assert evaluation.reason_codes == ["PEAK_ABOVE_TIER"]
    match = evaluation.matches[0]
    assert match.field_key == "dataset_file"
    assert match.evidence_value == {"metric": "/totals/months_above", "label": "Monate mit Spitze über 10 kW",
                                    "tool": "analyze_tiers", "value": 9, "calculation_id": "calc-tiers-1"}
    metric = next(item for item in evaluate_rule_metrics(orchestrator.pack, session) if item.rule_id == "peak-above-tier")
    assert (metric.value, metric.threshold, metric.matched, metric.tool) == (9, 1, True, "analyze_tiers")
    assert metric.calculation_id == "calc-tiers-1" and metric.label == "Monate mit Spitze über 10 kW"

    response = orchestrator.complete(session_id)
    recommendation = response.result.recommendations[0]
    assert recommendation.recommendation_id == "reduce-peaks"
    assert "Monate mit Spitze über 10 kW: 9 (Berechnung analyze_tiers)" in response.report.content
    assert "kWh im Jahr?**: 5250 (aus den Messdaten abgeleitet)" in response.report.content
    assert "## Berechnungen" in response.report.content and "calc-tiers-1" in response.report.content

    # Without a dataset the metric rule does not fire and nothing crashes.
    other = orchestrator.start()
    orchestrator.submit(other.session_id, "dataset_available", "no")
    assert evaluate_rules(orchestrator.pack, assessments.get(other.session_id)).reason_codes == []

    # The profile carries the session's results; the result chat posts them without new calls.
    profile = profiles.get("profile-first")
    assert [r.call.name for r in profile.tool_results] == ["analyze_tiers", "analyze_characteristics"]
    assert profile.answers["annual_electricity_kwh"].origin == "measurement"
    assert profile.answers["annual_electricity_kwh"].calculation_id == "calc-char-1"
    from guideos.conversations.service import ANALYSIS_INTRO, ConversationService
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    service = ConversationService(conversations, profiles, FakeToolProvider(),
                                  load_pack=lambda d, v: orchestrator.pack, tools=tools)
    analysis_calls = len([c for c in client.calls if c[0].startswith("analyze_")])
    conversation = service.ensure_result_conversation("profile-first", "http://fake", "fake")
    stored = conversations.get(conversation.conversation_id)
    analyses = [m for m in stored.messages if m.content.startswith(ANALYSIS_INTRO)]
    assert [m.content.split(": ", 1)[1][:20] for m in analyses] == ["Leistungsstufen je M", "Profilmerkmale: Jahr"]
    assert analyses[0].content.endswith("Hinweis zu § 6.") and analyses[1].content.endswith("Hinweis zu Merkmalen.")
    assert analyses[0].sections[0].sources[0].reference == "calc-tiers-1"
    assert len([c for c in client.calls if c[0].startswith("analyze_")]) == analysis_calls
    rule_message = next(m for m in stored.messages if "Spitzen senken" in m.content)
    kinds = [(source.kind, source.reference) for source in rule_message.sections[0].sources]
    assert ("tool", "calc-tiers-1") in kinds and ("rule", "reduce-peaks") in kinds
    assert "Monate mit Spitze über 10 kW: 9" in rule_message.content


def test_knowledge_recommendations_at_completion_see_the_first_analysis(tmp_path):
    """The analyses of the upload are in the session when complete() generates
    the knowledge recommendations, so the model gets them as TOOL_RESULTS."""
    import json

    from guideos.recommendations.service import knowledge_recommender
    from tests.test_llm_recommendations import FakeStructuredProvider

    provider = FakeStructuredProvider({"recommendations": [{
        "recommendation_id": "stagger-loads", "title": "Geräte staffeln",
        "message": "In 9 Monaten lag die Spitze über 10 kW.", "rationale": "Viertelstundenwerte liegen vor.",
        "evidence_fields": ["smart_meter_mode"], "confidence": 0.8, "source_ids": ["T1"],
    }]})
    pack = _pack()
    tools, client = analysis_tools(tmp_path, pack)
    orchestrator = AssessmentOrchestrator(
        pack, LocalAssessmentRepository(tmp_path / "assessments.json"),
        knowledge_recommender=knowledge_recommender(provider),
        profiles=LocalProfileRepository(tmp_path / "profiles.json"), dataset_importer=DatasetImporter(tools),
    )
    session_id = orchestrator.start().session_id
    _upload(orchestrator, session_id)
    orchestrator.submit(session_id, "electric_vehicle", False)

    response = orchestrator.complete(session_id, "http://fake", "fake")

    payload = json.loads(provider.messages[1]["content"])
    assert [t["source_id"] + " " + t["tool"] for t in payload["tool_results"]] == [
        "T1 analyze_tiers", "T2 analyze_characteristics"]
    assert payload["dataset"]["dataset_id"] == "ds_1"
    [item] = response.result.knowledge_recommendations
    assert item.sources[0].kind == "tool" and item.sources[0].reference == "calc-tiers-1"
    assert item.unverified_values == []
    assert response.result.knowledge_generation.tool_calculation_ids == ["calc-tiers-1", "calc-char-1"]


COSTS_OUTPUT = {
    "dataset_id": "ds_1",
    "totals": {"months_included": 12, "total_eur": "412.30",
               "comparisons": [{"tariff_id": "x-2025", "comparison_eur": "355.75", "delta_eur": "56.55"},
                               {"tariff_id": "x-2026", "comparison_eur": "370.10", "delta_eur": "42.20",
                                "grid_loss_eur": "24.96"}]},
    "provenance": {"calculation_id": "calc-costs-1", "core_version": "0.3.0", "assumptions": [], "limitations": []},
}
COSTS_SPEC = ToolSpec(
    name="calculate_costs", description="Kosten. Voraussetzung: dataset_id.", server_name="demo",
    input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "dataset_id": {"type": "string"},
                                                  "comparison_tariff_ids": {"type": "array"},
                                                  "include_flex": {"type": "boolean"}},
                  "required": ["owner", "dataset_id"]},
    output_schema={"type": "object", "properties": {"totals": {"type": "object"}}, "required": ["totals"]},
)


class CostToolClient(AnalysisToolClient):
    def call(self, server, name, arguments, timeout):
        if name == "calculate_costs":
            self.calls.append((name, dict(arguments)))
            if "calculate_costs" in self.fail:
                raise ToolError("tool_error", "Kosten scheitern.", {"code": "boom"})
            return self._result(name, arguments, COSTS_OUTPUT, "Kosten: 412,30 €.")
        return super().call(server, name, arguments, timeout)

    def list_tools(self, server):
        return [*super().list_tools(server), COSTS_SPEC]


AT_COMPLETION = [{
    "tool": "calculate_costs", "arguments": {"include_flex": False},
    "arguments_from_answers": {"comparison_tariff_ids": {
        "field_key": "grid_operator",
        "map": {"netz_noe": ["region-b-2025", "region-b-2026"]},
        "default": ["region-b-2025"]}},
    "note": "Vergleich nach Region.",
}]


def _cost_setup(tmp_path, fail=()):
    pack = _pack(at_completion=AT_COMPLETION)
    client = CostToolClient(fail=fail)
    tools, client = analysis_tools(tmp_path, pack, client)
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = store.get(pack.domain_id, "demo")
    store.save(server.model_copy(update={"tool_specs": [*server.tool_specs, COSTS_SPEC],
                                         "allowed_tools": [*server.allowed_tools, "calculate_costs"]}))
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(pack, LocalAssessmentRepository(tmp_path / "assessments.json"),
                                          profiles=profiles, dataset_importer=DatasetImporter(tools))
    return orchestrator, client, profiles


def test_completion_steps_run_with_arguments_from_answers(tmp_path):
    """A step of tools.at_completion runs when the assessment completes, with
    arguments taken from the answers (here the grid area), and its result
    joins the session's and profile's tool results."""
    orchestrator, client, profiles = _cost_setup(tmp_path)
    session_id = orchestrator.start().session_id
    _upload(orchestrator, session_id)   # derives grid_operator = netz_noe from the portal format
    assert "calculate_costs" not in [call[0] for call in client.calls]
    orchestrator.submit(session_id, "electric_vehicle", False)

    response = orchestrator.complete(session_id)

    call = next(call for call in client.calls if call[0] == "calculate_costs")
    assert call[1] == {"owner": "local", "dataset_id": "ds_1", "include_flex": False,
                       "comparison_tariff_ids": ["region-b-2025", "region-b-2026"]}
    session = orchestrator.repository.get(session_id)
    assert [r.call.name for r in session.tool_results] == [
        "analyze_tiers", "analyze_characteristics", "calculate_costs"]
    assert any(e.event_type == "analysis_completed" and e.payload["tool"] == "calculate_costs"
               for e in session.events)
    profile = profiles.get("profile-first")
    assert [r.call.name for r in profile.tool_results][-1] == "calculate_costs"
    assert "calc-costs-1" in response.report.content


def test_completion_steps_use_the_default_without_a_mapped_answer_and_survive_failures(tmp_path):
    def answer_until_review(orchestrator, session_id, values):
        response = orchestrator.get(session_id)
        while response.status == "in_progress":
            question = response.current_question
            response = orchestrator.submit(session_id, question.question_id, values[question.question_id])
            assert response.validation_errors == [], question.question_id

    orchestrator, client, _ = _cost_setup(tmp_path)
    session_id = orchestrator.start().session_id
    orchestrator.submit(session_id, "dataset_available", "yes")
    client.source_format = "generic_de"   # no derived grid operator
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    answer_until_review(orchestrator, session_id, {"grid_operator": "other", "annual_electricity_kwh": 4000,
                                                   "electric_vehicle": False, "smart_meter_mode": "quarter_hour"})
    orchestrator.complete(session_id)
    call = next(call for call in client.calls if call[0] == "calculate_costs")
    assert call[1]["comparison_tariff_ids"] == ["region-b-2025"]

    # Without a dataset the completion steps do not run; a failing step is recorded, completion stands.
    orchestrator, client, _ = _cost_setup(tmp_path / "b", fail=("calculate_costs",))
    session_id = orchestrator.start().session_id
    _upload(orchestrator, session_id)
    orchestrator.submit(session_id, "electric_vehicle", False)
    response = orchestrator.complete(session_id)
    assert response.status == "completed"
    session = orchestrator.repository.get(session_id)
    assert any(e.event_type == "analysis_failed" and e.payload["tool"] == "calculate_costs" for e in session.events)
    assert "calculate_costs" not in [r.call.name for r in session.tool_results]

    orchestrator, client, _ = _cost_setup(tmp_path / "c")
    other = orchestrator.start().session_id
    orchestrator.submit(other, "dataset_available", "no")
    answer_until_review(orchestrator, other, {"grid_operator": "other", "smart_meter_mode": "none",
                                              "annual_electricity_kwh": 3000, "electric_vehicle": False})
    orchestrator.complete(other)
    assert all(call[0] != "calculate_costs" for call in client.calls)
