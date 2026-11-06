"""Knowledge recommendations are generated at completion, persisted on the
session result, reported and shown in the result chat (concept step 1)."""

from datetime import UTC, datetime

import pytest

from guideos.assessment.contract import CompleteAssessmentInput, to_contract_response
from guideos.assessment.models import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    KnowledgeGeneration,
    LLMRecommendation,
)
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import RECOMMENDATION_OFFER, ConversationService
from guideos.profiles.models import Profile
from guideos.profiles.repository import LocalProfileRepository
from guideos.domains.catalog import pack_draft_payload
from guideos.domains.loader import load_domain_pack
from guideos.domains.models import KnowledgeRecommendationSettings
from guideos.knowledge.models import RetrievedPassage, SourceRef
from guideos.recommendations.llm import (
    PROMPT_VERSION,
    LLMRecommendationGenerator,
)
from guideos.recommendations.service import (
    RecommendationServiceError,
    generate_llm_recommendations,
)
from guideos.reports.renderer import render_markdown_report
from guideos.rules.engine import evaluate_assessment

ANSWERS = {
    "club_type": "sports",
    "founded_year": 1980,
    "members_count": 120,
    "funding_source": "public_grants",
    "volunteer_situation": "tight",
    "annual_budget_eur": None,
}

ITEM = {
    "recommendation_id": "hydraulic-balancing",
    "title": "Hydraulischen Abgleich prüfen",
    "message": "Senkt bei Gasheizungen oft den Verbrauch.",
    "rationale": "Gasheizung im Altbau.",
    "evidence_fields": ["funding_source"],
    "confidence": 0.7,
}

PASSAGE = RetrievedPassage(
    source_id="D1", kind="document", document_id="doc_1", title="Leitfaden Heizen",
    locator="Seite 4", url=None, content_hash="a" * 64,
    retrieved_at=datetime(2026, 10, 1, tzinfo=UTC), text="Ein hydraulischer Abgleich spart.",
    score=1.0,
)


def _cited_item() -> LLMRecommendation:
    return LLMRecommendation(
        **ITEM, source_ids=["D1"], sources=[PASSAGE.to_source()], unverified_values=["7 500 €"]
    )


def _llm_item() -> LLMRecommendation:
    return LLMRecommendation(
        recommendation_id="thermostats", title="Thermostatventile tauschen",
        message="Alte Ventile ersetzen.", rationale="Gasheizung.",
        evidence_fields=["funding_source"],
        sources=[SourceRef(kind="llm", title="KI-Fachwissen")],
    )


def _generation(**overrides) -> KnowledgeGeneration:
    values = {
        "model": "fake", "prompt_version": PROMPT_VERSION,
        "generated_at": datetime(2026, 10, 8, tzinfo=UTC), "passages": [PASSAGE], "retrieval_mode": "lexical",
    }
    return KnowledgeGeneration(**{**values, **overrides})


class FakeRecommender:
    def __init__(self, items=None, error=None):
        self.items = items if items is not None else [_cited_item()]
        self.error = error
        self.calls = []

    def __call__(self, pack, session, endpoint, model):
        self.calls.append((pack.domain_id, session.session_id, endpoint, model))
        if self.error is not None:
            raise self.error
        return self.items, _generation(model=model), []


def _orchestrator(tmp_path, recommender=None, pack=None):
    return AssessmentOrchestrator(
        pack or load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        knowledge_recommender=recommender,
    )


def _reviewed_session(orchestrator):
    response = orchestrator.start()
    for question_id, value in ANSWERS.items():
        orchestrator.submit(response.session_id, question_id, value)
    return response.session_id


def test_result_without_knowledge_fields_still_loads():
    result = AssessmentResult(reason_codes=[], recommendations=[])

    assert result.knowledge_recommendations == []
    assert result.knowledge_generation is None


