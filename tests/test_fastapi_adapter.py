from fastapi.testclient import TestClient

from guideos.adapters.fastapi import create_app
from guideos.domains.catalog import list_active_domain_versions


def test_health_returns_ok(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_domains_lists_active_demo_domains(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.get("/domains")

    assert response.status_code == 200
    domains = {item["domain_id"]: item for item in response.json()}
    assert "verein-demo" in domains and set(domains) == {ref.domain_id for ref in list_active_domain_versions()}
    assert domains["verein-demo"]["version"] == "1.0.0"
    assert domains["verein-demo"]["source"] in {"bundled", "local"}


def test_v1_health_returns_ok(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_v1_domains_lists_active_demo_domains(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.get("/api/v1/domains")

    assert response.status_code == 200
    domains = {item["domain_id"]: item for item in response.json()}
    assert "verein-demo" in domains and set(domains) == {ref.domain_id for ref in list_active_domain_versions()}
    assert domains["verein-demo"]["version"] == "1.0.0"


def test_v1_starts_assessment_session(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/api/v1/assessment-sessions",
        json={"domain_id": "verein-demo", "locale": "de"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["session_id"] is not None
    assert payload["status"] == "in_progress"
    assert payload["current_question"]["question_id"] == "club_type"


def test_v1_inputs_accept_structured_answer(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    started = _v1_start(client)

    response = client.post(
        f"/api/v1/assessment-sessions/{started['session_id']}/inputs",
        json={
            "kind": "submit_answer",
            "question_id": "club_type",
            "value": "sports",
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["accepted_answer"]["question_id"] == "club_type"
    assert payload["accepted_answer"]["normalized_value"] == "sports"
    assert payload["current_question"]["question_id"] == "founded_year"


def test_v1_get_assessment_session_returns_current_state(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    started = _v1_start(client)

    response = client.get(
        f"/api/v1/assessment-sessions/{started['session_id']}"
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["session_id"] == started["session_id"]
    assert payload["status"] == "in_progress"
    assert payload["current_question"]["question_id"] == "club_type"


def test_v1_complete_and_get_report(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = _v1_start(client)["session_id"]
    _v1_submit_all_answers(client, session_id)

    completed = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "complete"},
    )
    report = client.get(f"/api/v1/assessment-sessions/{session_id}/report")

    completed_payload = completed.json()
    report_payload = report.json()
    assert completed.status_code == 200
    assert completed_payload["ok"] is True
    assert completed_payload["status"] == "completed"
    assert completed_payload["report"]["format"] == "markdown"
    assert report.status_code == 200
    assert report_payload["ok"] is True
    assert report_payload["session_id"] == session_id
    assert report_payload["report"]["content"] == completed_payload["report"]["content"]


def test_v1_chat_rejects_removed_post_completion_scope(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = _v1_start(client)["session_id"]

    response = client.post(
        f"/api/v1/assessment-sessions/{session_id}/chat",
        json={"scope": "post_completion", "text": "Warum?"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["session_id"] == session_id
    assert payload["errors"][0]["code"] == "invalid_input"


def test_v1_inputs_return_contract_error_for_missing_session(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/api/v1/assessment-sessions/sess_missing/inputs",
        json={"kind": "complete"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["session_id"] == "sess_missing"
    assert payload["errors"][0]["code"] == "session_not_found"


def test_v1_inputs_return_contract_error_for_bad_payload(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = _v1_start(client)["session_id"]

    response = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "complete", "extra": True},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["session_id"] == session_id
    assert payload["errors"][0]["code"] == "invalid_input"


def test_v1_start_returns_404_for_unknown_domain(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/api/v1/assessment-sessions",
        json={"domain_id": "unknown-demo", "locale": "de"},
    )

    assert response.status_code == 404


def test_assessment_endpoint_starts_session(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/domains/verein-demo/assessment",
        json={"kind": "start", "locale": "de"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["session_id"] is not None
    assert payload["status"] == "in_progress"
    assert payload["current_question"]["question_id"] == "club_type"


def test_assessment_endpoint_accepts_structured_answer(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))
    started = client.post(
        "/domains/verein-demo/assessment",
        json={"kind": "start", "locale": "de"},
    ).json()

    response = client.post(
        "/domains/verein-demo/assessment",
        json={
            "kind": "submit_answer",
            "session_id": started["session_id"],
            "question_id": "club_type",
            "value": "sports",
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["accepted_answer"]["question_id"] == "club_type"
    assert payload["accepted_answer"]["normalized_value"] == "sports"
    assert payload["current_question"]["question_id"] == "founded_year"


def test_assessment_endpoint_returns_contract_error_for_bad_payload(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/domains/verein-demo/assessment",
        json={"kind": "complete"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "invalid_input"


def test_assessment_endpoint_returns_404_for_unknown_domain(tmp_path):
    client = TestClient(create_app(tmp_path / "assessments.json"))

    response = client.post(
        "/domains/unknown-demo/assessment",
        json={"kind": "start", "locale": "de"},
    )

    assert response.status_code == 404


def _v1_start(client: TestClient):
    return client.post(
        "/api/v1/assessment-sessions",
        json={"domain_id": "verein-demo", "locale": "de"},
    ).json()


def _v1_submit_all_answers(client: TestClient, session_id: str) -> None:
    for question_id, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        response = client.post(
            f"/api/v1/assessment-sessions/{session_id}/inputs",
            json={
                "kind": "submit_answer",
                "question_id": question_id,
                "value": value,
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True


class _RecommendingProvider:
    default_endpoint = "http://unused"
    default_model = "scripted"

    def generate_structured(self, *args):
        return {"recommendations": [{
            "recommendation_id": "hydraulic-balancing", "title": "Hydraulischen Abgleich prüfen",
            "message": "Senkt den Verbrauch.", "rationale": "Gasheizung.",
            "evidence_fields": ["funding_source"], "confidence": 0.7}]}

    def stream_chat(self, *args):
        yield ""


def test_v1_complete_generates_knowledge_recommendations_on_request(monkeypatch, tmp_path):
    provider = _RecommendingProvider()
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: provider)
    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = _v1_start(client)["session_id"]
    _v1_submit_all_answers(client, session_id)

    completed = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "complete", "knowledge_recommendations": True},
    )

    payload = completed.json()
    assert completed.status_code == 200
    assert payload["status"] == "completed"
    assert [item["source"] for item in payload["result"]["knowledge_recommendations"]] == ["llm"]
    assert payload["result"]["knowledge_generation"]["model"] == "scripted"
    assert payload["result"]["knowledge_generation"]["error_code"] is None


def test_v1_complete_stays_deterministic_without_the_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("guideos.adapters.fastapi.get_provider", lambda name: _RecommendingProvider())
    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = _v1_start(client)["session_id"]
    _v1_submit_all_answers(client, session_id)

    payload = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "complete", "knowledge_recommendations": False},
    ).json()

    assert payload["status"] == "completed"
    assert payload["result"]["knowledge_recommendations"] == []
    assert payload["result"]["knowledge_generation"] is None


def test_serves_the_built_frontend_next_to_the_api(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Frontend</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("GUIDEOS_FRONTEND_DIST", str(dist))
    client = TestClient(create_app(tmp_path / "assessments.json"))

    index = client.get("/")
    asset = client.get("/assets/app.js")
    health = client.get("/api/v1/health")

    assert index.status_code == 200 and "Frontend" in index.text
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert health.json() == {"ok": True}


def test_without_a_frontend_dist_the_root_is_not_served(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_FRONTEND_DIST", str(tmp_path / "missing"))
    client = TestClient(create_app(tmp_path / "assessments.json"))

    assert client.get("/").status_code == 404
    assert client.get("/api/v1/health").json() == {"ok": True}


def test_the_frontend_page_is_never_cached_but_its_assets_are(tmp_path, monkeypatch):
    """A browser must fetch index.html fresh after every release (the bundle
    names are hashed, so the assets may be cached for long)."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Frontend</title>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("GUIDEOS_FRONTEND_DIST", str(dist))
    client = TestClient(create_app(tmp_path / "assessments.json"))

    index = client.get("/")
    index_named = client.get("/index.html")
    asset = client.get("/assets/index-abc123.js")

    assert index.headers["cache-control"] == "no-cache"
    assert index_named.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_delete_assessment_session_removes_it_for_its_participant_only(tmp_path):
    """A participant can delete a running assessment (the frontend's "Meine
    Daten löschen" before a profile exists); other participants' sessions and
    unknown ids answer session_not_found."""
    client = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))
    own = {"X-GuideOS-Participant": "anna"}
    foreign = {"X-GuideOS-Participant": "bernd"}
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers=own).json()
    session_id = started["session_id"]

    refused = client.delete(f"/api/v1/assessment-sessions/{session_id}", headers=foreign).json()
    deleted = client.delete(f"/api/v1/assessment-sessions/{session_id}", headers=own).json()
    gone = client.get(f"/api/v1/assessment-sessions/{session_id}", headers=own).json()
    again = client.delete(f"/api/v1/assessment-sessions/{session_id}", headers=own).json()

    assert refused["ok"] is False and refused["errors"][0]["code"] == "session_not_found"
    assert deleted == {"ok": True, "session_id": session_id, "errors": []}
    assert gone["ok"] is False and gone["errors"][0]["code"] == "session_not_found"
    assert again["ok"] is False and again["errors"][0]["code"] == "session_not_found"
