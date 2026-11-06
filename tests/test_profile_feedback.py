"""Feedback on recommendations (concept step 3): stored on the profile,
visible, and effective when knowledge recommendations are regenerated."""

import json

import pytest
from fastapi.testclient import TestClient

from guideos.assessment.models import AssessmentAnswer, AssessmentSession, KnowledgeGeneration, LLMRecommendation
from guideos.context import build_advisory_context
from guideos.context.prompt import ADVISORY_POLICY, build_advisory_messages
from guideos.domains.loader import load_domain_pack
from guideos.knowledge.models import SourceRef
from guideos.profiles.models import Profile
from guideos.profiles.repository import LocalProfileRepository
from guideos.profiles.service import ProfileService, ProfileServiceError
from guideos.recommendations.llm import LLM_RECOMMENDATION_POLICY
from guideos.recommendations.service import generate_profile_recommendations
from guideos.rules.engine import evaluate_assessment

ANSWERS = {
    "club_type": "sports", "founded_year": 1980, "members_count": 120,
    "funding_source": "public_grants", "volunteer_situation": "tight", "annual_budget_eur": None,
}
HEADER = "X-GuideOS-Participant"


def _item(recommendation_id, title):
    return LLMRecommendation(
        recommendation_id=recommendation_id, title=title, message="Text.", rationale="Grund.",
        evidence_fields=["funding_source"], sources=[SourceRef(kind="llm", title="KI-Fachwissen")],
    )


def _profile(participant_id="p1"):
    pack = load_domain_pack()
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    session.participant_id = participant_id
    for field_key, value in ANSWERS.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value), normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    session.result.knowledge_recommendations = [
        _item("hydraulic-balancing", "Hydraulischen Abgleich prüfen"),
        _item("solar-thermal", "Solarthermie prüfen"),
    ]
    session.result.knowledge_generation = KnowledgeGeneration(model="fake", prompt_version=2)
    return pack, Profile.from_session(session)


def _service(tmp_path):
    pack, profile = _profile()
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(profile)
    return ProfileService(profiles, load_pack=lambda domain_id, version: pack), profiles, pack


def test_feedback_is_stored_per_recommendation_and_replaced_on_repeat(tmp_path):
    service, profiles, _ = _service(tmp_path)

    rejected = service.set_feedback("verein-demo", "p1", "solar-thermal", "rejected", origin="button")
    accepted_rule = service.set_feedback("verein-demo", "p1", "volunteer-recruiting", "accepted", origin="chat")
    changed = service.set_feedback("verein-demo", "p1", "solar-thermal", "accepted", origin="chat")

    profile = profiles.get("verein-demo", "p1")
    assert set(profile.feedback) == {"solar-thermal", "volunteer-recruiting"}
    assert profile.feedback["solar-thermal"].stance == "accepted"
    assert profile.feedback["solar-thermal"].origin == "chat"
    assert profile.feedback["solar-thermal"].title == "Solarthermie prüfen"
    assert profile.feedback["volunteer-recruiting"].title == "Ehrenamt sichern"
    assert rejected.feedback["solar-thermal"].stance == "rejected"
    assert accepted_rule.feedback["volunteer-recruiting"].kind == "rule"
    assert changed.feedback["solar-thermal"].kind == "llm"
    assert [e.payload for e in profile.events if e.event_type == "recommendation_feedback"] == [
        {"recommendation_id": "solar-thermal", "stance": "rejected", "origin": "button"},
        {"recommendation_id": "volunteer-recruiting", "stance": "accepted", "origin": "chat"},
        {"recommendation_id": "solar-thermal", "stance": "accepted", "origin": "chat"},
    ]
    assert profile.revision == 4


def test_feedback_requires_a_known_recommendation(tmp_path):
    service, _, _ = _service(tmp_path)

    with pytest.raises(ProfileServiceError) as excinfo:
        service.set_feedback("verein-demo", "p1", "unknown-rec", "rejected", origin="button")

    assert excinfo.value.code == "invalid_recommendation"
    with pytest.raises(ProfileServiceError) as missing:
        service.set_feedback("verein-demo", "p2", "solar-thermal", "rejected", origin="button")
    assert missing.value.code == "profile_not_found"


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result):
        self.result = result
        self.messages = []

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.messages.append(messages)
        return self.result


def test_regeneration_keeps_accepted_drops_rejected_and_tells_the_model(tmp_path):
    service, profiles, pack = _service(tmp_path)
    service.set_feedback("verein-demo", "p1", "hydraulic-balancing", "accepted", origin="button")
    service.set_feedback("verein-demo", "p1", "solar-thermal", "rejected", origin="button")
    provider = FakeProvider({"recommendations": [
        {"recommendation_id": "solar-thermal", "title": "Solarthermie prüfen", "message": "Nochmal.",
         "rationale": "Gas.", "evidence_fields": ["funding_source"]},
        {"recommendation_id": "solar-heat", "title": "solarthermie prüfen", "message": "Gleicher Titel.",
         "rationale": "Gas.", "evidence_fields": ["funding_source"]},
        {"recommendation_id": "thermostats", "title": "Thermostatventile tauschen", "message": "Neu.",
         "rationale": "Gas.", "evidence_fields": ["funding_source"]},
    ]})

    items = generate_profile_recommendations(
        profiles, "verein-demo", "p1", provider, "http://fake", "fake",
        load_pack=lambda domain_id, version: pack,
    )

    assert [i.recommendation_id for i in items] == ["hydraulic-balancing", "thermostats"]
    profile = profiles.get("verein-demo", "p1")
    assert [i.recommendation_id for i in profile.result.knowledge_recommendations] == [
        "hydraulic-balancing", "thermostats"
    ]
    assert [(e.recommendation_id, e.trigger) for e in profile.result.excluded] == [
        ("solar-thermal", "user_feedback"), ("solar-heat", "user_feedback")
    ]
    payload = json.loads(provider.messages[0][1]["content"])
    assert payload["recommendation_feedback"] == [
        {"recommendation_id": "hydraulic-balancing", "title": "Hydraulischen Abgleich prüfen", "stance": "accepted"},
        {"recommendation_id": "solar-thermal", "title": "Solarthermie prüfen", "stance": "rejected"},
    ]
    assert "RECOMMENDATION_FEEDBACK" in LLM_RECOMMENDATION_POLICY
    assert "abgelehnt" in LLM_RECOMMENDATION_POLICY


