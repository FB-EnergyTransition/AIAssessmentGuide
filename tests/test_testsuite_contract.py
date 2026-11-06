import json

import pytest
from pydantic import ValidationError

from testsuite.models import Assertion, Scenario
from testsuite.loader import load_scenarios
from testsuite.evaluator import evaluate, resolve_pointer


def scenario(**changes):
    return {"schema_version": 1, "id": "example", "version": "1.0", "domain_id": "verein-demo",
            "domain_version": "1.0.0", "steps": [{"id": "start", "action": "start_assessment"}], **changes}


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"steps": []},
    {"steps": [{"id": "x", "action": "shell"}]},
    {"steps": [{"id": "x", "action": "start_assessment"}, {"id": "y", "action": "completion_chat", "payload": {"text": "x"}}]},
    {"steps": [{"id": "x", "action": "start_assessment"}, {"id": "x", "action": "get_session"}]},
    {"steps": [{"id": "x", "action": "submit_answer", "payload": {"value": 2}}]},
    {"steps": [{"id": "x", "action": "get_session"}]},
])
def test_invalid_scenarios_rejected(changes):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario(**changes))


def test_loader_rejects_duplicate_scenario_ids(tmp_path):
    for name in ["one", "two"]:
        (tmp_path / f"{name}.json").write_text(json.dumps(scenario()))
    with pytest.raises(ValueError, match="ID"):
        load_scenarios(tmp_path)


def test_loader_reads_subdirectories_and_rejects_duplicates_across_them(tmp_path):
    (tmp_path / "top.json").write_text(json.dumps(scenario(id="top")))
    (tmp_path / "energy").mkdir()
    (tmp_path / "energy" / "nested.json").write_text(json.dumps(scenario(id="nested")))

    assert [s.id for s in load_scenarios(tmp_path)] == ["nested", "top"]

    (tmp_path / "energy" / "copy.json").write_text(json.dumps(scenario(id="top")))
    with pytest.raises(ValueError, match="ID"):
        load_scenarios(tmp_path)


def test_pointer_escaping_arrays_and_missing():
    assert resolve_pointer({"a/b": {"~x": [None]}}, "/a~1b/~0x/0") is None
    with pytest.raises(LookupError):
        resolve_pointer({"items": []}, "/items/0")
    with pytest.raises(LookupError):
        resolve_pointer([1], "/-1")


@pytest.mark.parametrize("assertion,data,passed", [
    ({"path": "/ok", "op": "eq", "expected": True}, {"ok": True}, True),
    ({"path": "/ok", "op": "eq", "expected": True}, {"ok": 1}, False),
    ({"path": "/missing", "op": "eq", "expected": None}, {}, False),
    ({"path": "/report", "op": "exists"}, {"report": None}, True),
    ({"path": "/missing", "op": "exists", "expected": False}, {}, True),
    ({"path": "/items", "op": "contains", "expected": {"id": "x"}}, {"items": [{"id": "x", "priority": 2}]}, True),
    ({"path": "/score", "op": "between", "expected": [0, 100]}, {"score": 101}, False),
    ({"path": "/score", "op": "between", "expected": [0, 100]}, {"score": True}, False),
    ({"path": "/text", "op": "not_contains", "expected": "yes"}, {"text": "Ja, das zählt."}, True),
    ({"path": "/text", "op": "not_contains", "expected": "normierte Antwort"}, {"text": "Hier ist die normierte Antwort:"}, False),
    ({"path": "/items", "op": "not_contains", "expected": {"id": "x"}}, {"items": [{"id": "y"}]}, True),
    ({"path": "/items", "op": "not_contains", "expected": {"id": "x"}}, {"items": [{"id": "x", "n": 1}]}, False),
    ({"path": "/missing", "op": "not_contains", "expected": "x"}, {}, False),
    ({"path": "/text", "op": "min_length", "expected": 3}, {"text": "abc"}, True),
    ({"path": "/text", "op": "min_length", "expected": 4}, {"text": "abc"}, False),
    ({"path": "/items", "op": "min_length", "expected": 1}, {"items": []}, False),
    ({"path": "/n", "op": "min_length", "expected": 1}, {"n": 12345}, False),
])
def test_assertions(assertion, data, passed):
    assert evaluate(Assertion.model_validate(assertion), data)["passed"] is passed


@pytest.mark.parametrize("data", [
    {"path": "bad", "op": "eq", "expected": 1},
    {"path": "/x", "op": "between", "expected": [5, 1]},
    {"path": "/x", "op": "eq"},
    {"path": "/x", "op": "min_length", "expected": -1},
    {"path": "/x", "op": "min_length", "expected": "3"},
    {"path": "/x", "op": "not_contains", "expected": 3},
])
def test_invalid_assertions_rejected(data):
    with pytest.raises(ValidationError):
        Assertion.model_validate(data)


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_schema_version_requires_integer(version):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario(schema_version=version))


def test_nonfinite_json_values_rejected_before_run():
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario(steps=[{"id": "start", "action": "start_assessment"},
            {"id": "answer", "action": "submit_answer", "payload": {"question_id": "x", "value": float("nan")}}]))


@pytest.mark.parametrize("step", [
    {"id": "c", "action": "create_conversation"},
    {"id": "c", "action": "create_conversation", "payload": {"title": "Heizung"}},
    {"id": "c", "action": "get_conversation"},
    {"id": "c", "action": "list_conversations"},
    {"id": "c", "action": "delete_conversation"},
    {"id": "c", "action": "send_conversation_message", "payload": {"text": "Frage"}},
    {"id": "c", "action": "llm_recommendations"},
])
def test_conversation_and_recommendation_actions_are_accepted(step):
    loaded = Scenario.model_validate(scenario(steps=[{"id": "start", "action": "start_assessment"}, step]))
    assert loaded.requires_llm is (step["action"] in {"send_conversation_message", "llm_recommendations"})


@pytest.mark.parametrize("step", [
    {"id": "c", "action": "send_conversation_message"},
    {"id": "c", "action": "create_conversation", "payload": {"assessment_session_id": "sess"}},
    {"id": "c", "action": "get_conversation", "payload": {"text": "x"}},
    {"id": "c", "action": "llm_recommendations", "payload": {"text": "x"}},
])
def test_invalid_conversation_payloads_rejected(step):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario(steps=[{"id": "start", "action": "start_assessment"}, step]))


@pytest.mark.parametrize("payload, needs_llm", [
    ({"knowledge_recommendations": True}, True),
    ({"knowledge_recommendations": False}, False),
    ({}, False),
])
def test_complete_with_knowledge_recommendations_needs_a_model(payload, needs_llm):
    loaded = Scenario.model_validate(scenario(steps=[{"id": "start", "action": "start_assessment"},
        {"id": "done", "action": "complete", "payload": payload}]))
    assert loaded.requires_llm is needs_llm


@pytest.mark.parametrize("payload", [{"knowledge_recommendations": "ja"}, {"knowledge_recommendations": 1},
                                     {"endpoint": "http://x", "model": "m"}])
def test_complete_payload_only_takes_a_boolean_flag(payload):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario(steps=[{"id": "start", "action": "start_assessment"},
            {"id": "done", "action": "complete", "payload": payload}]))
