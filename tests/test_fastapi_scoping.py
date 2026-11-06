from fastapi.testclient import TestClient

import guideos.adapters.fastapi as fastapi_adapter
from guideos.adapters.fastapi import create_app
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.domains.catalog import LocalCatalogStore

HEADER = "X-GuideOS-Participant"


def test_api_routes_all_repository_access_through_env_factories(tmp_path, monkeypatch):
    assessments = LocalAssessmentRepository(tmp_path / "factory" / "assessments.json")
    conversations = LocalConversationRepository(tmp_path / "factory" / "conversations.json")
    monkeypatch.setattr(fastapi_adapter, "assessment_repository_from_env", lambda path: assessments)
    monkeypatch.setattr(fastapi_adapter, "conversation_repository_from_env", lambda path: conversations)
    client = TestClient(create_app(tmp_path / "unused.json", conversation_path=tmp_path / "unused_conv.json"))

    session_id = _completed_session(client)
    loaded = client.get(f"/api/v1/assessment-sessions/{session_id}").json()
    created = client.post(
        "/api/v1/conversations",
        json={"domain_id": "verein-demo"},
    ).json()
    listed = client.get("/api/v1/conversations").json()

    assert loaded["ok"] is True and loaded["status"] == "completed"
    assert created["ok"] is True
    assert [c["conversation_id"] for c in listed["conversations"]] == [created["conversation"]["conversation_id"]]
    assert [s.session_id for s in assessments.list()] == [session_id]
    assert len(conversations.list()) == 1
    assert not (tmp_path / "unused.json").exists()
    assert not (tmp_path / "unused_conv.json").exists()


def test_participant_header_scopes_sessions_and_conversations(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))
    a = {HEADER: "participant-a"}
    b = {HEADER: "participant-b"}
    session_id = _completed_session(client, headers=a)
    conversation_id = client.post(
        "/api/v1/conversations",
        json={"domain_id": "verein-demo"},
        headers=a,
    ).json()["conversation"]["conversation_id"]

    own_session = client.get(f"/api/v1/assessment-sessions/{session_id}", headers=a).json()
    foreign_session = client.get(f"/api/v1/assessment-sessions/{session_id}", headers=b).json()
    foreign_report = client.get(f"/api/v1/assessment-sessions/{session_id}/report", headers=b).json()
    foreign_recs = client.post(f"/api/v1/assessment-sessions/{session_id}/llm-recommendations", headers=b).json()
    foreign_list = client.get("/api/v1/conversations", headers=b).json()
    foreign_get = client.get(f"/api/v1/conversations/{conversation_id}", headers=b).json()
    foreign_send = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"}, headers=b).json()
    foreign_delete = client.delete(f"/api/v1/conversations/{conversation_id}", headers=b).json()
    foreign_create = client.post(
        "/api/v1/conversations",
        json={"domain_id": "verein-demo"},
        headers=b,
    ).json()
    own_list = client.get("/api/v1/conversations", headers=a).json()

    assert own_session["ok"] is True
    assert foreign_session["errors"][0]["code"] == "session_not_found"
    assert foreign_report["errors"][0]["code"] == "session_not_found"
    assert foreign_recs["errors"][0]["code"] == "session_not_found"
    assert foreign_list["ok"] is True and foreign_list["conversations"] == []
    assert foreign_get["errors"][0]["code"] == "conversation_not_found"
    assert foreign_send["errors"][0]["code"] == "conversation_not_found"
    assert foreign_delete["errors"][0]["code"] == "conversation_not_found"
    assert foreign_create["errors"][0]["code"] == "profile_not_found"
    assert [c["conversation_id"] for c in own_list["conversations"]] == [conversation_id]
    assert own_list["conversations"][0]["participant_id"] == "participant-a"
    assert LocalAssessmentRepository(tmp_path / "assessments.json").get(session_id).participant_id == "participant-a"


def test_requests_without_header_stay_unscoped_without_database(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))
    session_id = _completed_session(client, headers={HEADER: "participant-a"})

    unscoped = client.get(f"/api/v1/assessment-sessions/{session_id}").json()

    assert unscoped["ok"] is True


def test_participant_header_is_required_when_database_is_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://unused")
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    monkeypatch.setattr(fastapi_adapter, "assessment_repository_from_env", lambda path: assessments)
    monkeypatch.setattr(fastapi_adapter, "conversation_repository_from_env", lambda path: conversations)
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    client = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))

    rejected = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"})
    rejected_list = client.get("/api/v1/conversations")
    accepted = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers={HEADER: "participant-a"}
    )

    assert rejected.status_code == 200
    assert rejected.json()["ok"] is False
    assert rejected.json()["errors"][0]["code"] == "invalid_input"
    assert rejected_list.json()["errors"][0]["code"] == "invalid_input"
    assert accepted.json()["ok"] is True
    assert assessments.get(accepted.json()["session_id"]).participant_id == "participant-a"


def _completed_session(client, headers=None):
    headers = headers or {}
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=headers).json()
    assert started["ok"] is True, started
    session_id = started["session_id"]
    for question_id, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        client.post(
            f"/api/v1/assessment-sessions/{session_id}/inputs",
            json={"kind": "submit_answer", "question_id": question_id, "value": value},
            headers=headers,
        )
    completed = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}, headers=headers
    ).json()
    assert completed["status"] == "completed", completed
    return session_id