def test_advisory_context_and_prompt_carry_the_feedback(tmp_path):
    service, profiles, pack = _service(tmp_path)
    service.set_feedback("verein-demo", "p1", "solar-thermal", "rejected", origin="button")
    profile = profiles.get("verein-demo", "p1")

    context = build_advisory_context(pack, profile)
    messages = build_advisory_messages(context, [], "Frage")

    assert [(f.recommendation_id, f.stance) for f in context.feedback] == [("solar-thermal", "rejected")]
    assert json.loads(messages[1]["content"])["recommendation_feedback"][0]["stance"] == "rejected"
    assert "RECOMMENDATION_FEEDBACK" in ADVISORY_POLICY


def test_rest_feedback_endpoint_and_runner_action(tmp_path):
    from guideos.adapters.fastapi import create_app
    from testsuite.models import LLM_ACTIONS
    from testsuite.runner import action_request

    from tests.test_testsuite_runner import make_scenario

    client = TestClient(create_app(
        tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json",
        knowledge_path=tmp_path / "knowledge.json", profile_path=tmp_path / "profiles.json",
    ))
    a = {HEADER: "participant-a"}
    session_id = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=a).json()["session_id"]
    for question_id, value in ANSWERS.items():
        client.post(f"/api/v1/assessment-sessions/{session_id}/inputs",
                    json={"kind": "submit_answer", "question_id": question_id, "value": value}, headers=a)
    client.post(f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}, headers=a)
    path = "/api/v1/profiles/verein-demo/recommendations/volunteer-recruiting/feedback"

    accepted = client.post(path, json={"stance": "accepted"}, headers=a).json()
    unknown = client.post("/api/v1/profiles/verein-demo/recommendations/nope/feedback", json={"stance": "rejected"}, headers=a).json()
    invalid = client.post(path, json={"stance": "maybe"}, headers=a).json()
    foreign = client.post(path, json={"stance": "accepted"}, headers={HEADER: "b"}).json()

    assert accepted["ok"] is True
    assert accepted["profile"]["feedback"]["volunteer-recruiting"]["stance"] == "accepted"
    assert accepted["profile"]["feedback"]["volunteer-recruiting"]["origin"] == "button"
    assert unknown["errors"][0]["code"] == "invalid_recommendation"
    assert invalid["errors"][0]["code"] == "invalid_input"
    assert foreign["errors"][0]["code"] == "profile_not_found"
    profile = client.get("/api/v1/profiles/verein-demo", headers=a).json()["profile"]
    assert "Vom Nutzer befürwortet" in profile["report"]["content"]

    scenario = make_scenario(steps=[
        {"id": "start", "action": "start_assessment"},
        {"id": "fb", "action": "recommendation_feedback", "payload": {"recommendation_id": "volunteer-recruiting", "stance": "rejected"}},
    ])
    assert "recommendation_feedback" not in LLM_ACTIONS
    assert action_request(scenario, scenario.steps[1], "sess_1", {}) == (
        "POST", "/api/v1/profiles/verein-demo/recommendations/volunteer-recruiting/feedback", {"stance": "rejected"}
    )


def test_policies_forbid_rejected_recommendations_under_another_name():
    """Decision 2026-10-12: a rejected recommendation must not come back
    reworded, under another name or as a step of another hint."""
    from guideos.recommendations.llm import PROMPT_VERSION

    for policy in (ADVISORY_POLICY, LLM_RECOMMENDATION_POLICY):
        assert "unter anderem Namen" in policy
        assert "Teil eines anderen Hinweises" in policy
    # The advisory rule belongs to the rules block, on its own line, not glued behind the JSON instruction.
    rules, _, answer_format = ADVISORY_POLICY.partition("Antwortformat:")
    assert "RECOMMENDATION_FEEDBACK" in rules and "RECOMMENDATION_FEEDBACK" not in answer_format
    assert "zurück.- " not in ADVISORY_POLICY
    assert PROMPT_VERSION >= 3


def test_rule_results_for_the_model_carry_the_user_feedback(tmp_path):
    """A rejected rule recommendation must not reach the model as an
    unqualified rule result; the payload marks it and the policy gives
    feedback precedence over rule results."""
    service, profiles, pack = _service(tmp_path)
    profile = profiles.get("verein-demo", "p1")
    rule_id = profile.result.recommendations[0].recommendation_id
    service.set_feedback("verein-demo", "p1", rule_id, "rejected", origin="button")
    context = build_advisory_context(pack, profiles.get("verein-demo", "p1"))

    payload = json.loads(build_advisory_messages(context, [], "Frage")[1]["content"])

    marked = {item["recommendation_id"]: item.get("user_feedback") for item in payload["rule_results"]["recommendations"]}
    assert marked[rule_id] == "rejected"
    assert all(stance is None for key, stance in marked.items() if key != rule_id)
    assert "RECOMMENDATION_FEEDBACK geht RULE_RESULTS vor" in ADVISORY_POLICY
