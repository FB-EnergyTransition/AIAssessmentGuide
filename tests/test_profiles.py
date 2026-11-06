"""Profiles: one per participant and domain, created from a completed
session (concept step 2a)."""

import json
import os
from uuid import uuid4

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.domains.loader import load_domain_pack
from guideos.profiles.models import Profile
from guideos.profiles.repository import (
    LocalProfileRepository,
    ParticipantProfileRepository,
    ProfileConflictError,
    ProfileRepository,
    ProfileRepositoryError,
)
from guideos.rules.engine import evaluate_assessment

ANSWERS = {
    "club_type": "sports",
    "founded_year": 1980,
    "members_count": 120,
    "funding_source": "public_grants",
    "volunteer_situation": "tight",
    "annual_budget_eur": None,
}


def _completed_session(pack=None, participant_id=None, **overrides):
    pack = pack or load_domain_pack()
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    session.participant_id = participant_id
    for field_key, value in {**ANSWERS, **overrides}.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return session


def test_profile_is_created_from_a_completed_session():
    session = _completed_session(participant_id="p1")

    profile = Profile.from_session(session)

    assert profile.profile_id.startswith("prof_")
    assert (profile.participant_id, profile.domain_id) == ("p1", "verein-demo")
    assert profile.domain_version == session.domain_version
    assert profile.domain_content_hash == session.domain_content_hash
    assert profile.source_session_id == session.session_id
    assert profile.revision == 0
    assert set(profile.answers) == set(ANSWERS)
    assert profile.answers["funding_source"].normalized_value == "public_grants"
    assert profile.answers["funding_source"].origin == "assessment"
    assert profile.result == session.result
    assert [e.event_type for e in profile.events] == ["created_from_session"]
    assert profile.events[0].payload == {"session_id": session.session_id}


def test_profile_requires_a_completed_session_with_result():
    session = _completed_session()
    session.status = "review"

    with pytest.raises(ValueError, match="abgeschlossen"):
        Profile.from_session(session)


def test_replacing_a_profile_keeps_its_id_and_records_the_old_session():
    first = _completed_session(participant_id="p1")
    profile = Profile.from_session(first)
    profile.revision = 3
    second = _completed_session(participant_id="p1", funding_source="membership_fees")

    replaced = profile.replace_with(second)

    assert replaced is profile
    assert profile.revision == 3
    assert profile.source_session_id == second.session_id
    assert profile.answers["funding_source"].normalized_value == "membership_fees"
    assert profile.result == second.result
    assert [e.event_type for e in profile.events] == ["created_from_session", "replaced_by_session"]
    assert profile.events[-1].payload == {
        "previous_session_id": first.session_id, "session_id": second.session_id,
    }
    assert profile.updated_at >= profile.created_at


def test_replace_rejects_a_session_of_another_domain_or_participant():
    profile = Profile.from_session(_completed_session(participant_id="p1"))

    with pytest.raises(ValueError, match="Dom"):
        profile.replace_with(_completed_session(load_domain_pack(), participant_id="p1").model_copy(update={"domain_id": "other"}))
    with pytest.raises(ValueError, match="Teilnehmer"):
        profile.replace_with(_completed_session(participant_id="p2"))


def test_local_repository_satisfies_the_protocol(tmp_path):
    assert isinstance(LocalProfileRepository(tmp_path / "profiles.json"), ProfileRepository)


def test_local_repository_roundtrips_one_profile_per_participant_and_domain(tmp_path):
    repository = LocalProfileRepository(tmp_path / "profiles.json")
    profile = Profile.from_session(_completed_session(participant_id="p1"))

    repository.save(profile)

    stored = repository.get("verein-demo", "p1")
    assert stored.profile_id == profile.profile_id and stored.revision == 1
    assert stored.answers["funding_source"].normalized_value == "public_grants"
    assert repository.list("p1") == [stored]
    assert repository.list("p2") == []
    with pytest.raises(LookupError, match="Profil"):
        repository.get("verein-demo", "p2")
    with pytest.raises(LookupError):
        repository.get("andere-demo", "p1")
    data = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["profiles"]) == 1


