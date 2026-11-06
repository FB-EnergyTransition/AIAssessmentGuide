"""Model selection of the REST API: with GUIDEOS_MODEL_SELECTION=1 the API
lists the endpoint's models under GET /api/v1/models and takes the model of
the header X-GuideOS-Model for every LLM call; without the flag the header is
ignored and the provider's default model is used, as before."""

from fastapi.testclient import TestClient

import guideos.adapters.fastapi as fastapi_adapter
from guideos.adapters.fastapi import create_app
from guideos.config import DEFAULT_OLLAMA_MODEL
from guideos.providers.base import ProviderError

MODEL_HEADER = "X-GuideOS-Model"


class RecordingProvider:
    """Answers every structured call by schema and records the model used."""

    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, models=("fake", "fake-large"), list_error=None):
        self.models_available = list(models)
        self.list_error = list_error
        self.calls = []

    def list_models(self, endpoint):
        if self.list_error:
            raise self.list_error
        return list(self.models_available)

    def stream_chat(self, endpoint, model, messages):
        raise AssertionError("nicht erwartet")

    def generate_structured(self, endpoint, model, messages, schema):
        self.calls.append((endpoint, model))
        properties = set(schema.get("properties", {}))
        if "decision" in properties:
            return {"decision": "valid", "normalized_value": "sports", "confidence": 0.9,
                    "reason_code": None, "follow_up": None}
        if "proposes_answer" in properties:
            return {"message": "Hilfe", "proposes_answer": False, "normalized_value": None, "confidence": 0.5}
        if "recommendations" in properties:
            return {"recommendations": [{
                "recommendation_id": "hydraulic-balancing", "title": "Hydraulischen Abgleich prüfen",
                "message": "Senkt den Verbrauch.", "rationale": "Gasheizung.",
                "evidence_fields": ["funding_source"], "confidence": 0.7}]}
        return {"sections": [{"text": "Beratungsantwort", "source_ids": []}]}

    @property
    def models_used(self):
        return [model for _endpoint, model in self.calls]


def _client(tmp_path, monkeypatch, provider, enabled=None):
    monkeypatch.setattr(fastapi_adapter, "get_provider", lambda name: provider)
    if enabled is None:
        monkeypatch.delenv("GUIDEOS_MODEL_SELECTION", raising=False)
    else:
        monkeypatch.setenv("GUIDEOS_MODEL_SELECTION", enabled)
    return TestClient(
        create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json")
    )


def _start(client):
    return client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).json()["session_id"]


def _answer_all(client, session_id):
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
        )


def _completed_session(client):
    session_id = _start(client)
    _answer_all(client, session_id)
    completed = client.post(f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}).json()
    assert completed["status"] == "completed", completed
    return session_id


def test_models_endpoint_is_disabled_by_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, RecordingProvider())

    response = client.get("/api/v1/models")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "default_model": DEFAULT_OLLAMA_MODEL, "models": []}


def test_models_endpoint_does_not_touch_the_provider_while_disabled(tmp_path, monkeypatch):
    """A disabled feature must not fail because of the provider registry."""
    def unknown_provider(name):
        raise KeyError(name)

    client = _client(tmp_path, monkeypatch, RecordingProvider())
    monkeypatch.setattr(fastapi_adapter, "get_provider", unknown_provider)

    response = client.get("/api/v1/models")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "default_model": DEFAULT_OLLAMA_MODEL, "models": []}


def test_header_model_must_be_listed_by_the_endpoint(tmp_path, monkeypatch):
    """The list the frontend offers is also the server-side allowlist: a model
    the endpoint does not list is refused instead of being loaded on the
    shared Ollama endpoint, and no model call is made."""
    provider = RecordingProvider()
    client = _client(tmp_path, monkeypatch, provider, enabled="1")
    session_id = _start(client)

    response = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "submit_text_answer", "question_id": "club_type", "text": "Sportverein"},
        headers={MODEL_HEADER: "llama3:70b"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Das gewählte KI-Modell steht am Endpoint nicht zur Auswahl."
    assert provider.calls == []


def test_header_model_is_refused_while_the_model_list_is_unavailable(tmp_path, monkeypatch):
    provider = RecordingProvider(list_error=ProviderError("Ollama-Endpunkt konnte nicht erreicht werden"))
    client = _client(tmp_path, monkeypatch, provider, enabled="1")
    session_id = _completed_session(client)

    response = client.post(
        f"/api/v1/assessment-sessions/{session_id}/llm-recommendations", headers={MODEL_HEADER: "fake-large"}
    )
    without_header = client.post(f"/api/v1/assessment-sessions/{session_id}/llm-recommendations")

    assert response.status_code == 400
    assert without_header.status_code == 200 and without_header.json()["ok"] is True
    assert provider.models_used == ["fake"]


def test_models_endpoint_lists_the_endpoints_models_when_enabled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, RecordingProvider(), enabled="1")

    response = client.get("/api/v1/models")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "default_model": "fake", "models": ["fake", "fake-large"]}


