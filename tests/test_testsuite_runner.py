import json

import os

import httpx
from fastapi.testclient import TestClient

from guideos.adapters.fastapi import create_app
from testsuite.client import GuideOSClient
from testsuite.models import Scenario
from testsuite.repository import JsonlRepository
from testsuite.runner import execute_run, run_exit_code, rerun_scenarios


def make_scenario(id="basic", steps=None, **extra):
    return Scenario.model_validate({"schema_version": 1, "id": id, "version": "1", "domain_id": "verein-demo",
        "domain_version": "1.0.0", "persist_raw": True,
        "steps": steps or [{"id": "start", "action": "start_assessment"}], **extra})


def api_client(tmp_path, tool_servers=None):
    """The API behind the runner's HTTP client; tool_servers ({domain_id:
    running MiniServer}) are registered for their domains."""
    if tool_servers:
        from pathlib import Path
        from testsuite.lifecycle import register_tool_servers
        register_tool_servers(Path(os.environ["GUIDEOS_TOOL_SERVERS_PATH"]),
                              {domain_id: {"url": server.url, "token": server.token}
                               for domain_id, server in tool_servers.items()})
    api = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))
    def handler(request):
        forwarded = {k: v for k, v in request.headers.items() if k.lower().startswith("x-")}
        response = api.request(request.method, request.url.path, content=request.content,
                               headers={"Content-Type": "application/json", **forwarded})
        return httpx.Response(response.status_code, json=response.json())
    return GuideOSClient("http://localhost", transport=httpx.MockTransport(handler))


def test_unexpected_failure_stops_case_but_next_case_runs(tmp_path):
    repo = JsonlRepository(tmp_path / "results")
    bad = make_scenario("bad", steps=[
        {"id": "start", "action": "start_assessment", "assertions": [{"path": "/status", "op": "eq", "expected": "completed"}]},
        {"id": "unreached", "action": "get_report"},
    ])
    with api_client(tmp_path) as client:
        run = execute_run([bad, make_scenario()], client, repo)
    data = repo.read(run["id"])
    assert [case["status"] for case in data["cases"]] == ["failed", "passed"]
    assert run_exit_code(run) == 1
    assert all(event["action"] != "get_report" for event in data["events"])


def test_expected_error_continues_and_rerun_uses_snapshot(tmp_path):
    scenario = make_scenario(steps=[
        {"id": "start", "action": "start_assessment"},
        {"id": "early", "action": "get_report", "expected_ok": False,
         "assertions": [{"path": "/errors", "op": "contains", "expected": {"code": "invalid_state"}}]},
        {"id": "state", "action": "get_session"},
    ])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo)
    assert run["status"] == "passed"
    loaded = rerun_scenarios(repo.read(run["id"]))
    assert loaded[0].model_dump() == scenario.model_dump()


def test_llm_skips_without_calling_api_and_error_takes_precedence(tmp_path):
    repo = JsonlRepository(tmp_path / "results")
    llm = make_scenario("llm", steps=[{"id": "s", "action": "start_assessment"},
        {"id": "text", "action": "submit_text_answer", "payload": {"question_id": "club_type", "text": "Haus"}}])
    def broken(request):
        raise httpx.ConnectError("offline", request=request)
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(broken)) as client:
        run = execute_run([llm, make_scenario()], client, repo)
    assert [c["status"] for c in repo.read(run["id"])["cases"]] == ["skipped", "error"]
    assert run_exit_code(run) == 2


