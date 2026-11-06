"""Hybrid retrieval with an optional reranker (concept step 6): BM25 and
embeddings fused by reciprocal rank, a provider reranker on top, lexical
fallback, and the mode recorded with every generation."""

import json
import logging

from guideos.domains.loader import load_domain_pack
from guideos.knowledge.search import reciprocal_rank_fusion
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore
from guideos.providers.base import ProviderError

HEATING = "# Heizung\n\nEin Heizungstausch auf eine Wärmepumpe senkt den Verbrauch."
INSULATION = "# Dämmung\n\nEine gedämmte Fassade senkt den Wärmebedarf deutlich."
WINDOWS = "# Fenster\n\nNeue Fenster halten die Wärme im Haus."


class FakeProvider:
    """Embeds as [mentions heating, mentions insulation, mentions windows];
    reranks by an explicit score table or fails on demand."""

    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, rerank_scores=None, rerank_error=None):
        self.rerank_scores = rerank_scores or {}
        self.rerank_error = rerank_error
        self.rerank_calls = []

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        return {}

    def list_models(self, endpoint):
        return []

    def embed(self, endpoint, model, texts):
        return [
            [float("heiz" in t.lower()), float("dämm" in t.lower()), float("fenster" in t.lower())]
            for t in texts
        ]

    def rerank(self, endpoint, model, query, texts):
        self.rerank_calls.append((endpoint, model, query, list(texts)))
        if self.rerank_error:
            raise self.rerank_error
        return [
            next((score for needle, score in self.rerank_scores.items() if needle in text.lower()), 0.0)
            for text in texts
        ]


def _service(tmp_path, provider, embedding_model=None, rerank_model=None):
    service = KnowledgeService(
        LocalKnowledgeStore(tmp_path / "knowledge.json"), provider=provider,
        embedding_model=embedding_model, rerank_model=rerank_model,
    )
    for name, text in (("heizung.md", HEATING), ("daemmung.md", INSULATION), ("fenster.md", WINDOWS)):
        service.add_document("verein-demo", name, text.encode(), "http://fake")
    return service


def test_reciprocal_rank_fusion_rewards_items_ranked_well_in_both_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d", "a"]], k=60)

    assert [item for item, _ in fused][:2] == ["b", "a"]
    assert fused[0][1] > fused[1][1] > fused[-1][1]
    assert reciprocal_rank_fusion([]) == []


def test_without_embeddings_the_search_is_lexical(tmp_path):
    service = _service(tmp_path, FakeProvider())
    pack = load_domain_pack()

    passages, mode = service.search_with_mode(pack, "Dämmung der Fassade", "http://fake")

    assert mode == "lexical"
    assert passages[0].title == "Dämmung"
    assert service.search(pack, "Dämmung der Fassade", "http://fake") == passages


def test_with_embeddings_lexical_and_semantic_rankings_are_fused(tmp_path):
    service = _service(tmp_path, FakeProvider(), embedding_model="fake-embed")
    pack = load_domain_pack()

    # "Wärmepumpe" only matches the heating text lexically; the embedding says "heating" too.
    passages, mode = service.search_with_mode(pack, "Heizung Wärmepumpe", "http://fake")

    assert mode == "hybrid"
    assert passages[0].title == "Heizung"
    # A text that only the lexical ranking finds still makes the list.
    assert {p.title for p in passages} >= {"Heizung"}


def test_reranker_reorders_the_fused_candidates(tmp_path, caplog):
    provider = FakeProvider(rerank_scores={"dämm": 0.9, "heiz": 0.2})
    service = _service(tmp_path, provider, embedding_model="fake-embed", rerank_model="fake-rerank")
    pack = load_domain_pack()

    with caplog.at_level(logging.INFO, logger="guideos"):
        passages, mode = service.search_with_mode(pack, "Heizung Dämmung", "http://fake")

    assert mode == "hybrid+rerank"
    assert passages[0].title == "Dämmung"
    endpoint, model, query, texts = provider.rerank_calls[0]
    assert (endpoint, model, query) == ("http://fake", "fake-rerank", "Heizung Dämmung")
    assert len(texts) >= 2
    assert any("mode=hybrid+rerank" in r.getMessage() for r in caplog.records)
    assert not any("Dämmung" in r.getMessage() for r in caplog.records)


