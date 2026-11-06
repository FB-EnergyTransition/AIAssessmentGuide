"""Changing single answers of a profile (concept step 3): deterministic
validation, visibility, open follow-up questions and rule recomputation."""

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession, KnowledgeGeneration, LLMRecommendation
from guideos.domains.loader import load_domain_pack, validate_domain_pack_data
from guideos.knowledge.models import SourceRef
from guideos.profiles.models import Profile
from guideos.profiles.repository import LocalProfileRepository
from guideos.profiles.service import ProfileService, ProfileServiceError
from guideos.rules.engine import evaluate_assessment

DEMO_ANSWERS = {
    "club_type": "sports", "founded_year": 1980, "members_count": 120,
    "funding_source": "public_grants", "volunteer_situation": "tight", "annual_budget_eur": None,
}

# A small pack with a follow-up question that is only visible for clubs
# with their own premises (visible_if), for the visibility and open-question
# mechanics of profile changes; shared with test_profile_chat.
VISIBILITY_PACK_DATA = {
    "domain_id": "verein-raeume-demo", "version": "1.0.0", "name": "Vereinsräume Demo",
    "description": "Testpack mit einer sichtbarkeitsabhängigen Folgefrage.", "default_locale": "de",
    "questions": [
        {"question_id": "club_type", "field_key": "club_type", "text": "Welche Art von Verein ist es?",
         "field_type": "select", "required": True,
         "options": [{"value": "sports", "label": "Sportverein"}, {"value": "culture", "label": "Kulturverein"}]},
        {"question_id": "premises", "field_key": "premises",
         "text": "Nutzt der Verein eigene oder gemietete Räume?", "field_type": "select", "required": True,
         "options": [{"value": "owned", "label": "Eigene Räume"}, {"value": "rented", "label": "Gemietet"}],
         "help_text": "Die Angabe entscheidet, ob Fragen zum Vereinshaus gestellt werden."},
        {"question_id": "premises_age_years", "field_key": "premises_age_years",
         "text": "Wie alt ist das Vereinshaus ungefähr in Jahren?", "field_type": "integer", "required": True,
         "allow_unknown": True, "validation": {"minimum": 0, "maximum": 200},
         "visible_if": {"field_key": "premises", "operator": "equals", "value": "owned"},
         "help_text": "Das Baujahr des Vereinshauses steht meist im Grundbuch oder in den Unterlagen des Vorstands."},
        {"question_id": "funding_source", "field_key": "funding_source", "text": "Woher stammt der Großteil der Einnahmen?",
         "field_type": "select", "required": True,
         "options": [{"value": "membership_fees", "label": "Mitgliedsbeiträge"},
                     {"value": "public_grants", "label": "Öffentliche Förderungen"}]},
        {"question_id": "annual_budget_eur", "field_key": "annual_budget_eur",
         "text": "Wie hoch sind die jährlichen Ausgaben des Vereins in Euro?", "field_type": "number",
         "required": True, "allow_unknown": True, "validation": {"minimum": 0, "maximum": 10000000}},
    ],
    "rules": [
        {"rule_id": "old-premises", "field_key": "premises_age_years", "operator": "gte", "value": 50,
         "reason_code": "OLD_PREMISES", "recommendation_ids": ["premises-check"]},
        {"rule_id": "grants-dependent", "field_key": "funding_source", "operator": "in", "value": ["public_grants"],
         "reason_code": "GRANTS_DEPENDENT", "recommendation_ids": ["funding-diversification"]},
        {"rule_id": "missing-budget", "field_key": "annual_budget_eur", "operator": "is_missing",
         "reason_code": "MISSING_BUDGET", "recommendation_ids": ["collect-budget"]},
    ],
    "recommendations": [
        {"recommendation_id": "collect-budget", "priority": 1, "title": "Finanzdaten erfassen",
         "message": "Erfasse die jährlichen Ausgaben."},
        {"recommendation_id": "funding-diversification", "priority": 2, "title": "Einnahmen breiter aufstellen",
         "message": "Prüfe weitere Einnahmequellen."},
        {"recommendation_id": "premises-check", "priority": 3, "title": "Vereinshaus prüfen lassen",
         "message": "Lass den Zustand des Vereinshauses begutachten."},
    ],
}
VISIBILITY_ANSWERS = {
    "club_type": "sports", "premises": "owned", "premises_age_years": 20,
    "funding_source": "public_grants", "annual_budget_eur": 18000,
}


def visibility_pack():
    return validate_domain_pack_data(VISIBILITY_PACK_DATA)

def _profile(pack, answers, participant_id="p1"):
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    session.participant_id = participant_id
    for field_key, value in answers.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return Profile.from_session(session)