def test_complete_persists_knowledge_recommendations_and_reports_them(tmp_path):
    recommender = FakeRecommender()
    orchestrator = _orchestrator(tmp_path, recommender)
    session_id = _reviewed_session(orchestrator)

    completed = orchestrator.complete(session_id, endpoint="http://fake", model="fake")

    stored = orchestrator.repository.get(session_id)
    assert recommender.calls == [("verein-demo", session_id, "http://fake", "fake")]
    assert [r.recommendation_id for r in stored.result.knowledge_recommendations] == [
        "hydraulic-balancing"
    ]
    assert stored.result.knowledge_generation.model == "fake"
    assert stored.result.knowledge_generation.prompt_version == PROMPT_VERSION
    assert [p.source_id for p in stored.result.knowledge_generation.passages] == ["D1"]
    assert stored.result.knowledge_generation.error_code is None
    assert all(r.source == "rule" for r in stored.result.recommendations)
    assert "## Hinweise aus Wissensquellen (KI-formuliert)" in completed.report.content
    assert "Hydraulischen Abgleich prüfen" in completed.report.content
    assert "Leitfaden Heizen" in completed.report.content
    assert completed.status == "completed"


def test_complete_via_contract_passes_endpoint_and_model(tmp_path):
    recommender = FakeRecommender()
    orchestrator = _orchestrator(tmp_path, recommender)
    session_id = _reviewed_session(orchestrator)

    response = orchestrator.handle_contract(
        CompleteAssessmentInput(session_id=session_id, endpoint="http://fake", model="fake")
    )

    assert response.ok
    assert recommender.calls[0][2:] == ("http://fake", "fake")
    assert [r.recommendation_id for r in response.result.knowledge_recommendations] == [
        "hydraulic-balancing"
    ]
    assert response.result.knowledge_generation.model == "fake"
    assert response.result.knowledge_generation.passage_count == 1
    assert response.result.knowledge_generation.error_code is None


def test_complete_without_model_skips_generation(tmp_path):
    recommender = FakeRecommender()
    orchestrator = _orchestrator(tmp_path, recommender)
    session_id = _reviewed_session(orchestrator)

    completed = orchestrator.complete(session_id)

    assert recommender.calls == []
    assert completed.result.knowledge_recommendations == []
    assert completed.result.knowledge_generation is None
    assert "Hinweise aus Wissensquellen" not in completed.report.content


def test_complete_survives_a_failed_generation(tmp_path):
    recommender = FakeRecommender(
        error=RecommendationServiceError("Ollama nicht erreichbar.", code="provider_error")
    )
    orchestrator = _orchestrator(tmp_path, recommender)
    session_id = _reviewed_session(orchestrator)

    completed = orchestrator.complete(session_id, endpoint="http://fake", model="fake")

    assert completed.status == "completed"
    assert completed.result.recommendations
    assert completed.result.knowledge_recommendations == []
    assert completed.result.knowledge_generation.error_code == "provider_error"
    assert completed.result.knowledge_generation.passages == []
    assert "konnten nicht erzeugt werden" in completed.report.content


def test_pack_can_disable_knowledge_recommendations(tmp_path):
    recommender = FakeRecommender()
    pack = load_domain_pack().model_copy(
        update={"knowledge_recommendations": KnowledgeRecommendationSettings(enabled=False)}
    )
    orchestrator = _orchestrator(tmp_path, recommender, pack=pack)
    session_id = _reviewed_session(orchestrator)

    completed = orchestrator.complete(session_id, endpoint="http://fake", model="fake")

    assert recommender.calls == []
    assert completed.result.knowledge_generation is None


def test_draft_serialization_only_contains_knowledge_settings_when_configured():
    pack = load_domain_pack()

    assert "knowledge_recommendations" not in pack_draft_payload(pack)
    configured = pack.model_copy(
        update={"knowledge_recommendations": KnowledgeRecommendationSettings(max_count=2)}
    )
    assert pack_draft_payload(configured)["knowledge_recommendations"] == {
        "enabled": True,
        "max_count": 2,
    }


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result):
        self.result = result
        self.schemas = []
        self.messages = []

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.schemas.append(schema)
        self.messages.append(messages)
        return self.result


def test_generator_limits_the_batch_to_the_requested_count():
    from guideos.context.builder import build_advisory_context

    pack = load_domain_pack()
    session = _completed_session(pack)
    provider = FakeProvider({"recommendations": [ITEM]})

    LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", build_advisory_context(pack, session), max_count=2
    )

    assert provider.schemas[0]["properties"]["recommendations"]["maxItems"] == 2
    assert "Höchstens 2 Empfehlungen" in provider.messages[0][0]["content"]