def test_local_repository_rejects_a_second_profile_for_the_same_pair(tmp_path):
    repository = LocalProfileRepository(tmp_path / "profiles.json")
    repository.save(Profile.from_session(_completed_session(participant_id="p1")))

    with pytest.raises(ProfileConflictError):
        repository.save(Profile.from_session(_completed_session(participant_id="p1")))


def test_local_repository_updates_with_optimistic_concurrency(tmp_path):
    repository = LocalProfileRepository(tmp_path / "profiles.json")
    profile = Profile.from_session(_completed_session(participant_id="p1"))
    repository.save(profile)
    stale = repository.get("verein-demo", "p1")
    current = repository.get("verein-demo", "p1")

    repository.save(current)

    assert repository.get("verein-demo", "p1").revision == 2
    with pytest.raises(ProfileConflictError):
        repository.save(stale)


def test_local_repository_deletes_and_rejects_bad_files(tmp_path):
    repository = LocalProfileRepository(tmp_path / "profiles.json")
    repository.save(Profile.from_session(_completed_session(participant_id="p1")))

    repository.delete("verein-demo", "p1")

    assert repository.list() == []
    with pytest.raises(LookupError):
        repository.delete("verein-demo", "p1")
    (tmp_path / "profiles.json").write_text('{"version": 99, "profiles": []}', encoding="utf-8")
    with pytest.raises(ProfileRepositoryError, match="Version"):
        repository.list()
    (tmp_path / "profiles.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ProfileRepositoryError, match="JSON"):
        repository.list()


def test_local_repository_without_participant_keeps_one_profile_per_domain(tmp_path):
    repository = LocalProfileRepository(tmp_path / "profiles.json")
    repository.save(Profile.from_session(_completed_session()))

    assert repository.get("verein-demo").participant_id is None
    assert len(repository.list()) == 1


def test_participant_repository_scopes_every_call(tmp_path):
    base = LocalProfileRepository(tmp_path / "profiles.json")
    first = ParticipantProfileRepository(base, "p1")
    second = ParticipantProfileRepository(base, "p2")
    profile = Profile.from_session(_completed_session())

    first.save(profile)

    assert profile.participant_id == "p1"
    assert first.get("verein-demo").profile_id == profile.profile_id
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get("verein-demo")
    with pytest.raises(LookupError):
        second.delete("verein-demo")
    with pytest.raises(LookupError):
        first.save(Profile.from_session(_completed_session(participant_id="p2")))


POSTGRES_URL = os.environ.get("GUIDEOS_TEST_POSTGRES_URL")


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="GUIDEOS_TEST_POSTGRES_URL is required for PostgreSQL integration tests.",
)
def test_postgres_repository_roundtrips_and_isolates_participants():
    from guideos.assessment.migrations import migrate_database
    from guideos.profiles.postgres_repository import PostgresProfileRepository

    migrate_database(POSTGRES_URL)
    base = PostgresProfileRepository(POSTGRES_URL)
    first = ParticipantProfileRepository(base, f"first_{uuid4().hex}")
    second = ParticipantProfileRepository(base, f"second_{uuid4().hex}")
    profile = Profile.from_session(_completed_session())

    first.save(profile)
    stale = first.get("verein-demo")
    first.save(first.get("verein-demo"))

    assert first.get("verein-demo").revision == 2
    assert [p.profile_id for p in first.list()] == [profile.profile_id]
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get("verein-demo")
    with pytest.raises(ProfileConflictError):
        first.save(stale)
    with pytest.raises(ProfileConflictError):
        first.save(Profile.from_session(_completed_session()))
    first.delete("verein-demo")
    with pytest.raises(LookupError):
        first.get("verein-demo")


def test_factory_picks_local_repository_without_database(tmp_path, monkeypatch):
    from guideos.profiles.factory import profile_repository_from_env

    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "p.json"))

    repository = profile_repository_from_env()

    assert isinstance(repository, LocalProfileRepository)
    assert repository.path == tmp_path / "p.json"