def test_raw_opt_out_redacts_assertion_values_and_disallows_inexact_rerun(tmp_path):
    scenario = make_scenario(persist_raw=False, steps=[
        {"id": "start", "action": "start_assessment", "assertions": [
            {"path": "/assistant_message", "op": "eq", "expected": "sensitive prose"}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo)
    data = repo.read(run["id"])
    assert "sensitive prose" not in json.dumps(data)
    import pytest
    with pytest.raises(ValueError, match="Snapshot"):
        rerun_scenarios(data)


def start_configured_tool_servers():
    """One in-process server per module named in testsuite/tool_servers/
    (`in_process` = "package.module:function"), keyed by domain."""
    import importlib
    from testsuite.lifecycle import tool_server_configs
    from tests.mini_mcp_server import MINI_TOKEN
    started = {}
    servers = {}
    for domain_id, config in tool_server_configs().items():
        if config["module"] not in started:
            module_name, function_name = config["in_process"].split(":")
            server = getattr(importlib.import_module(module_name), function_name)(MINI_TOKEN)
            server.token = MINI_TOKEN
            started[config["module"]] = server
        servers[domain_id] = started[config["module"]]
    return servers


def test_bundled_deterministic_scenarios_pass_through_rest(tmp_path):
    from testsuite.loader import load_scenarios
    repo = JsonlRepository(tmp_path / "results")
    servers = start_configured_tool_servers()
    try:
        with api_client(tmp_path, tool_servers=servers) as client:
            run = execute_run(load_scenarios(), client, repo)
    finally:
        for server in {id(s): s for s in servers.values()}.values():
            server.stop()
    assert run["status"] == "passed", repo.read(run["id"])
    # Without --include-llm every scenario that needs a model is skipped, every other one passes.
    statuses = {case["scenario_id"]: case["status"] for case in repo.read(run["id"])["cases"]}
    expected = {scenario.id: "skipped" if scenario.requires_llm else "passed" for scenario in load_scenarios()}
    assert statuses == expected
    assert "skipped" in statuses.values() and "passed" in statuses.values()


class ScriptedProvider:
    default_endpoint = "http://unused"
    default_model = "scripted"
    def __init__(self, result):
        self.result = result
    def generate_structured(self, *args):
        if "sections" in args[3].get("properties", {}):
            return {"sections": [{"text": "Synthetische Erklärung.", "source_ids": []}]}
        if args[3].get("title") == "ProfileChangeProposal":
            return {"field_key": None, "value": None}
        return self.result
    def stream_chat(self, *args):
        yield "Synthetische Erklärung."


def test_uncertain_interpretation_keeps_question_and_answers(monkeypatch, tmp_path):
    provider = ScriptedProvider({"decision": "uncertain", "normalized_value": None,
        "confidence": 0.2, "reason_code": "ambiguous", "follow_up": "Welcher Gebäudetyp?"})
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
        {"id": "text", "action": "submit_text_answer", "payload": {"question_id": "club_type", "text": "Haus"},
         "expected_ok": False, "assertions": [
             {"path": "/errors", "op": "contains", "expected": {"code": "interpretation_uncertain"}},
             {"path": "/answers", "op": "eq", "expected": {}},
             {"path": "/current_question/question_id", "op": "eq", "expected": "club_type"}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo, include_llm=True)
    assert run["status"] == "passed"


def test_proposals_require_acceptance_and_support_discard_with_references(monkeypatch, tmp_path):
    provider = ScriptedProvider({"message": "Ein Vorschlag.", "proposes_answer": True,
                                 "normalized_value": "sports", "confidence": 0.9})
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
        {"id": "help", "action": "question_help", "payload": {"question_id": "club_type", "text": "Sportverein"},
         "assertions": [{"path": "/answers", "op": "eq", "expected": {}}]},
        {"id": "discard", "action": "discard_proposed_answer", "references": {"message_id": "help#/chat_messages/1/message_id"},
         "assertions": [{"path": "/chat_messages/1/proposed_answer", "op": "eq", "expected": None}]},
        {"id": "help_again", "action": "question_help", "payload": {"question_id": "club_type", "text": "Sportverein"}},
        {"id": "accept", "action": "accept_proposed_answer", "references": {"message_id": "help_again#/chat_messages/3/message_id"},
         "assertions": [{"path": "/accepted_answer/normalized_value", "op": "eq", "expected": "sports"},
                        {"path": "/current_question/question_id", "op": "eq", "expected": "founded_year"}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo, include_llm=True)
    assert run["status"] == "passed", repo.read(run["id"])


def test_provider_timeout_and_malformed_output_through_rest(monkeypatch, tmp_path):
    from guideos.providers.ollama import OllamaProvider
    import io
    for index, mode in enumerate(("timeout", "malformed", "schema")):
        provider = OllamaProvider() if mode != "schema" else ScriptedProvider({"not": "the schema"})
        monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
        def respond(*args, **kwargs):
            if mode == "timeout":
                raise TimeoutError("scripted timeout")
            return io.BytesIO(b'{"message":{"content":"not json"}}')
        monkeypatch.setattr("guideos.providers.ollama.request.urlopen", respond)
        scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
            {"id": "text", "action": "submit_text_answer", "payload": {"question_id": "club_type", "text": "Haus"},
             "expected_ok": False, "assertions": [
                 {"path": "/validation_errors", "op": "contains", "expected": {"code": "interpretation_failed"}},
                 {"path": "/answers", "op": "eq", "expected": {}}]},
            {"id": "get", "action": "get_session"}])
        case_path = tmp_path / str(index)
        case_path.mkdir()
        repo = JsonlRepository(case_path / "results")
        with api_client(case_path) as client:
            run = execute_run([scenario], client, repo, include_llm=True)
        assert run["status"] == "passed", repo.read(run["id"])


def test_cancellation_finalizes_cases_and_run(tmp_path):
    def cancelled(request):
        raise KeyboardInterrupt
    repo = JsonlRepository(tmp_path)
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(cancelled)) as client:
        run = execute_run([make_scenario("one"), make_scenario("two")], client, repo)
    assert run_exit_code(run) == 130
    assert all(case["status"] == "cancelled" for case in repo.read(run["id"])["cases"])
    assert repo.read(run["id"])["events"][0]["error_code"] == "KeyboardInterrupt"


def test_failed_requests_keep_correlation_id_and_session_on_failed_start_assertion(tmp_path):
    repo = JsonlRepository(tmp_path)
    def broken(request):
        raise httpx.ConnectError("offline", request=request)
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(broken)) as client:
        run = execute_run([make_scenario()], client, repo)
    event = repo.read(run["id"])["events"][0]
    assert event["request_id"]
    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment", "assertions": [
        {"path": "/status", "op": "eq", "expected": "completed"}]}])
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo)
    assert repo.read(run["id"])["cases"][0]["session_id"]


