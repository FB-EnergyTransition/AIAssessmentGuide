from fastapi.testclient import TestClient

import guideos.adapters.fastapi as fastapi_adapter
from guideos.adapters.fastapi import create_app
from guideos.knowledge.factory import knowledge_service_from_env
from guideos.providers.base import ProviderError


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, error=None, source_ids=()):
        self.error = error
        self.source_ids = list(source_ids)
        self.calls = []

    def stream_chat(self, endpoint, model, messages):
        raise AssertionError("nicht erwartet")

    def generate_structured(self, endpoint, model, messages, schema):
        self.calls.append((endpoint, model, messages))
        if self.error:
            raise self.error
        return {"sections": [{"text": "Beratungsantwort", "source_ids": self.source_ids}]}


def test_create_conversation_for_completed_session(tmp_path):
    client = _client(tmp_path)
    session_id = _completed_session(client)

    response = client.post(
        "/api/v1/conversations",
        json={"domain_id": "verein-demo", "title": "Heizung"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    conversation = payload["conversation"]
    assert conversation["conversation_id"].startswith("conv_")
    assert conversation["profile_id"].startswith("prof_")
    assert session_id
    assert conversation["title"] == "Heizung"
    assert conversation["messages"] == []
    assert payload["errors"] == []


def test_create_conversation_requires_a_profile_and_a_valid_payload(tmp_path):
    client = _client(tmp_path)
    started = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}
    ).json()

    without_profile = client.post(
        "/api/v1/conversations", json={"domain_id": "verein-demo"}
    ).json()
    invalid = client.post("/api/v1/conversations", json={"title": "x"}).json()

    assert started["ok"] is True
    assert without_profile["ok"] is False
    assert without_profile["errors"][0]["code"] == "profile_not_found"
    assert "Assessment" in without_profile["errors"][0]["message"]
    assert invalid["ok"] is False
    assert invalid["errors"][0]["code"] == "invalid_input"


def test_list_get_and_delete_conversations(tmp_path):
    client = _client(tmp_path)
    session_id = _completed_session(client)
    first = _create(client, session_id, "A")
    second = _create(client, session_id, "B")

    listed = client.get("/api/v1/conversations").json()
    loaded = client.get(f"/api/v1/conversations/{second}").json()
    deleted = client.delete(f"/api/v1/conversations/{first}").json()
    after = client.get("/api/v1/conversations").json()
    missing = client.get(f"/api/v1/conversations/{first}").json()
    missing_delete = client.delete(f"/api/v1/conversations/{first}").json()

    assert [c["title"] for c in listed["conversations"]] == ["A", "B"]
    assert loaded["ok"] is True and loaded["conversation"]["conversation_id"] == second
    assert deleted["ok"] is True
    assert [c["conversation_id"] for c in after["conversations"]] == [second]
    assert missing["ok"] is False
    assert missing["errors"][0]["code"] == "conversation_not_found"
    assert missing_delete["errors"][0]["code"] == "conversation_not_found"


def test_result_conversation_endpoint_seeds_and_reuses_the_result_chat(tmp_path, monkeypatch):
    provider = FakeProvider()
    client = _client(tmp_path, monkeypatch, provider)
    _completed_session(client)

    first = client.post("/api/v1/conversations/result", json={"domain_id": "verein-demo"}).json()
    second = client.post("/api/v1/conversations/result", json={"domain_id": "verein-demo"}).json()
    listed = client.get("/api/v1/conversations").json()

    assert first["ok"] is True
    conversation = first["conversation"]
    assert conversation["title"] == "Ergebnis"
    assert conversation["result_session_id"]
    roles = [message["role"] for message in conversation["messages"]]
    assert roles and set(roles) == {"assistant"}
    assert any("Empfehlung" in message["content"] for message in conversation["messages"])
    assert second["conversation"]["conversation_id"] == conversation["conversation_id"]
    assert len(second["conversation"]["messages"]) == len(conversation["messages"])
    assert [c["conversation_id"] for c in listed["conversations"]] == [conversation["conversation_id"]]
    assert provider.calls == []


def test_result_conversation_endpoint_errors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeProvider())

    without_profile = client.post("/api/v1/conversations/result", json={"domain_id": "verein-demo"}).json()
    invalid = client.post("/api/v1/conversations/result", json={"title": "x"}).json()

    assert without_profile["ok"] is False
    assert without_profile["errors"][0]["code"] == "profile_not_found"
    assert invalid["ok"] is False
    assert invalid["errors"][0]["code"] == "invalid_input"


def test_send_message_uses_context_and_persists_reply(tmp_path, monkeypatch):
    provider = FakeProvider()
    client = _client(tmp_path, monkeypatch, provider)
    session_id = _completed_session(client)
    conversation_id = _create(client, session_id, "A")

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Was empfiehlst du?"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert [(m["role"], m["content"]) for m in payload["conversation"]["messages"]] == [
        ("user", "Was empfiehlst du?"),
        ("assistant", "Beratungsantwort  \n_Quelle: KI-Fachwissen_"),
    ]
    assert payload["conversation"]["messages"][1]["sections"] == [
        {
            "text": "Beratungsantwort",
            "sources": [
                {
                    "kind": "llm", "title": "KI-Fachwissen", "reference": None, "locator": None,
                    "url": None, "content_hash": None, "retrieved_at": None, "excerpt": None,
                }
            ],
            "unverified_values": [],
        }
    ]
    assert "prompt" not in payload and "context" not in payload
    endpoint, model, messages = provider.calls[0]
    assert (endpoint, model) == ("http://fake", "fake")
    assert "DOMAIN_KNOWLEDGE" in messages[0]["content"]
    assert "verified_user_context" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "Was empfiehlst du?"}
    reloaded = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert len(reloaded["conversation"]["messages"]) == 2