def test_purge_removes_profiles_too():
    from guideos.pilot.purge_data import purge_pilot_data

    class FakeConnection:
        statements = []

        def execute(self, statement, *args):
            self.statements.append(" ".join(statement.split()))

    connection = FakeConnection()
    purge_pilot_data(connection)

    assert "DELETE FROM profiles" in connection.statements


# --- creation at completion -------------------------------------------------

import logging  # noqa: E402

from guideos.assessment.orchestrator import AssessmentOrchestrator  # noqa: E402
from guideos.assessment.repository import LocalAssessmentRepository  # noqa: E402


def _orchestrator(tmp_path, profiles=None):
    return AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        profiles=profiles,
    )


def _complete(orchestrator, **overrides):
    response = orchestrator.start()
    for question_id, value in {**ANSWERS, **overrides}.items():
        orchestrator.submit(response.session_id, question_id, value)
    return orchestrator.complete(response.session_id)


def test_completion_creates_the_profile_of_the_domain(tmp_path, caplog):
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = _orchestrator(tmp_path, profiles)

    with caplog.at_level(logging.INFO, logger="guideos"):
        completed = _complete(orchestrator)

    profile = profiles.get("verein-demo")
    assert profile.source_session_id == completed.session_id
    assert profile.result == completed.result
    assert profile.answers["funding_source"].normalized_value == "public_grants"
    assert profile.revision == 1
    session = orchestrator.repository.get(completed.session_id)
    assert session.status == "completed"
    assert any(e.event_type == "profile_created" and e.payload == {"profile_id": profile.profile_id}
               for e in session.events)
    assert any(f"profile_created session_id={completed.session_id} profile_id={profile.profile_id}" in r.getMessage()
               for r in caplog.records)


def test_second_completion_replaces_the_profile_and_keeps_its_id(tmp_path):
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = _orchestrator(tmp_path, profiles)
    first = _complete(orchestrator)
    profile_id = profiles.get("verein-demo").profile_id

    second = _complete(orchestrator, funding_source="membership_fees")

    profile = profiles.get("verein-demo")
    assert profile.profile_id == profile_id
    assert profile.source_session_id == second.session_id
    assert profile.answers["funding_source"].normalized_value == "membership_fees"
    assert profile.revision == 2
    assert [e.event_type for e in profile.events] == ["created_from_session", "replaced_by_session"]
    assert profile.events[-1].payload["previous_session_id"] == first.session_id
    assert len(profiles.list()) == 1
    session = orchestrator.repository.get(second.session_id)
    assert any(e.event_type == "profile_replaced" for e in session.events)
    # Both sessions stay as history.
    assert orchestrator.repository.get(first.session_id).status == "completed"


def test_completion_without_profile_repository_changes_nothing(tmp_path):
    completed = _complete(_orchestrator(tmp_path))

    assert completed.status == "completed"
    assert not (tmp_path / "profiles.json").exists()


def test_profile_is_scoped_to_the_participant(tmp_path):
    from guideos.assessment.repository import ParticipantAssessmentRepository

    base_profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        ParticipantAssessmentRepository(LocalAssessmentRepository(tmp_path / "a.json"), "p1"),
        profiles=ParticipantProfileRepository(base_profiles, "p1"),
    )

    _complete(orchestrator)

    assert base_profiles.get("verein-demo", "p1").participant_id == "p1"
    assert base_profiles.list("p2") == []


# --- profile report ---------------------------------------------------------