def test_models_endpoint_returns_an_empty_list_when_the_endpoint_is_unreachable(tmp_path, monkeypatch, caplog):
    provider = RecordingProvider(list_error=ProviderError("Ollama-Endpunkt konnte nicht erreicht werden"))
    client = _client(tmp_path, monkeypatch, provider, enabled="1")

    with caplog.at_level("WARNING", logger="guideos"):
        response = client.get("/api/v1/models")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "default_model": "fake", "models": []}
    assert "model_list_failed" in caplog.text


def test_header_is_ignored_without_the_flag(tmp_path, monkeypatch):
    provider = RecordingProvider()
    client = _client(tmp_path, monkeypatch, provider)
    session_id = _start(client)

    client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "submit_text_answer", "question_id": "club_type", "text": "Sportverein"},
        headers={MODEL_HEADER: "fake-large"},
    )

    assert provider.models_used == ["fake"]


def test_header_model_reaches_text_answers_and_question_help(tmp_path, monkeypatch):
    provider = RecordingProvider()
    client = _client(tmp_path, monkeypatch, provider, enabled="1")
    session_id = _start(client)

    text = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs",
        json={"kind": "submit_text_answer", "question_id": "club_type", "text": "Sportverein"},
        headers={MODEL_HEADER: "fake-large"},
    ).json()
    help_reply = client.post(
        f"/api/v1/assessment-sessions/{session_id}/chat",
        json={"scope": "question_help", "question_id": "founded_year", "text": "Was heißt Baujahr?"},
        headers={MODEL_HEADER: " fake-large "},
    ).json()
    without_header = client.post(
        f"/api/v1/assessment-sessions/{session_id}/chat",
        json={"scope": "question_help", "question_id": "founded_year", "text": "Nochmal?"},
    ).json()

    assert text["ok"] is True and help_reply["ok"] is True and without_header["ok"] is True
    assert provider.models_used == ["fake-large", "fake-large", "fake"]


def test_header_model_reaches_completion_recommendations_unless_the_body_names_one(tmp_path, monkeypatch):
    provider = RecordingProvider()
    client = _client(tmp_path, monkeypatch, provider, enabled="1")

    first = _start(client)
    _answer_all(client, first)
    by_header = client.post(
        f"/api/v1/assessment-sessions/{first}/inputs",
        json={"kind": "complete", "knowledge_recommendations": True},
        headers={MODEL_HEADER: "fake-large"},
    ).json()
    second = _start(client)
    _answer_all(client, second)
    by_body = client.post(
        f"/api/v1/assessment-sessions/{second}/inputs",
        json={"kind": "complete", "knowledge_recommendations": True, "endpoint": "http://fake", "model": "explicit"},
        headers={MODEL_HEADER: "fake-large"},
    ).json()

    assert by_header["result"]["knowledge_generation"]["model"] == "fake-large"
    assert by_body["result"]["knowledge_generation"]["model"] == "explicit"
    assert provider.models_used == ["fake-large", "explicit"]


def test_header_model_reaches_recommendation_and_conversation_endpoints(tmp_path, monkeypatch):
    provider = RecordingProvider()
    client = _client(tmp_path, monkeypatch, provider, enabled="1")
    session_id = _completed_session(client)
    headers = {MODEL_HEADER: "fake-large"}

    llm = client.post(f"/api/v1/assessment-sessions/{session_id}/llm-recommendations", headers=headers).json()
    profile = client.post("/api/v1/profiles/verein-demo/knowledge-recommendations", headers=headers).json()
    conversation = client.post(
        "/api/v1/conversations", json={"domain_id": "verein-demo", "title": "Heizung"}
    ).json()["conversation"]
    sent = client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"text": "Was empfiehlst du?"},
        headers=headers,
    ).json()
    default = client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/messages",
        json={"text": "Und sonst?"},
    ).json()

    assert llm["ok"] is True and profile["ok"] is True and sent["ok"] is True and default["ok"] is True
    assert provider.models_used == ["fake-large", "fake-large", "fake-large", "fake"]


def test_models_endpoint_needs_the_pilot_login(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", "geheim")
    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    client = _client(tmp_path, monkeypatch, RecordingProvider(), enabled="1")

    rejected = client.get("/api/v1/models")
    # Own source address: the throttle test of the login suite locks the default test-client source per process.
    client.post("/api/v1/login", json={"username": "test", "password": "geheim"}, headers={"X-Forwarded-For": "10.7.7.7"})
    allowed = client.get("/api/v1/models")

    assert rejected.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["models"] == ["fake", "fake-large"]
