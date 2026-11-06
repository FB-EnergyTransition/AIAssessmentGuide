import json
import logging

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.domains.loader import load_domain_pack
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore
from guideos.providers.base import ProviderError
from guideos.recommendations.service import (
    RecommendationServiceError,
    generate_llm_recommendations,
)
from guideos.rules.engine import evaluate_assessment


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        if self.error:
            raise self.error
        return self.result


ITEM = {
    "recommendation_id": "hydraulic-balancing",
    "title": "Hydraulischen Abgleich prüfen",
    "message": "Senkt den Verbrauch.",
    "rationale": "Gasheizung.",
    "evidence_fields": ["funding_source"],
    "confidence": 0.7,
}


def test_service_generates_recommendations_for_completed_session(tmp_path):
    assessments, session, pack = _repository(tmp_path)

    recommendations = generate_llm_recommendations(
        assessments, session.session_id, FakeProvider({"recommendations": [ITEM]}),
        "http://fake", "fake", load_pack=lambda domain_id, version: pack,
    )

    assert [(r.recommendation_id, r.source) for r in recommendations] == [("hydraulic-balancing", "llm")]


def test_service_reports_stable_error_codes(tmp_path):
    assessments, session, pack = _repository(tmp_path)
    unfinished = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    assessments.save(unfinished)
    load_pack = lambda domain_id, version: pack  # noqa: E731

    with pytest.raises(LookupError):
        generate_llm_recommendations(assessments, "sess_fehlt", FakeProvider(), "http://fake", "fake", load_pack=load_pack)
    with pytest.raises(RecommendationServiceError) as unfinished_error:
        generate_llm_recommendations(assessments, unfinished.session_id, FakeProvider(), "http://fake", "fake", load_pack=load_pack)
    with pytest.raises(RecommendationServiceError) as provider_error:
        generate_llm_recommendations(
            assessments, session.session_id, FakeProvider(error=ProviderError("weg")), "http://fake", "fake", load_pack=load_pack,
        )
    with pytest.raises(RecommendationServiceError) as malformed_error:
        generate_llm_recommendations(
            assessments, session.session_id, FakeProvider({"recommendations": [{"title": "x"}]}), "http://fake", "fake", load_pack=load_pack,
        )
    session.domain_content_hash = "b" * 64
    assessments.save(session)
    with pytest.raises(RecommendationServiceError) as drift_error:
        generate_llm_recommendations(assessments, session.session_id, FakeProvider(), "http://fake", "fake", load_pack=load_pack)

    assert unfinished_error.value.code == "invalid_state"
    assert provider_error.value.code == "provider_error" and "weg" in str(provider_error.value)
    assert malformed_error.value.code == "provider_error"
    assert drift_error.value.code == "domain_version_unavailable"


def _repository(tmp_path):
    pack = load_domain_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    for field_key, value in {
        "club_type": "sports", "founded_year": 1980, "members_count": 120,
        "funding_source": "public_grants", "volunteer_situation": "tight", "annual_budget_eur": None,
    }.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    assessments.save(session)
    return assessments, session, pack


def test_service_logs_generated_recommendation_count(tmp_path, caplog):
    assessments, session, pack = _repository(tmp_path)

    with caplog.at_level(logging.INFO, logger="guideos"):
        generate_llm_recommendations(
            assessments, session.session_id, FakeProvider({"recommendations": [ITEM]}),
            "http://fake", "fake", load_pack=lambda domain_id, version: pack,
        )

    messages = [
        r.getMessage() for r in caplog.records if r.name == "guideos.recommendations.service"
    ]
    assert any(
        f"llm_recommendations_generated session_id={session.session_id}" in m and "count=1" in m
        for m in messages
    )
    assert not any("Hydraulischen" in m for m in messages)


class RecordingProvider(FakeProvider):
    def generate_structured(self, endpoint, model, messages, schema):
        self.messages = messages
        return self.result


def test_service_retrieves_knowledge_passages_for_the_session(tmp_path):
    assessments, session, pack = _repository(tmp_path)
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))
    knowledge.add_document(
        "verein-demo", "leitfaden.md",
        "# Gebäudehülle\n\nEine gedämmte Fassade senkt den Heizwärmebedarf.".encode(),
        "http://fake",
    )
    provider = RecordingProvider({"recommendations": [{**ITEM, "source_ids": ["D1"]}]})

    [item] = generate_llm_recommendations(
        assessments, session.session_id, provider, "http://fake", "fake",
        load_pack=lambda domain_id, version: pack, knowledge=knowledge,
    )

    payload = json.loads(provider.messages[1]["content"])
    assert [p["source_id"] for p in payload["knowledge_sources"]] == ["D1"]
    assert [(s.kind, s.title, s.locator) for s in item.sources] == [
        ("document", "Gebäudehülle", "Gebäudehülle")
    ]


def test_service_reports_an_unavailable_knowledge_base(tmp_path):
    assessments, session, pack = _repository(tmp_path)
    (tmp_path / "knowledge.json").write_text("kaputt", encoding="utf-8")
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))

    with pytest.raises(RecommendationServiceError) as excinfo:
        generate_llm_recommendations(
            assessments, session.session_id, FakeProvider({"recommendations": []}),
            "http://fake", "fake", load_pack=lambda domain_id, version: pack, knowledge=knowledge,
        )

    assert excinfo.value.code == "knowledge_unavailable"
    assert "Wissensbasis" in str(excinfo.value)


def test_generation_records_the_calculation_ids_the_model_saw(tmp_path):
    from guideos.context import build_advisory_context
    from guideos.recommendations.service import generate_for_context
    from guideos.tools.models import ToolCall, ToolResult

    assessments, session, pack = _repository(tmp_path)
    session.tool_results = [
        ToolResult(call=ToolCall(name="analyze_tiers", arguments={}), server_name="s",
                   structured_content={}, summary="A.", calculation_id="calc-tiers-1"),
        ToolResult(call=ToolCall(name="analyze_windows", arguments={}), server_name="s",
                   structured_content={}, summary="B.", calculation_id=None),
    ]

    _, generation, _ = generate_for_context(
        pack, build_advisory_context(pack, session), session.session_id,
        FakeProvider({"recommendations": [ITEM]}), "http://fake", "fake",
    )

    assert generation.tool_calculation_ids == ["calc-tiers-1"]
    assert generation.prompt_version == 5