def test_reranker_failure_falls_back_to_the_fused_order_with_a_warning(tmp_path, caplog):
    provider = FakeProvider(rerank_error=ProviderError("Reranker weg"))
    service = _service(tmp_path, provider, rerank_model="fake-rerank")
    pack = load_domain_pack()

    with caplog.at_level(logging.WARNING, logger="guideos"):
        passages, mode = service.search_with_mode(pack, "Dämmung der Fassade", "http://fake")

    assert mode == "lexical"
    assert passages[0].title == "Dämmung"
    assert any("rerank_failed" in r.getMessage() for r in caplog.records)


def test_factory_reads_the_rerank_model_from_the_environment(tmp_path, monkeypatch):
    from guideos.knowledge.factory import knowledge_service_from_env

    monkeypatch.setenv("GUIDEOS_KNOWLEDGE_PATH", str(tmp_path / "k.json"))
    monkeypatch.setenv("GUIDEOS_RERANK_MODEL", " my-reranker ")
    monkeypatch.delenv("GUIDEOS_EMBEDDING_MODEL", raising=False)

    service = knowledge_service_from_env(FakeProvider())

    assert service.rerank_model == "my-reranker" and service.embedding_model is None


def test_generation_metadata_records_the_retrieval_mode(tmp_path):
    from guideos.assessment.models import AssessmentAnswer, AssessmentSession
    from guideos.context import build_advisory_context
    from guideos.recommendations.service import generate_for_context
    from guideos.rules.engine import evaluate_assessment

    class RecommendingProvider(FakeProvider):
        def generate_structured(self, endpoint, model, messages, schema):
            return {"recommendations": []}

    provider = RecommendingProvider()
    service = _service(tmp_path, provider, embedding_model="fake-embed", rerank_model="fake-rerank")
    pack = load_domain_pack()
    session = AssessmentSession.create(domain_id=pack.domain_id, domain_version=pack.version,
                                       domain_content_hash=pack.content_hash, locale="de")
    for k, v in {"club_type": "sports", "founded_year": 1980, "members_count": 120,
                 "funding_source": "public_grants", "volunteer_situation": "tight", "annual_budget_eur": None}.items():
        session.answers[k] = AssessmentAnswer(question_id=k, field_key=k, raw_answer=str(v), normalized_value=v, confidence=1)
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)

    _, generation, _ = generate_for_context(
        pack, build_advisory_context(pack, session), "s", provider, "http://fake", "fake", knowledge=service
    )

    assert generation.retrieval_mode == "hybrid+rerank"
    assert generation.embedding_model == "fake-embed" and generation.rerank_model == "fake-rerank"
    assert len(generation.passages) >= 1


def test_ollama_reranks_by_asking_the_model_for_scores(monkeypatch):
    from guideos.providers.ollama import OllamaProvider

    captured = {}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(chat_request, timeout):
        captured["body"] = json.loads(chat_request.data.decode("utf-8"))
        captured["url"] = chat_request.full_url
        return FakeResponse({"message": {"role": "assistant", "content": json.dumps({"scores": [0.2, 0.9]})}})

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    scores = OllamaProvider().rerank("http://ollama", "reranker", "Dämmung", ["Heizung", "Dämmung"])

    assert scores == [0.2, 0.9]
    assert captured["url"].endswith("/api/chat") and captured["body"]["model"] == "reranker"
    assert captured["body"]["options"]["temperature"] == 0
    assert "Dämmung" in captured["body"]["messages"][-1]["content"]

    def wrong_length(chat_request, timeout):
        return FakeResponse({"message": {"role": "assistant", "content": json.dumps({"scores": [0.5]})}})

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", wrong_length)
    import pytest

    with pytest.raises(ProviderError):
        OllamaProvider().rerank("http://ollama", "reranker", "x", ["a", "b"])
