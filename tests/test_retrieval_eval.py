"""Retrieval evaluation: does the knowledge search find the expected passages
per domain, and how do the modes lexical, hybrid and rerank compare?"""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from guideos.domains.catalog import load_active_domain_pack
from guideos.domains.loader import load_domain_pack
from guideos.knowledge.models import KnowledgeChunk, KnowledgeDocument, RetrievedPassage
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore
from testsuite.retrieval_eval import (EvalSet, bundled_eval_sets, evaluate, hit_rank, load_eval_set,
                                      render_markdown)


def _passage(title, locator):
    return RetrievedPassage(source_id="D1", kind="document", document_id="d", title=title, locator=locator,
                            content_hash="h", retrieved_at=datetime.now(UTC), text="x", score=1.0)


def test_hit_rank_matches_title_and_optional_pages():
    passages = [_passage("Heizung", "Seite 3"), _passage("Dämmung", "Seite 2"), _passage("Heizung", "Seite 16")]
    assert hit_rank(passages, [{"title": "Heizung", "pages": [16]}]) == 3
    assert hit_rank(passages, [{"title": "Dämmung"}]) == 2
    assert hit_rank(passages, [{"title": "Heizung", "pages": [16, 3]}]) == 1
    assert hit_rank(passages, [{"title": "Strom"}]) is None


def _document(document_id, title, texts, embedding_model=None, vectors=None):
    chunks = [KnowledgeChunk(locator=f"Seite {index + 1}", text=text,
                             embedding=vectors[index] if vectors else None) for index, text in enumerate(texts)]
    return KnowledgeDocument(document_id=document_id, domain_id="verein-demo", kind="document", title=title,
                             filename=f"{document_id}.pdf", content_hash=document_id, created_at=datetime.now(UTC),
                             embedding_model=embedding_model, chunks=chunks)


class FakeProvider:
    """Embeddings count two keywords; the reranker prefers texts with 'Vorlauf'."""
    default_endpoint = "http://unused"
    default_model = "fake"

    def embed(self, endpoint, model, texts):
        return [[float("wärmepumpe" in t.lower()), float("dämm" in t.lower())] for t in texts]

    def rerank(self, endpoint, model, query, texts):
        return [1.0 if "Vorlauf" in t else 0.1 for t in texts]


def _eval_set():
    return EvalSet.model_validate({"schema_version": 1, "domain_id": "verein-demo", "queries": [
        {"id": "wp", "query": "Wärmepumpe Vorlauftemperatur",
         "expected": [{"title": "Heizung", "pages": [2]}]},
        {"id": "daemm", "query": "Dämmung der Außenwand", "expected": [{"title": "Dämmung"}]},
        {"id": "nix", "query": "Photovoltaik Speicher", "expected": [{"title": "Strom"}]},
    ]})


def test_evaluate_compares_modes_and_reports_ranks_and_metrics(tmp_path):
    store = LocalKnowledgeStore(tmp_path / "knowledge.json")
    store.save(_document("d1", "Heizung", ["Wärmepumpe im Altbau", "Wärmepumpe mit niedriger Vorlauftemperatur"],
                         "fake", [[1.0, 0.0], [1.0, 0.0]]))
    store.save(_document("d2", "Dämmung", ["Dämmung der Außenwand senkt den Bedarf"], "fake", [[0.0, 1.0]]))
    provider = FakeProvider()
    services = {
        "lexical": KnowledgeService(store, provider=provider),
        "hybrid": KnowledgeService(store, provider=provider, embedding_model="fake"),
        "rerank": KnowledgeService(store, provider=provider, embedding_model="fake", rerank_model="judge"),
    }

    report = evaluate(services, load_domain_pack(), _eval_set(), "http://unused", k=3)

    modes = report["modes"]
    assert [modes[name]["mode"] for name in ("lexical", "hybrid", "rerank")] == ["lexical", "hybrid", "hybrid+rerank"]
    ranks = {name: {q["id"]: q["rank"] for q in modes[name]["queries"]} for name in modes}
    assert ranks["lexical"]["daemm"] == 1 and ranks["lexical"]["nix"] is None
    assert ranks["rerank"]["wp"] == 1  # the reranker lifts the Vorlauf passage to the top
    assert modes["rerank"]["hit_rate"] == pytest.approx(2 / 3)
    assert modes["rerank"]["mrr"] == pytest.approx((1 + 1) / 3)
    assert all(q["seconds"] >= 0 for q in modes["lexical"]["queries"])
    assert report["k"] == 3 and report["domain_id"] == "verein-demo"

    text = render_markdown(report)
    assert "| Modus |" in text and "hybrid+rerank" in text
    assert "| wp |" in text and "–" in text  # missing hits are shown as a dash


@pytest.mark.parametrize("changes", [
    {"queries": []},
    {"queries": [{"id": "a", "query": "x", "expected": []}]},
    {"queries": [{"id": "a", "query": "x", "expected": [{"title": "T"}]},
                 {"id": "a", "query": "y", "expected": [{"title": "T"}]}]},
    {"queries": [{"id": "a", "query": "x", "expected": [{"title": "T", "page": 3}]}]},
])
def test_eval_set_rejects_empty_duplicate_or_unknown_fields(changes):
    data = {"schema_version": 1, "domain_id": "verein-demo",
            "queries": [{"id": "a", "query": "x", "expected": [{"title": "T"}]}], **changes}
    with pytest.raises(ValidationError):
        EvalSet.model_validate(data)


def test_bundled_eval_sets_name_active_domains_and_round_trip_through_files(tmp_path):
    sets = bundled_eval_sets()
    for eval_set in sets:
        load_active_domain_pack(eval_set.domain_id)
        assert len(eval_set.queries) >= 15
    # Each set lives in its own file next to the others; a directory without sets is empty, not an error.
    assert bundled_eval_sets(tmp_path) == []
    directory = tmp_path / "sets"
    directory.mkdir()
    sample = sets[0] if sets else _eval_set()
    path = directory / f"{sample.domain_id}.json"
    path.write_text(json.dumps(sample.model_dump()), encoding="utf-8")
    assert load_eval_set(path) == sample
    assert bundled_eval_sets(directory) == [sample]