def test_send_message_cites_documents_of_the_knowledge_base(tmp_path, monkeypatch):
    provider = FakeProvider(source_ids=["D1"])
    client = _client(tmp_path, monkeypatch, provider)
    session_id = _completed_session(client)
    conversation_id = _create(client, session_id, "A")
    document = knowledge_service_from_env(provider).add_document(
        "verein-demo", "leitfaden.md",
        "# Förderung\n\nDer Heizungstausch wird gefördert.".encode(), "http://fake",
    )

    payload = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Wird der Heizungstausch gefördert?"},
    ).json()

    [source] = payload["conversation"]["messages"][1]["sections"][0]["sources"]
    assert source["kind"] == "document" and source["title"] == "Förderung"
    assert source["reference"] == document.document_id
    assert source["content_hash"] == document.content_hash
    assert "gefördert" in source["excerpt"]
    assert "Dokument „Förderung“" in payload["conversation"]["messages"][1]["content"]
    assert '"source_id": "D1"' in provider.calls[0][2][1]["content"]


def test_send_message_errors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeProvider(error=ProviderError("Ollama weg")))
    session_id = _completed_session(client)
    conversation_id = _create(client, session_id, "A")

    empty = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": " "}).json()
    invalid = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"txt": "x"}).json()
    failed = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Frage"}).json()
    missing = client.post("/api/v1/conversations/conv_fehlt/messages", json={"text": "Frage"}).json()

    assert empty["errors"][0]["code"] == "required"
    assert invalid["errors"][0]["code"] == "invalid_input"
    assert failed["errors"][0]["code"] == "provider_error"
    assert "Ollama weg" in failed["errors"][0]["message"]
    assert missing["errors"][0]["code"] == "conversation_not_found"
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["conversation"]["messages"] == []


def test_conversations_survive_app_restart(tmp_path):
    client = _client(tmp_path)
    session_id = _completed_session(client)
    conversation_id = _create(client, session_id, "A")

    restarted = _client(tmp_path)

    payload = restarted.get(f"/api/v1/conversations/{conversation_id}").json()
    assert payload["ok"] is True
    assert payload["conversation"]["profile_id"].startswith("prof_")
    assert session_id


def _client(tmp_path, monkeypatch=None, provider=None):
    if monkeypatch is not None:
        monkeypatch.setattr(fastapi_adapter, "get_provider", lambda name: provider)
    return TestClient(
        create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json")
    )


def _completed_session(client):
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).json()
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
        )
    completed = client.post(
        f"/api/v1/assessment-sessions/{session_id}/inputs", json={"kind": "complete"}
    ).json()
    assert completed["status"] == "completed"
    return session_id


def _create(client, session_id, title):
    payload = client.post(
        "/api/v1/conversations",
        json={"domain_id": "verein-demo", "title": title},
    ).json()
    assert payload["ok"] is True, payload
    return payload["conversation"]["conversation_id"]


class FakeRecommendationProvider(FakeProvider):
    def __init__(self, result=None, error=None):
        super().__init__()
        self.result = result
        self.structured_error = error

    def generate_structured(self, endpoint, model, messages, schema):
        if self.structured_error:
            raise self.structured_error
        return self.result


LLM_ITEM = {
    "recommendation_id": "hydraulic-balancing",
    "title": "Hydraulischen Abgleich prüfen",
    "message": "Senkt bei Gasheizungen oft den Verbrauch.",
    "rationale": "Gasheizung im Altbau.",
    "evidence_fields": ["funding_source"],
    "confidence": 0.7,
}


def test_llm_recommendations_endpoint_returns_llm_provenance(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeRecommendationProvider({"recommendations": [LLM_ITEM]}))
    session_id = _completed_session(client)

    response = client.post(f"/api/v1/assessment-sessions/{session_id}/llm-recommendations")
    result = client.get(f"/api/v1/assessment-sessions/{session_id}").json()

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["session_id"] == session_id
    assert [(r["recommendation_id"], r["source"]) for r in payload["recommendations"]] == [
        ("hydraulic-balancing", "llm")
    ]
    assert [s["kind"] for s in payload["recommendations"][0]["sources"]] == ["llm"]
    assert {r["source"] for r in result["result"]["recommendations"]} == {"rule"}
    assert "hydraulic-balancing" not in {r["recommendation_id"] for r in result["result"]["recommendations"]}


def test_llm_recommendations_endpoint_errors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeRecommendationProvider(error=ProviderError("weg")))
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).json()
    session_id = _completed_session(client)

    unfinished = client.post(f"/api/v1/assessment-sessions/{started['session_id']}/llm-recommendations").json()
    missing = client.post("/api/v1/assessment-sessions/sess_fehlt/llm-recommendations").json()
    failed = client.post(f"/api/v1/assessment-sessions/{session_id}/llm-recommendations").json()

    assert unfinished["errors"][0]["code"] == "invalid_state"
    assert missing["errors"][0]["code"] == "session_not_found"
    assert failed["ok"] is False and failed["errors"][0]["code"] == "provider_error"