def _service(tmp_path, pack=None, answers=None):
    pack = pack or load_domain_pack()
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profile = _profile(pack, answers or DEMO_ANSWERS)
    profile.result.knowledge_recommendations = [
        LLMRecommendation(
            recommendation_id="fee-review", title="Mitgliedsbeiträge prüfen",
            message="Verringert die Abhängigkeit von Förderungen.", rationale="Förderabhängig.",
            evidence_fields=["funding_source"],
            sources=[SourceRef(kind="llm", title="KI-Fachwissen")],
        )
    ]
    profile.result.knowledge_generation = KnowledgeGeneration(model="fake", prompt_version=2)
    profiles.save(profile)
    return ProfileService(profiles, load_pack=lambda domain_id, version: pack), profiles


def test_update_fact_validates_writes_and_recomputes_rules(tmp_path):
    service, profiles = _service(tmp_path)
    before = profiles.get("verein-demo", "p1")
    assert "GRANTS_DEPENDENT" in before.result.reason_codes

    update = service.update_fact("verein-demo", "p1", "funding_source", "membership_fees", raw_answer="Mitgliedsbeiträge")

    profile = profiles.get("verein-demo", "p1")
    answer = profile.answers["funding_source"]
    assert (answer.normalized_value, answer.raw_answer, answer.origin) == ("membership_fees", "Mitgliedsbeiträge", "chat_update")
    assert answer.updated_at > before.answers["funding_source"].updated_at
    assert profile.answers["founded_year"].origin == "assessment"
    assert "GRANTS_DEPENDENT" not in profile.result.reason_codes
    assert [r.recommendation_id for r in profile.result.recommendations] == [
        r.recommendation_id for r in update.profile.result.recommendations
    ]
    assert update.changed_field == "funding_source" and update.previous_value == "public_grants"
    assert update.result_changed is True and update.open_questions == []
    assert profile.revision == before.revision + 1
    assert [e.event_type for e in profile.events][-2:] == ["fact_updated", "result_recomputed"]
    assert profile.events[-2].payload == {"field_key": "funding_source", "origin": "chat_update"}
    assert profile.events[-1].payload == {"trigger": "fact_updated"}
    # Knowledge recommendations stay, marked as predating the change.
    assert [r.recommendation_id for r in profile.result.knowledge_recommendations] == ["fee-review"]
    assert profile.result.knowledge_generation.model == "fake"
    assert profile.knowledge_stale is True


def test_update_fact_accepts_display_labels_and_unknown(tmp_path):
    service, profiles = _service(tmp_path)

    service.update_fact("verein-demo", "p1", "funding_source", "Mitgliedsbeiträge")
    update = service.update_fact("verein-demo", "p1", "annual_budget_eur", 15000)

    assert profiles.get("verein-demo", "p1").answers["funding_source"].normalized_value == "membership_fees"
    assert update.profile.answers["annual_budget_eur"].normalized_value == 15000
    assert "MISSING_BUDGET" not in update.profile.result.reason_codes


def test_update_fact_rejects_invalid_values_without_changing_the_profile(tmp_path):
    service, profiles = _service(tmp_path)
    before = profiles.get("verein-demo", "p1")

    with pytest.raises(ProfileServiceError) as excinfo:
        service.update_fact("verein-demo", "p1", "founded_year", 1500)

    assert excinfo.value.code == "validation_failed"
    assert excinfo.value.validation_code == "below_minimum"
    assert "mindestens" in str(excinfo.value)
    assert profiles.get("verein-demo", "p1") == before


def test_update_fact_rejects_unknown_hidden_fields_and_missing_profiles(tmp_path):
    service, profiles = _service(tmp_path)

    with pytest.raises(ProfileServiceError) as unknown:
        service.update_fact("verein-demo", "p1", "roof_colour", "rot")
    with pytest.raises(ProfileServiceError) as missing:
        service.update_fact("andere-demo", "p1", "funding_source", "public_grants")
    with pytest.raises(ProfileServiceError) as foreign:
        service.update_fact("verein-demo", "p2", "funding_source", "public_grants")

    assert unknown.value.code == "invalid_question"
    assert missing.value.code == "profile_not_found"
    assert foreign.value.code == "profile_not_found"