def test_profile_report_lists_answers_with_origin_and_both_recommendation_kinds():
    from datetime import UTC, datetime

    from guideos.assessment.models import KnowledgeGeneration, LLMRecommendation
    from guideos.knowledge.models import SourceRef
    from guideos.reports.renderer import render_profile_report

    pack = load_domain_pack()
    session = _completed_session()
    session.result.knowledge_recommendations = [
        LLMRecommendation(
            recommendation_id="fee-schedule", title="Beitragsordnung überarbeiten",
            message="Gestaffelte Beiträge prüfen.", rationale="Förderabhängig.",
            evidence_fields=["funding_source"], sources=[SourceRef(kind="llm", title="KI-Fachwissen")],
        )
    ]
    session.result.knowledge_generation = KnowledgeGeneration(model="fake", prompt_version=1)
    profile = Profile.from_session(session)
    profile.answers["funding_source"].origin = "chat_update"
    profile.answers["funding_source"].updated_at = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)

    report = render_profile_report(pack, profile)

    content = report.content
    assert content.startswith("# Vereins-Check Demo")
    assert "keine verbindliche Beratung" in content
    assert f"Profil: `{profile.profile_id}`" in content
    assert f"Domain-Version: `{pack.domain_id}/{pack.version}`" in content
    assert f"Quelle: Assessment `{session.session_id}`" in content
    assert "- **Woher stammt der Großteil der Einnahmen?**: Öffentliche Förderungen (geändert am 03.11.2026)" in content
    assert "- **In welchem Jahr wurde der Verein gegründet?**: 1980\n" in content
    assert "## Empfehlungen" in content and "Ehrenamt sichern" in content
    assert "## Hinweise aus Wissensquellen (KI-formuliert)" in content
    assert "Beitragsordnung überarbeiten" in content


# --- REST ------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

HEADER = "X-GuideOS-Participant"


def _rest_client(tmp_path):
    from guideos.adapters.fastapi import create_app

    return TestClient(
        create_app(
            tmp_path / "assessments.json",
            conversation_path=tmp_path / "conversations.json",
            knowledge_path=tmp_path / "knowledge.json",
            profile_path=tmp_path / "profiles.json",
        )
    )


def _rest_complete(client, headers=None):
    session_id = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=headers
    ).json()["session_id"]
    inputs = f"/api/v1/assessment-sessions/{session_id}/inputs"
    for question_id, value in ANSWERS.items():
        client.post(inputs, json={"kind": "submit_answer", "question_id": question_id, "value": value},
                    headers=headers)
    assert client.post(inputs, json={"kind": "complete"}, headers=headers).json()["status"] == "completed"
    return session_id


def test_rest_profile_is_absent_before_and_present_after_completion(tmp_path):
    client = _rest_client(tmp_path)

    assert client.get("/api/v1/profiles").json() == {"ok": True, "profile": None, "profiles": [], "errors": []}
    missing = client.get("/api/v1/profiles/verein-demo").json()
    assert missing["ok"] is False and missing["errors"][0]["code"] == "profile_not_found"

    session_id = _rest_complete(client)
    body = client.get("/api/v1/profiles/verein-demo").json()

    assert body["ok"] is True
    profile = body["profile"]
    assert profile["profile_id"].startswith("prof_")
    assert profile["domain_id"] == "verein-demo" and profile["source_session_id"] == session_id
    assert profile["revision"] == 1
    assert profile["answers"]["funding_source"] == {
        "question_id": "funding_source", "normalized_value": "public_grants", "display_value": "Öffentliche Förderungen",
        "label": "Woher stammt der Großteil der Einnahmen?",
        "origin": "assessment", "calculation_id": None,
        "updated_at": profile["answers"]["funding_source"]["updated_at"],
    }
    assert {r["source"] for r in profile["result"]["recommendations"]} == {"rule"}
    assert profile["result"]["knowledge_recommendations"] == []
    assert profile["report"]["format"] == "markdown"
    assert f"Profil: `{profile['profile_id']}`" in profile["report"]["content"]
    listed = client.get("/api/v1/profiles").json()
    assert [p["profile_id"] for p in listed["profiles"]] == [profile["profile_id"]]
    assert listed["profiles"][0]["report"] is None