def _completed_session(pack, answers=ANSWERS):
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    for field_key, value in answers.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return session


def test_service_persists_generated_recommendations_on_the_session(tmp_path):
    pack = load_domain_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = _completed_session(pack)
    assessments.save(session)

    items = generate_llm_recommendations(
        assessments, session.session_id, FakeProvider({"recommendations": [ITEM]}),
        "http://fake", "fake", load_pack=lambda domain_id, version: pack,
    )

    stored = assessments.get(session.session_id)
    assert [r.recommendation_id for r in items] == ["hydraulic-balancing"]
    assert stored.result.knowledge_recommendations == items
    assert stored.result.knowledge_generation.model == "fake"
    assert stored.result.knowledge_generation.prompt_version == PROMPT_VERSION
    assert stored.result.knowledge_generation.error_code is None
    assert stored.result.recommendations == session.result.recommendations


def test_service_respects_the_pack_limit(tmp_path):
    pack = load_domain_pack().model_copy(
        update={"knowledge_recommendations": KnowledgeRecommendationSettings(max_count=3)}
    )
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = _completed_session(pack)
    assessments.save(session)
    provider = FakeProvider({"recommendations": [ITEM]})

    generate_llm_recommendations(
        assessments, session.session_id, provider, "http://fake", "fake",
        load_pack=lambda domain_id, version: pack,
    )

    assert provider.schemas[0]["properties"]["recommendations"]["maxItems"] == 3


def test_report_separates_cited_and_uncited_knowledge_recommendations():
    pack = load_domain_pack()
    session = _completed_session(pack)
    session.result.knowledge_recommendations = [_cited_item(), _llm_item()]
    session.result.knowledge_generation = _generation()

    content = render_markdown_report(pack, session, session.result).content

    knowledge = content.split("## Hinweise aus Wissensquellen (KI-formuliert)")[1]
    assert "keine verbindliche Beratung" in content
    cited, uncited = knowledge.split("### Allgemeines KI-Fachwissen")
    assert "Hydraulischen Abgleich prüfen" in cited
    assert "Quelle: Dokument „Leitfaden Heizen“, Seite 4" in cited
    assert "Bezug: Woher stammt der Großteil der Einnahmen? (Öffentliche Förderungen)" in cited
    assert "Ungeprüft: Zahlenangabe 7 500 € steht nicht in der zitierten Quelle" in cited
    assert "Thermostatventile tauschen" in uncited
    assert "Thermostatventile" not in cited
    assert "Modell: fake" in knowledge


def test_open_result_conversation_shows_persisted_recommendations_instead_of_offer(tmp_path):
    pack = load_domain_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = _completed_session(pack)
    session.result.knowledge_recommendations = [_cited_item()]
    session.result.knowledge_generation = _generation()
    assessments.save(session)
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(Profile.from_session(session))
    provider = FakeProvider({"recommendations": []})
    service = ConversationService(
        LocalConversationRepository(tmp_path / "conversations.json"),
        profiles, provider, load_pack=lambda domain_id, version: pack,
    )

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    stored = service.get(conversation.conversation_id)
    assert [m.role for m in stored.messages] == ["assistant", "assistant", "assistant"]
    assert "Ehrenamt sichern" in stored.messages[1].content
    assert stored.messages[2].content.startswith("**Zusätzliche KI-Empfehlungen**")
    assert "Hydraulischen Abgleich prüfen" in stored.messages[2].content
    assert "Leitfaden Heizen" in stored.messages[2].content
    assert RECOMMENDATION_OFFER not in [m.content for m in stored.messages]
    assert [s.kind for s in stored.messages[2].sections[0].sources][0] == "document"
    assert provider.schemas == []


def test_open_result_conversation_offers_generation_after_a_failed_attempt(tmp_path):
    pack = load_domain_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = _completed_session(pack)
    session.result.knowledge_generation = _generation(passages=[], error_code="provider_error")
    assessments.save(session)
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(Profile.from_session(session))
    service = ConversationService(
        LocalConversationRepository(tmp_path / "conversations.json"),
        profiles, FakeProvider({"recommendations": []}),
        load_pack=lambda domain_id, version: pack,
    )

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    assert service.get(conversation.conversation_id).messages[-1].content == RECOMMENDATION_OFFER