def test_update_fact_handles_visibility_and_reports_open_questions(tmp_path):
    pack = visibility_pack()
    service, profiles = _service(tmp_path, pack, VISIBILITY_ANSWERS)

    became_tenant = service.update_fact("verein-raeume-demo", "p1", "premises", "rented")

    assert "premises_age_years" not in became_tenant.profile.answers
    assert became_tenant.open_questions == []
    with pytest.raises(ProfileServiceError) as hidden:
        service.update_fact("verein-raeume-demo", "p1", "premises_age_years", 5)
    assert hidden.value.code == "invalid_question"

    became_owner = service.update_fact("verein-raeume-demo", "p1", "premises", "owned")

    assert [q.field_key for q in became_owner.open_questions] == ["premises_age_years"]
    assert service.open_questions("verein-raeume-demo", "p1") == became_owner.open_questions
    answered = service.update_fact("verein-raeume-demo", "p1", "premises_age_years", 25)
    assert answered.open_questions == []
    assert "premises_age_years" in [e.payload.get("field_key") for e in answered.profile.events]


def test_update_fact_rejects_a_changed_domain_pack(tmp_path):
    service, profiles = _service(tmp_path)
    profile = profiles.get("verein-demo", "p1")
    profile.domain_content_hash = "b" * 64
    profiles.save(profile)

    with pytest.raises(ProfileServiceError) as excinfo:
        service.update_fact("verein-demo", "p1", "funding_source", "membership_fees")

    assert excinfo.value.code == "domain_version_unavailable"


# --- REST and test-runner -------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

HEADER = "X-GuideOS-Participant"


def _client(tmp_path):
    from guideos.adapters.fastapi import create_app

    return TestClient(create_app(
        tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json",
        knowledge_path=tmp_path / "knowledge.json", profile_path=tmp_path / "profiles.json",
    ))


def _complete(client, headers):
    session_id = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=headers).json()["session_id"]
    inputs = f"/api/v1/assessment-sessions/{session_id}/inputs"
    for question_id, value in DEMO_ANSWERS.items():
        client.post(inputs, json={"kind": "submit_answer", "question_id": question_id, "value": value}, headers=headers)
    assert client.post(inputs, json={"kind": "complete"}, headers=headers).json()["status"] == "completed"


def test_rest_updates_a_profile_fact_and_reports_the_outcome(tmp_path):
    client = _client(tmp_path)
    a = {HEADER: "participant-a"}
    _complete(client, a)
    facts = "/api/v1/profiles/verein-demo/facts"

    changed = client.post(facts, json={"field_key": "funding_source", "value": "Mitgliedsbeiträge"}, headers=a).json()
    invalid = client.post(facts, json={"field_key": "founded_year", "value": 1500}, headers=a).json()
    unknown = client.post(facts, json={"field_key": "roof_colour", "value": "rot"}, headers=a).json()
    malformed = client.post(facts, json={"value": 1}, headers=a).json()
    foreign = client.post(facts, json={"field_key": "funding_source", "value": "public_grants"}, headers={HEADER: "b"}).json()
    profile = client.get("/api/v1/profiles/verein-demo", headers=a).json()["profile"]

    assert changed["ok"] is True
    assert changed["changed_field"] == "funding_source" and changed["previous_value"] == "public_grants"
    assert changed["result_changed"] is True and changed["open_questions"] == []
    assert changed["profile"]["answers"]["funding_source"]["display_value"] == "Mitgliedsbeiträge"
    assert changed["profile"]["answers"]["funding_source"]["origin"] == "chat_update"
    assert "GRANTS_DEPENDENT" not in changed["profile"]["result"]["reason_codes"]
    assert changed["profile"]["knowledge_stale"] is False  # nothing generated yet
    assert invalid["ok"] is False
    assert invalid["errors"][0]["code"] == "validation_failed"
    assert invalid["errors"][0]["validation_code"] == "below_minimum"
    assert unknown["errors"][0]["code"] == "invalid_question"
    assert malformed["errors"][0]["code"] == "invalid_input"
    assert foreign["errors"][0]["code"] == "profile_not_found"
    assert profile["revision"] == 2 and profile["answers"]["funding_source"]["normalized_value"] == "membership_fees"
    assert "Mitgliedsbeiträge (geändert am" in profile["report"]["content"]


def test_runner_maps_update_profile_fact_to_the_facts_resource():
    from testsuite.models import LLM_ACTIONS
    from testsuite.runner import action_request

    from tests.test_testsuite_runner import make_scenario

    scenario = make_scenario(steps=[
        {"id": "start", "action": "start_assessment"},
        {"id": "change", "action": "update_profile_fact", "payload": {"field_key": "funding_source", "value": "membership_fees"}},
    ])

    assert "update_profile_fact" not in LLM_ACTIONS
    assert action_request(scenario, scenario.steps[1], "sess_1", {}) == (
        "POST", "/api/v1/profiles/verein-demo/facts", {"field_key": "funding_source", "value": "membership_fees"}
    )