def test_rest_profiles_are_scoped_by_participant_and_deletable(tmp_path):
    client = _rest_client(tmp_path)
    a, b = {HEADER: "participant-a"}, {HEADER: "participant-b"}
    _rest_complete(client, headers=a)

    assert client.get("/api/v1/profiles/verein-demo", headers=a).json()["ok"] is True
    assert client.get("/api/v1/profiles/verein-demo", headers=b).json()["ok"] is False
    assert client.get("/api/v1/profiles", headers=b).json()["profiles"] == []
    assert client.delete("/api/v1/profiles/verein-demo", headers=b).json()["ok"] is False
    session_a = _rest_complete(client, headers=a)
    session_b = _rest_complete(client, headers=b)
    deleted = client.delete("/api/v1/profiles/verein-demo", headers=a).json()
    assert deleted["ok"] is True
    assert client.get("/api/v1/profiles/verein-demo", headers=a).json()["ok"] is False
    assert len(client.get("/api/v1/profiles", headers=a).json()["profiles"]) == 0
    # Deleting the profile also removes the participant's sessions of the domain, nobody else's.
    gone = client.get(f"/api/v1/assessment-sessions/{session_a}", headers=a).json()
    assert gone["ok"] is False and gone["errors"][0]["code"] == "session_not_found"
    assert client.get(f"/api/v1/assessment-sessions/{session_b}", headers=b).json()["ok"] is True
    assert client.get("/api/v1/profiles/verein-demo", headers=b).json()["ok"] is True


def test_rest_second_completion_replaces_the_profile(tmp_path):
    client = _rest_client(tmp_path)
    first = _rest_complete(client)
    profile_id = client.get("/api/v1/profiles/verein-demo").json()["profile"]["profile_id"]

    second = _rest_complete(client)
    profile = client.get("/api/v1/profiles/verein-demo").json()["profile"]

    assert profile["profile_id"] == profile_id
    assert profile["source_session_id"] == second != first
    assert profile["revision"] == 2


def test_runner_maps_get_profile_to_the_profile_resource():
    from testsuite.runner import action_request

    from tests.test_testsuite_runner import make_scenario

    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
                                    {"id": "profile", "action": "get_profile"}])
    step = scenario.steps[1]

    assert action_request(scenario, step, None, {}) == ("GET", "/api/v1/profiles/verein-demo", None)
    assert action_request(scenario, step, "sess_1", {}) == ("GET", "/api/v1/profiles/verein-demo", None)


# --- advisory context and knowledge recommendations on the profile -------


def test_advisory_context_is_built_from_a_profile():
    from guideos.context import build_advisory_context

    pack = load_domain_pack()
    session = _completed_session(participant_id="p1")
    profile = Profile.from_session(session)

    context = build_advisory_context(pack, profile)

    assert context.profile_id == profile.profile_id
    assert context.assessment_session_id is None
    assert context.participant_id == "p1"
    assert {f.field_key: f.normalized_value for f in context.facts}["funding_source"] == "public_grants"
    assert next(f for f in context.facts if f.field_key == "funding_source").display_value == "Öffentliche Förderungen"
    assert [r.recommendation_id for r in context.rule_recommendations] == [
        r.recommendation_id for r in session.result.recommendations
    ]
    assert context.rule_metrics == build_advisory_context(pack, session).rule_metrics