COMPLETION_STEPS = [
    {"id": "start", "action": "start_assessment"},
    *[{"id": q, "action": "submit_answer", "payload": {"question_id": q, "value": v}} for q, v in [
        ("club_type", "sports"), ("founded_year", 1980), ("members_count", 120),
        ("funding_source", "public_grants"), ("volunteer_situation", "tight"), ("annual_budget_eur", None)]],
    {"id": "complete", "action": "complete"},
]


def test_conversation_actions_map_to_conversation_resources():
    from testsuite.runner import action_request
    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
        {"id": "create", "action": "create_conversation", "payload": {"title": "Förderung"}},
        {"id": "send", "action": "send_conversation_message", "payload": {"text": "Frage"}},
        {"id": "get", "action": "get_conversation"},
        {"id": "other", "action": "get_conversation", "references": {"conversation_id": "create#/conversation/conversation_id"}},
        {"id": "list", "action": "list_conversations"},
        {"id": "delete", "action": "delete_conversation"},
        {"id": "recs", "action": "llm_recommendations"}])
    steps = {step.id: step for step in scenario.steps}
    responses = {"create": {"conversation": {"conversation_id": "conv_a"}}}
    assert action_request(scenario, steps["create"], "sess_1", responses) == (
        "POST", "/api/v1/conversations", {"domain_id": "verein-demo", "title": "Förderung"})
    assert action_request(scenario, steps["send"], "sess_1", responses, conversation_id="conv_b") == (
        "POST", "/api/v1/conversations/conv_b/messages", {"text": "Frage"})
    assert action_request(scenario, steps["get"], "sess_1", responses, conversation_id="conv_b") == (
        "GET", "/api/v1/conversations/conv_b", None)
    assert action_request(scenario, steps["other"], "sess_1", responses, conversation_id="conv_b") == (
        "GET", "/api/v1/conversations/conv_a", None)
    assert action_request(scenario, steps["list"], "sess_1", responses) == ("GET", "/api/v1/conversations", None)
    assert action_request(scenario, steps["delete"], "sess_1", responses, conversation_id="conv_b") == (
        "DELETE", "/api/v1/conversations/conv_b", None)
    assert action_request(scenario, steps["recs"], "sess_1", responses) == (
        "POST", "/api/v1/assessment-sessions/sess_1/llm-recommendations", None)
    import pytest
    with pytest.raises(ValueError, match="Konversations-ID"):
        action_request(scenario, steps["get"], "sess_1", responses)