def test_contract_result_keeps_rule_shape_and_adds_knowledge_fields(tmp_path):
    orchestrator = _orchestrator(tmp_path, FakeRecommender())
    session_id = _reviewed_session(orchestrator)
    completed = orchestrator.complete(session_id, endpoint="http://fake", model="fake")

    contract = to_contract_response(completed)

    assert {r.source for r in contract.result.recommendations} == {"rule"}
    assert contract.result.knowledge_recommendations[0].source == "llm"
    assert contract.result.knowledge_recommendations[0].sources[0].kind == "document"
    assert contract.result.knowledge_generation.model_dump(mode="json").keys() == {
        "model", "prompt_version", "generated_at", "error_code", "passage_count",
        "retrieval_mode", "embedding_model", "rerank_model", "tool_calculation_ids",
    }
    assert contract.result.knowledge_generation.retrieval_mode == "lexical"
    assert contract.result.knowledge_generation.tool_calculation_ids == []


def test_orchestrator_logs_generation_without_content(tmp_path, caplog):
    import logging

    orchestrator = _orchestrator(tmp_path, FakeRecommender())
    session_id = _reviewed_session(orchestrator)

    with caplog.at_level(logging.INFO, logger="guideos"):
        orchestrator.complete(session_id, endpoint="http://fake", model="fake")

    messages = [r.getMessage() for r in caplog.records if r.name.startswith("guideos")]
    assert any(
        f"knowledge_recommendations_persisted session_id={session_id} count=1" in m
        for m in messages
    )
    assert not any("Hydraulischen" in m for m in messages)


@pytest.mark.parametrize("value", [0, 11])
def test_knowledge_settings_reject_out_of_range_counts(value):
    with pytest.raises(ValueError):
        KnowledgeRecommendationSettings(max_count=value)


def test_v1_complete_with_model_persists_knowledge_recommendations(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from guideos.adapters import fastapi as adapter

    provider = FakeProvider({"recommendations": [ITEM]})
    monkeypatch.setattr(adapter, "get_provider", lambda name: provider)
    client = TestClient(
        adapter.create_app(tmp_path / "assessments.json", knowledge_path=tmp_path / "knowledge.json")
    )
    session_id = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}
    ).json()["session_id"]
    inputs = f"/api/v1/assessment-sessions/{session_id}/inputs"
    for question_id, value in ANSWERS.items():
        client.post(inputs, json={"kind": "submit_answer", "question_id": question_id, "value": value})

    completed = client.post(
        inputs, json={"kind": "complete", "endpoint": "http://fake", "model": "fake"}
    ).json()

    assert completed["ok"] and completed["status"] == "completed"
    assert [r["recommendation_id"] for r in completed["result"]["knowledge_recommendations"]] == [
        "hydraulic-balancing"
    ]
    assert completed["result"]["knowledge_generation"]["model"] == "fake"
    assert completed["result"]["knowledge_generation"]["passage_count"] == 0
    stored = client.get(f"/api/v1/assessment-sessions/{session_id}").json()
    assert stored["result"]["knowledge_recommendations"] == completed["result"]["knowledge_recommendations"]
    assert "Hinweise aus Wissensquellen" in stored["report"]["content"]


def test_v1_complete_without_model_stays_deterministic(tmp_path):
    from fastapi.testclient import TestClient

    from guideos.adapters.fastapi import create_app

    client = TestClient(create_app(tmp_path / "assessments.json"))
    session_id = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}
    ).json()["session_id"]
    inputs = f"/api/v1/assessment-sessions/{session_id}/inputs"
    for question_id, value in ANSWERS.items():
        client.post(inputs, json={"kind": "submit_answer", "question_id": question_id, "value": value})

    completed = client.post(inputs, json={"kind": "complete"}).json()

    assert completed["ok"]
    assert completed["result"]["knowledge_recommendations"] == []
    assert completed["result"]["knowledge_generation"] is None