def test_profile_recommendations_are_generated_and_persisted_on_the_profile(tmp_path):
    from guideos.recommendations.service import (
        RecommendationServiceError,
        generate_profile_recommendations,
    )

    class FakeProvider:
        name = "fake"
        display_name = "Fake"
        default_endpoint = "http://fake"
        default_model = "fake"

        def __init__(self, result=None, error=None):
            self.result, self.error = result, error

        def stream_chat(self, endpoint, model, messages):
            yield ""

        def generate_structured(self, endpoint, model, messages, schema):
            if self.error:
                raise self.error
            return self.result

    pack = load_domain_pack()
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(Profile.from_session(_completed_session(participant_id="p1")))
    item = {
        "recommendation_id": "fee-review", "title": "Mitgliedsbeiträge prüfen",
        "message": "Verringert die Abhängigkeit von Förderungen.", "rationale": "Förderabhängig.",
        "evidence_fields": ["funding_source"], "confidence": 0.7,
    }

    items = generate_profile_recommendations(
        profiles, "verein-demo", "p1", FakeProvider({"recommendations": [item]}),
        "http://fake", "fake", load_pack=lambda domain_id, version: pack,
    )

    stored = profiles.get("verein-demo", "p1")
    assert [r.recommendation_id for r in items] == ["fee-review"]
    assert stored.result.knowledge_recommendations == items
    assert stored.result.knowledge_generation.model == "fake"
    assert stored.revision == 2
    assert [e.event_type for e in stored.events][-1] == "knowledge_recommendations_generated"
    with pytest.raises(RecommendationServiceError) as missing:
        generate_profile_recommendations(
            profiles, "andere-demo", "p1", FakeProvider({"recommendations": []}),
            "http://fake", "fake", load_pack=lambda domain_id, version: pack,
        )
    assert missing.value.code == "profile_not_found"
    from guideos.providers.base import ProviderError

    with pytest.raises(RecommendationServiceError) as failed:
        generate_profile_recommendations(
            profiles, "verein-demo", "p1", FakeProvider(error=ProviderError("weg")),
            "http://fake", "fake", load_pack=lambda domain_id, version: pack,
        )
    assert failed.value.code == "provider_error"
    assert profiles.get("verein-demo", "p1").revision == 2


# --- knowledge recommendations of the profile on request (REST + runner) ---


def test_rest_profile_recommendations_are_generated_and_persisted(tmp_path, monkeypatch):
    from guideos.adapters import fastapi as adapter

    item = {
        "recommendation_id": "fee-review", "title": "Mitgliedsbeiträge prüfen",
        "message": "Verringert die Abhängigkeit von Förderungen.", "rationale": "Förderabhängig.",
        "evidence_fields": ["funding_source"], "confidence": 0.7,
    }

    class FakeProvider:
        name = "fake"
        display_name = "Fake"
        default_endpoint = "http://fake"
        default_model = "fake"

        def stream_chat(self, endpoint, model, messages):
            yield ""

        def generate_structured(self, endpoint, model, messages, schema):
            return {"recommendations": [item]}

    monkeypatch.setattr(adapter, "get_provider", lambda name: FakeProvider())
    client = TestClient(
        adapter.create_app(
            tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json",
            knowledge_path=tmp_path / "knowledge.json", profile_path=tmp_path / "profiles.json",
        )
    )
    a = {HEADER: "participant-a"}

    missing = client.post("/api/v1/profiles/verein-demo/knowledge-recommendations", headers=a).json()
    _rest_complete(client, headers=a)
    generated = client.post("/api/v1/profiles/verein-demo/knowledge-recommendations", headers=a).json()
    profile = client.get("/api/v1/profiles/verein-demo", headers=a).json()["profile"]
    foreign = client.post(
        "/api/v1/profiles/verein-demo/knowledge-recommendations", headers={HEADER: "participant-b"}
    ).json()

    assert missing["ok"] is False and missing["errors"][0]["code"] == "profile_not_found"
    assert generated["ok"] is True and generated["domain_id"] == "verein-demo"
    assert [r["recommendation_id"] for r in generated["recommendations"]] == ["fee-review"]
    assert generated["recommendations"][0]["source"] == "llm"
    assert [r["recommendation_id"] for r in profile["result"]["knowledge_recommendations"]] == [
        "fee-review"
    ]
    assert profile["result"]["knowledge_generation"]["model"] == "fake"
    assert profile["revision"] == 2
    assert foreign["ok"] is False and foreign["errors"][0]["code"] == "profile_not_found"


def test_runner_maps_profile_recommendations_as_an_llm_action():
    from testsuite.models import LLM_ACTIONS
    from testsuite.runner import action_request

    from tests.test_testsuite_runner import make_scenario

    scenario = make_scenario(steps=[{"id": "start", "action": "start_assessment"},
                                    {"id": "recs", "action": "profile_recommendations"}])

    assert "profile_recommendations" in LLM_ACTIONS
    assert scenario.requires_llm is True
    assert action_request(scenario, scenario.steps[1], "sess_1", {}) == (
        "POST", "/api/v1/profiles/verein-demo/knowledge-recommendations", None
    )