def test_deterministic_conversation_workflow_through_rest(tmp_path):
    scenario = make_scenario(steps=[
        {"id": "start", "action": "start_assessment"},
        {"id": "too_early", "action": "create_conversation", "expected_ok": False,
         "assertions": [{"path": "/errors", "op": "contains", "expected": {"code": "profile_not_found"}}]},
        *COMPLETION_STEPS[1:],
        {"id": "create", "action": "create_conversation", "payload": {"title": "Förderung"},
         "assertions": [{"path": "/conversation/messages", "op": "eq", "expected": []},
                        {"path": "/conversation/title", "op": "eq", "expected": "Förderung"}]},
        {"id": "get", "action": "get_conversation",
         "assertions": [{"path": "/conversation/title", "op": "eq", "expected": "Förderung"}]},
        {"id": "list", "action": "list_conversations",
         "assertions": [{"path": "/conversations", "op": "contains", "expected": {"title": "Förderung"}}]},
        {"id": "delete", "action": "delete_conversation"},
        {"id": "gone", "action": "get_conversation", "expected_ok": False,
         "assertions": [{"path": "/errors", "op": "contains", "expected": {"code": "conversation_not_found"}}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo)
    assert run["status"] == "passed", repo.read(run["id"])
    assert scenario.requires_llm is False


def test_conversations_and_llm_recommendations_with_scripted_provider(monkeypatch, tmp_path):
    provider = ScriptedProvider({"recommendations": [{
        "recommendation_id": "hydraulic-balancing", "title": "Hydraulischen Abgleich prüfen",
        "message": "Senkt den Verbrauch.", "rationale": "Gasheizung.", "evidence_fields": ["funding_source"],
        "confidence": 0.7}]})
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
    scenario = make_scenario(steps=[
        *COMPLETION_STEPS,
        {"id": "create_a", "action": "create_conversation", "payload": {"title": "A"}},
        {"id": "send_a", "action": "send_conversation_message", "payload": {"text": "Welche Heizung?"},
         "assertions": [{"path": "/conversation/messages/0/content", "op": "eq", "expected": "Welche Heizung?"},
                        {"path": "/conversation/messages/1/role", "op": "eq", "expected": "assistant"},
                        {"path": "/conversation/messages/1/sections/0/sources/0/kind", "op": "eq",
                         "expected": "llm"}]},
        {"id": "create_b", "action": "create_conversation", "payload": {"title": "B"},
         "assertions": [{"path": "/conversation/messages", "op": "eq", "expected": []}]},
        {"id": "send_b", "action": "send_conversation_message", "payload": {"text": "Andere Frage"}},
        {"id": "get_a", "action": "get_conversation",
         "references": {"conversation_id": "create_a#/conversation/conversation_id"},
         "assertions": [{"path": "/conversation/messages/1/role", "op": "eq", "expected": "assistant"},
                        {"path": "/conversation/messages/2", "op": "exists", "expected": False}]},
        {"id": "recs", "action": "llm_recommendations",
         "assertions": [{"path": "/recommendations", "op": "contains", "expected": {"source": "llm"}}]},
        {"id": "session", "action": "get_session",
         "assertions": [{"path": "/result/recommendations", "op": "contains", "expected": {"source": "rule"}}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([scenario], client, repo, include_llm=True)
    assert run["status"] == "passed", repo.read(run["id"])
    assert scenario.requires_llm is True


def test_repeat_runs_each_scenario_several_times_and_reports_pass_rates(tmp_path):
    from testsuite.reports import build_report
    flaky = make_scenario("flaky", steps=[{"id": "start", "action": "start_assessment", "assertions": [
        {"path": "/status", "op": "eq", "expected": "completed"}]}])
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        run = execute_run([make_scenario(), flaky], client, repo, repeat=3)
    data = repo.read(run["id"])
    cases = data["cases"]
    assert [(c["scenario_id"], c["attempt"]) for c in cases] == [
        ("basic", 1), ("basic", 2), ("basic", 3), ("flaky", 1), ("flaky", 2), ("flaky", 3)]
    assert [c["status"] for c in cases] == ["passed"] * 3 + ["failed"] * 3
    assert run["status"] == "failed"
    report = build_report(data)
    assert "basic: 3/3 bestanden" in report
    assert "flaky: 0/3 bestanden" in report
    assert "## basic (Versuch 2): passed" in report
    assert [s.id for s in rerun_scenarios(data)] == ["basic", "flaky"]


def test_repeat_must_be_positive(tmp_path):
    import pytest
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client, pytest.raises(ValueError, match="repeat"):
        execute_run([make_scenario()], client, repo, repeat=0)


def test_report_shows_llm_inputs_and_texts_per_step():
    from testsuite.reports import build_report
    run = {"id": "run1", "status": "passed", "metadata": {}}
    case = {"id": "case1", "run_id": "run1", "scenario_id": "llm", "status": "passed", "attempt": 1}
    events = [
        {"case_id": "case1", "action": "submit_text_answer", "http_status": 200, "duration_ms": 5.0,
         "request_payload": {"question_id": "club_type", "text": "Wir sind ein Sportverein."},
         "response_payload": {"assistant_message": "In welchem Jahr wurde der Verein gegründet?",
                              "accepted_answer": {"normalized_value": "sports", "confidence": 0.9},
                              "validation_errors": [], "chat_messages": []}},
        {"case_id": "case1", "action": "submit_text_answer", "http_status": 200, "duration_ms": 5.0,
         "request_payload": {"question_id": "club_type", "text": "Keine Ahnung."},
         "response_payload": {"assistant_message": "Meinst du einen Sportverein?", "accepted_answer": None,
                              "validation_errors": [{"field_key": "club_type", "code": "ambiguous",
                                                     "message": "Meinst du einen Sportverein?"}],
                              "chat_messages": []}},
        {"case_id": "case1", "action": "question_help", "http_status": 200, "duration_ms": 5.0,
         "request_payload": {"question_id": "club_type", "text": "Was bedeutet Sportverein?"},
         "response_payload": {"assistant_message": "Welche Art von Verein ist es?",
                              "accepted_answer": None, "validation_errors": [],
                              "chat_messages": [
                                  {"role": "user", "scope": "question_help", "content": "Was bedeutet Sportverein?"},
                                  {"role": "assistant", "scope": "question_help", "content": "Ein Verein für den Sport.",
                                   "proposed_answer": {"normalized_value": "sports", "confidence": 0.8}}]}},
        {"case_id": "case1", "action": "send_conversation_message", "http_status": 200, "duration_ms": 5.0,
         "request_payload": {"text": "Welche Förderung passt?"},
         "response_payload": {"conversation": {"messages": [
             {"role": "user", "content": "Hallo?"},
             {"role": "assistant", "content": "Frühere Antwort."},
             {"role": "user", "content": "Welche Förderung passt?"},
             {"role": "assistant", "content": "Eine Landesförderung passt gut."}]}}},
        {"case_id": "case1", "action": "llm_recommendations", "http_status": 200, "duration_ms": 5.0,
         "request_payload": None,
         "response_payload": {"recommendations": [
             {"title": "Beiträge anpassen", "rationale": "Die Beiträge sind seit Jahren gleich.", "source": "llm"}]}},
        {"case_id": "case1", "action": "submit_answer", "http_status": 200, "duration_ms": 5.0,
         "request_payload": {"question_id": "founded_year", "value": 1980},
         "response_payload": {"assistant_message": "Danke.", "accepted_answer": {"normalized_value": 1980}}},
    ]
    report = build_report({"runs": [run], "cases": [case], "assertions": [], "events": events, "artifacts": []})
    assert "- submit_text_answer: HTTP 200 (5.0 ms)\n" \
           "  - Eingabe: Wir sind ein Sportverein.\n" \
           "  - Normalisiert: \"sports\" (Confidence 0.9)\n" \
           "- submit_text_answer: HTTP 200 (5.0 ms)\n" \
           "  - Eingabe: Keine Ahnung.\n" \
           "  - Rückfrage (ambiguous): Meinst du einen Sportverein?\n" in report
    assert "- question_help: HTTP 200 (5.0 ms)\n" \
           "  - Eingabe: Was bedeutet Sportverein?\n" \
           "  - Antwort: Ein Verein für den Sport.\n" \
           "  - Vorschlag: \"sports\" (Confidence 0.8)\n" in report
    steps, dialog = report.split("### Dialog")
    assert "In welchem Jahr" not in steps
    assert "Welche Art von Verein" not in steps
    assert "Frühere Antwort." not in report
    assert dialog.count("Meinst du einen Sportverein?") == 1
    assert "- send_conversation_message: HTTP 200 (5.0 ms)\n" \
           "  - Eingabe: Welche Förderung passt?\n" \
           "  - Antwort: Eine Landesförderung passt gut." in report
    assert "- llm_recommendations: HTTP 200 (5.0 ms)\n" \
           "  - Empfehlung: Beiträge anpassen: Die Beiträge sind seit Jahren gleich." in report
    assert "- submit_answer: HTTP 200 (5.0 ms)\n" \
           "  - Eingabe" not in report
    assert "Danke." not in report.split("### Dialog")[0]
    html_report = build_report({"runs": [run], "cases": [case], "assertions": [], "events": events,
                                "artifacts": []}, "html")
    assert "Eine Landesförderung passt gut." in html_report


def test_report_skips_llm_texts_without_response():
    from testsuite.reports import build_report
    run = {"id": "run1", "status": "error", "metadata": {}}
    case = {"id": "case1", "run_id": "run1", "scenario_id": "llm", "status": "error", "attempt": 1}
    events = [{"case_id": "case1", "action": "question_help", "http_status": None, "duration_ms": 1.0,
               "request_payload": {"question_id": "club_type", "text": "Hilfe?"},
               "response_payload": None, "error_code": "ConnectError"}]
    report = build_report({"runs": [run], "cases": [case], "assertions": [], "events": events, "artifacts": []})
    assert "  - Eingabe: Hilfe?\n" in report
    assert "Antwort:" not in report


def test_report_reconstructs_dialog_per_case():
    from testsuite.reports import build_report
    run = {"id": "run1", "status": "passed", "metadata": {}}
    case = {"id": "case1", "run_id": "run1", "scenario_id": "dialog", "status": "passed", "attempt": 1}
    q1 = {"question_id": "club_type", "text": "Welche Art von Verein ist es?",
          "options": [{"value": "sports", "label": "Sportverein"},
                      {"value": "culture", "label": "Kulturverein"}]}
    q2 = {"question_id": "founded_year", "text": "In welchem Jahr wurde der Verein gegründet?"}
    events = [
        {"case_id": "case1", "sequence_no": 1, "action": "domains", "http_status": 200, "duration_ms": 1.0,
         "request_payload": None, "response_payload": {"domains": []}},
        {"case_id": "case1", "sequence_no": 2, "action": "start_assessment", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {}, "response_payload": {"assistant_message": q1["text"], "current_question": q1,
                                                     "validation_errors": [], "errors": []}},
        {"case_id": "case1", "sequence_no": 3, "action": "question_help", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {"question_id": "club_type", "text": "Zählt ein Dachverband?"},
         "response_payload": {"assistant_message": q1["text"], "current_question": q1, "validation_errors": [],
                              "chat_messages": [{"role": "user", "scope": "question_help", "content": "Zählt ein Dachverband?"},
                                                {"role": "assistant", "scope": "question_help", "content": "Ja, meist schon."}]}},
        {"case_id": "case1", "sequence_no": 4, "action": "submit_text_answer", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {"question_id": "club_type", "text": "Wir machen Sport im Verein."},
         "response_payload": {"assistant_message": q2["text"], "current_question": q2, "validation_errors": [],
                              "accepted_answer": {"normalized_value": "sports", "confidence": 1.0}}},
        {"case_id": "case1", "sequence_no": 5, "action": "submit_answer", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {"question_id": "founded_year", "value": 1700},
         "response_payload": {"assistant_message": "Bitte korrigiere die Eingabe.", "current_question": q2,
                              "validation_errors": [{"field_key": "founded_year", "code": "out_of_range",
                                                     "message": "Das Gründungsjahr muss zwischen 1800 und 2026 liegen."}]}},
        {"case_id": "case1", "sequence_no": 6, "action": "submit_answer", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {"question_id": "founded_year", "value": 1980},
         "response_payload": {"assistant_message": "Bitte prüfe deine Angaben vor dem Abschluss.",
                              "current_question": None, "validation_errors": []}},
        {"case_id": "case1", "sequence_no": 7, "action": "complete", "http_status": 200, "duration_ms": 1.0,
         "request_payload": None,
         "response_payload": {"assistant_message": "Das Assessment ist abgeschlossen.", "current_question": None,
                              "validation_errors": [],
                              "result": {"reason_codes": ["OLD_STATUTES", "MISSING_BUDGET"],
                                         "recommendations": [{"recommendation_id": "club-consultation",
                                                              "title": "Vereinsberatung", "source": "rule"}]},
                              "report": {"format": "markdown", "content": "# Ergebnis\n\nEmpfehlungen: 1\n"}}},
        {"case_id": "case1", "sequence_no": 8, "action": "create_conversation", "http_status": 200, "duration_ms": 1.0,
         "request_payload": {"title": "Förderung"}, "response_payload": {"conversation": {"title": "Förderung", "messages": []}}},
        {"case_id": "case1", "sequence_no": 9, "action": "send_conversation_message", "http_status": 200,
         "duration_ms": 1.0, "request_payload": {"text": "Welche Förderung passt?"},
         "response_payload": {"conversation": {"title": "Förderung", "messages": [
             {"role": "user", "content": "Welche Förderung passt?"},
             {"role": "assistant", "content": "Eine Landesförderung."}]}}},
        {"case_id": "case1", "sequence_no": 10, "action": "llm_recommendations", "http_status": 200, "duration_ms": 1.0,
         "request_payload": None,
         "response_payload": {"recommendations": [{"title": "Beiträge", "rationale": "Alte Beiträge.", "source": "llm"}]}},
        {"case_id": "case1", "sequence_no": 11, "action": "llm_recommendations", "http_status": 409, "duration_ms": 1.0,
         "request_payload": None,
         "response_payload": {"ok": False, "recommendations": [],
                              "errors": [{"code": "invalid_state", "message": "Noch nicht abgeschlossen."}]}},
        {"case_id": "case1", "sequence_no": 12, "action": "get_session", "http_status": None, "duration_ms": 1.0,
         "request_payload": None, "response_payload": None, "error_code": "ConnectError"},
    ]
    report = build_report({"runs": [run], "cases": [case], "assertions": [], "events": events, "artifacts": []})
    expected = "\n".join([
        "### Dialog",
        "",
        "- GuideOS: Welche Art von Verein ist es? [Sportverein | Kulturverein]",
        "- Nutzer: Zählt ein Dachverband?",
        "- GuideOS: Ja, meist schon.",
        "- Nutzer: Wir machen Sport im Verein.",
        "- GuideOS: In welchem Jahr wurde der Verein gegründet?",
        "- Nutzer: founded_year = 1700",
        "- GuideOS: Das Gründungsjahr muss zwischen 1800 und 2026 liegen.",
        "- GuideOS: Bitte korrigiere die Eingabe.",
        "- Nutzer: founded_year = 1980",
        "- GuideOS: Bitte prüfe deine Angaben vor dem Abschluss.",
        "- Nutzer: (Abschluss)",
        "- GuideOS: Das Assessment ist abgeschlossen.",
        "- Ergebnis: Reason-Codes OLD_STATUTES, MISSING_BUDGET, Empfehlungen club-consultation (rule)",
        "- Konversation \"Förderung\" gestartet",
        "- Nutzer: Welche Förderung passt?",
        "- GuideOS: Eine Landesförderung.",
        "- Nutzer: (LLM-Empfehlungen angefordert)",
        "- GuideOS: Beiträge: Alte Beiträge.",
        "- Nutzer: (LLM-Empfehlungen angefordert)",
        "- GuideOS: Fehler invalid_state: Noch nicht abgeschlossen.",
        "- Fehler: get_session ohne Antwort (ConnectError)",
        "",
        "### Assessment-Report",
        "",
        "```markdown",
        "# Ergebnis",
        "",
        "Empfehlungen: 1",
        "```",
        "",
    ])
    assert expected in report, report
    assert report.index("- get_session: HTTP None") < report.index("### Dialog") < report.index("## Metadaten")


def test_report_dialog_shows_question_options():
    from testsuite.reports import dialog_lines
    event = {"action": "start_assessment", "request_payload": {},
             "response_payload": {"assistant_message": "Welche Art von Verein ist es?",
                                  "current_question": {"question_id": "club_type",
                                                       "text": "Welche Art von Verein ist es?",
                                                       "options": [{"value": "sports", "label": "Sportverein"},
                                                                   {"value": "social", "label": "Sozial- oder Hilfsverein"}]}}}
    assert dialog_lines(event) == ["- GuideOS: Welche Art von Verein ist es? [Sportverein | Sozial- oder Hilfsverein]"]
    event["response_payload"]["current_question"]["options"] = []
    assert dialog_lines(event) == ["- GuideOS: Welche Art von Verein ist es?"]


def test_each_case_runs_as_its_own_participant(tmp_path):
    """Cases of one run share the temporary API; the participant header
    keeps their sessions, profiles and conversations apart."""
    import httpx

    from testsuite.client import GuideOSClient
    from testsuite.repository import JsonlRepository
    from testsuite.runner import execute_run

    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("X-GuideOS-Participant")))
        if request.url.path == "/api/v1/domains":
            return httpx.Response(200, json=[{"domain_id": "verein-demo", "version": "1.0.0",
                                             "name": "Verein", "source": "bundled"}])
        return httpx.Response(200, json={"ok": True, "status": "in_progress", "session_id": "sess_1"})

    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"}])
    with GuideOSClient("http://api", transport=httpx.MockTransport(handler)) as client:
        run = execute_run([scenario], client, JsonlRepository(tmp_path), repeat=2)

    cases = JsonlRepository(tmp_path).read(run["id"])["cases"]
    participants = [case["participant_id"] for case in cases]
    assert len(set(participants)) == 2
    assert all(participant == case["id"] for participant, case in zip(participants, cases))
    headers_per_case = {}
    for path, participant in seen:
        headers_per_case.setdefault(participant, set()).add(path)
    assert set(headers_per_case) == set(participants)
    assert all("/api/v1/assessment-sessions" in paths for paths in headers_per_case.values())


def test_complete_with_knowledge_recommendations_through_rest(monkeypatch, tmp_path):
    from testsuite.runner import action_request
    provider = ScriptedProvider({"recommendations": [{
        "recommendation_id": "hydraulic-balancing", "title": "Hydraulischen Abgleich prüfen",
        "message": "Senkt den Verbrauch.", "rationale": "Gasheizung.", "evidence_fields": ["funding_source"],
        "confidence": 0.7}]})
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
    scenario = make_scenario(steps=[
        *COMPLETION_STEPS[:-1],
        {"id": "complete", "action": "complete", "payload": {"knowledge_recommendations": True},
         "assertions": [{"path": "/result/knowledge_recommendations", "op": "contains", "expected": {"source": "llm"}},
                        {"path": "/result/knowledge_generation/error_code", "op": "eq", "expected": None}]},
        {"id": "profile", "action": "get_profile",
         "assertions": [{"path": "/profile/result/knowledge_recommendations", "op": "contains",
                         "expected": {"source": "llm"}}]}])
    assert scenario.requires_llm is True
    assert action_request(scenario, scenario.steps[-2], "sess_1", {}) == (
        "POST", "/api/v1/assessment-sessions/sess_1/inputs", {"kind": "complete", "knowledge_recommendations": True})
    repo = JsonlRepository(tmp_path / "results")
    with api_client(tmp_path) as client:
        skipped = execute_run([scenario], client, repo)
        run = execute_run([scenario], client, repo, include_llm=True)
    assert repo.read(skipped["id"])["cases"][0]["status"] == "skipped"
    assert run["status"] == "passed", repo.read(run["id"])
    events = repo.read(run["id"])["events"]
    assert next(e for e in events if e["action"] == "complete")["request_payload"] == {
        "kind": "complete", "knowledge_recommendations": True}


def test_report_shows_knowledge_recommendations_generated_at_completion():
    from testsuite.reports import build_report, llm_text_lines
    run = {"id": "run1", "status": "passed", "metadata": {}}
    case = {"id": "case1", "run_id": "run1", "scenario_id": "llm", "status": "passed", "attempt": 1}
    generated = {"case_id": "case1", "action": "complete", "http_status": 200, "duration_ms": 5.0,
                 "request_payload": {"kind": "complete", "knowledge_recommendations": True},
                 "response_payload": {"status": "completed", "result": {
                     "recommendations": [{"title": "Ehrenamt sichern", "source": "rule"}],
                     "knowledge_recommendations": [
                         {"title": "Beiträge anpassen", "rationale": "Die Beiträge sind seit Jahren gleich.", "source": "llm"}]}}}
    deterministic = {**generated, "request_payload": {"kind": "complete"},
                     "response_payload": {"status": "completed", "result": {"recommendations": [],
                                                                            "knowledge_recommendations": []}}}
    assert llm_text_lines(generated) == ["  - Empfehlung: Beiträge anpassen: Die Beiträge sind seit Jahren gleich."]
    assert llm_text_lines(deterministic) == []
    report = build_report({"runs": [run], "cases": [case], "assertions": [], "events": [generated], "artifacts": []})
    assert "- complete: HTTP 200 (5.0 ms)\n" \
           "  - Empfehlung: Beiträge anpassen: Die Beiträge sind seit Jahren gleich." in report
    assert "Ehrenamt sichern" not in report.split("### Dialog")[0]


class SequencedProvider(ScriptedProvider):
    """Liefert je generate_structured-Aufruf das nächste Ergebnis, das letzte danach immer."""
    def __init__(self, results):
        super().__init__(results[-1])
        self.queue = list(results)

    def generate_structured(self, *args):
        self.result = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return super().generate_structured(*args)


def _year_scenario():
    from testsuite.loader import load_scenarios
    return next(s for s in load_scenarios() if s.id == "regression_year_two_digit_1962")


def _valid_year(value):
    return {"decision": "valid", "normalized_value": value, "confidence": 0.9, "reason_code": None, "follow_up": None}


def test_two_digit_year_scenario_accepts_follow_up_or_direct_value_but_not_a_wrong_year(monkeypatch, tmp_path):
    uncertain = {"decision": "uncertain", "normalized_value": None, "confidence": 0.3,
                 "reason_code": "ambiguous", "follow_up": "Welches Jahr genau?"}
    outcomes = {}
    for label, provider in [("follow_up", SequencedProvider([uncertain, _valid_year(1962)])),
                            ("direct", SequencedProvider([_valid_year(1962)])),
                            ("wrong_year", SequencedProvider([_valid_year(1960), _valid_year(1962)]))]:
        monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name, p=provider: p)
        repo = JsonlRepository(tmp_path / label)
        with api_client(tmp_path / label) as client:
            outcomes[label] = execute_run([_year_scenario()], client, repo, include_llm=True)["status"]
    assert outcomes == {"follow_up": "passed", "direct": "passed", "wrong_year": "failed"}


def test_submit_file_answer_sends_the_fixture_as_a_file_answer(tmp_path, monkeypatch):
    import pytest

    from testsuite import runner
    from testsuite.runner import action_request, read_dataset_fixture

    (tmp_path / "kassabuch.csv").write_text("datum;betrag\n2025-01-02;12,50\n", encoding="utf-8-sig")
    monkeypatch.setattr(runner, "DATASET_FIXTURE_DIRECTORY", tmp_path)
    scenario = make_scenario("file", domain_id="tools-demo", domain_version="1.0.0", steps=[
        {"id": "start", "action": "start_assessment"},
        {"id": "upload", "action": "submit_file_answer",
         "payload": {"question_id": "dataset_file", "fixture": "kassabuch.csv", "format_hint": "csv"}},
    ])
    assert scenario.requires_llm is False

    method, path, body = action_request(scenario, scenario.steps[1], "sess_1", {})

    assert (method, path) == ("POST", "/api/v1/assessment-sessions/sess_1/inputs")
    assert body["kind"] == "submit_answer" and body["question_id"] == "dataset_file"
    assert body["value"] == {"filename": "kassabuch.csv", "content": "datum;betrag\n2025-01-02;12,50\n",
                             "format_hint": "csv"}
    assert body["value"]["content"] == read_dataset_fixture("kassabuch.csv")  # BOM removed

    with pytest.raises(ValueError, match="Fixture"):
        action_request(scenario, scenario.steps[1].model_copy(update={"payload": {
            "question_id": "dataset_file", "fixture": "gibt-es-nicht.csv"}}), "sess_1", {})
