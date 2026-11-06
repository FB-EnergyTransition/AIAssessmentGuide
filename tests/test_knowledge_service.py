import logging
from datetime import UTC, datetime

import pytest

from guideos.domains.loader import load_domain_pack
from guideos.domains.models import WebSourceSettings
from guideos.knowledge.factory import knowledge_service_from_env
from guideos.knowledge.ingestion import FetchedPage, KnowledgeError
from guideos.knowledge.models import KnowledgeChunk, KnowledgeDocument
from guideos.knowledge.search import cosine_scores, lexical_scores
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import KnowledgeStoreError, LocalKnowledgeStore
from guideos.providers.base import ProviderError
from tests.knowledge_fixtures import make_pdf


HEATING = "# Heizung\n\nDer Tausch der Heizung gegen eine Wärmepumpe wird gefördert."
INSULATION = "# Dämmung\n\nEine gedämmte Fassade senkt den Wärmebedarf deutlich."


class FakeEmbeddingProvider:
    """Embeds a text as [mentions heating, mentions insulation]."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def embed(self, endpoint, model, texts):
        self.calls.append((endpoint, model, list(texts)))
        if self.error:
            raise self.error
        return [
            [float("heiz" in text.lower()), float("dämm" in text.lower()) + 0.01]
            for text in texts
        ]


def test_lexical_scores_match_german_compounds_and_inflections():
    texts = [
        "Der Tausch der Heizung wird gefördert.",
        "Fenster sollten dreifach verglast sein.",
        "Im Sommer hilft eine Beschattung.",
    ]

    scores = lexical_scores("Heizungstausch Förderung", texts)

    assert scores[0] > 0
    assert scores[0] > scores[1] and scores[0] > scores[2]
    assert lexical_scores("Photovoltaik", texts) == [0.0, 0.0, 0.0]
    assert lexical_scores("", texts) == [0.0, 0.0, 0.0]


def test_cosine_scores_rank_by_direction_not_length():
    scores = cosine_scores([1.0, 0.0], [[5.0, 0.0], [1.0, 1.0], [0.0, 2.0], [0.0, 0.0]])

    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.7071, abs=1e-4)
    assert scores[2:] == [0.0, 0.0]


def test_local_store_roundtrips_documents_per_domain(tmp_path):
    store = LocalKnowledgeStore(tmp_path / "knowledge.json")
    first = _document("doc_1", "verein-demo")
    second = _document("doc_2", "other-demo")

    store.save(first)
    store.save(second)
    store.save(first.model_copy(update={"title": "Neu"}))

    reloaded = LocalKnowledgeStore(tmp_path / "knowledge.json")
    assert [(d.document_id, d.title) for d in reloaded.list("verein-demo")] == [("doc_1", "Neu")]
    assert reloaded.list("other-demo") == [second]
    reloaded.delete("doc_1")
    assert reloaded.list("verein-demo") == []
    with pytest.raises(LookupError):
        reloaded.delete("doc_1")


@pytest.mark.parametrize(
    "content",
    ["kein json", "[]", '{"version": 99, "documents": []}', '{"version": 1, "documents": [{}]}'],
)
def test_local_store_rejects_malformed_files_in_german(tmp_path, content):
    path = tmp_path / "knowledge.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(KnowledgeStoreError, match="Wissensbasis"):
        LocalKnowledgeStore(path).list("verein-demo")


def test_add_document_stores_chunks_with_hash_and_title(tmp_path):
    service = _service(tmp_path)

    document = service.add_document(
        "verein-demo", "leitfaden.pdf", make_pdf(["Seite eins", "Seite zwei"]), "http://fake"
    )
    titled = service.add_document(
        "verein-demo", "notiz.txt", b"Inhalt", "http://fake", title=" Interne Notiz "
    )

    assert document.kind == "document"
    assert document.title == "leitfaden" and document.filename == "leitfaden.pdf"
    assert document.document_id.startswith("doc_")
    assert len(document.content_hash) == 64
    assert document.embedding_model is None
    assert [(c.locator, c.text, c.embedding) for c in document.chunks] == [
        ("Seite 1", "Seite eins", None),
        ("Seite 2", "Seite zwei", None),
    ]
    assert titled.title == "Interne Notiz"
    assert service.list_documents("verein-demo") == [document, titled]
    assert service.list_documents("other-demo") == []


def test_add_document_rejects_duplicates_and_unusable_files(tmp_path):
    service = _service(tmp_path)
    service.add_document("verein-demo", "a.txt", b"Gleicher Inhalt", "http://fake")

    with pytest.raises(KnowledgeError, match="bereits in der Wissensbasis"):
        service.add_document("verein-demo", "b.txt", b"Gleicher Inhalt", "http://fake")
    with pytest.raises(KnowledgeError, match="Unterstützt werden"):
        service.add_document("verein-demo", "a.docx", b"x", "http://fake")

    service.add_document("other-demo", "a.txt", b"Gleicher Inhalt", "http://fake")
    assert len(service.list_documents("verein-demo")) == 1


def test_add_web_page_stores_final_url_and_versions_an_earlier_fetch(tmp_path):
    pages = iter([_page("<title>Alt</title><p>Alter Stand</p>"),
                  _page("<title>Neu</title><p>Neuer Stand</p>")])
    service = _service(tmp_path, fetch=lambda url: next(pages))

    first = service.add_web_page("verein-demo", "https://klimaaktiv.at/heizung", "http://fake")
    second = service.add_web_page("verein-demo", "https://klimaaktiv.at/heizung", "http://fake")

    assert first.kind == "web" and first.title == "Alt"
    assert first.url == "https://www.klimaaktiv.at/heizung" and first.filename is None
    stored = {d.document_id: d for d in service.list_documents("verein-demo")}
    # The earlier fetch stays as a superseded source version, the new one is current.
    assert set(stored) == {first.document_id, second.document_id}
    assert stored[first.document_id].superseded_by == second.document_id
    assert stored[second.document_id].superseded_by is None
    assert stored[second.document_id].title == "Neu" and stored[second.document_id].chunks[0].text == "Neuer Stand"


def test_add_web_page_enforces_the_allowed_domains_of_the_pack(tmp_path):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return _page("<p>Text</p>", url="https://tracker.example.com/ziel")

    service = _service(tmp_path, fetch=fetch)

    with pytest.raises(KnowledgeError, match="example.com ist für diese Domain nicht erlaubt"):
        service.add_web_page(
            "verein-demo", "https://example.com/x", "http://fake", allowed_domains=["klimaaktiv.at"]
        )
    assert fetched == []
    with pytest.raises(KnowledgeError, match="tracker.example.com ist für diese Domain"):
        service.add_web_page(
            "verein-demo", "https://www.klimaaktiv.at/x", "http://fake",
            allowed_domains=["klimaaktiv.at"],
        )
    with pytest.raises(KnowledgeError, match="http- oder https-Adresse"):
        service.add_web_page(
            "verein-demo", "klimaaktiv.at/x", "http://fake", allowed_domains=["klimaaktiv.at"]
        )
    assert service.list_documents("verein-demo") == []


def test_factory_reads_storage_path_and_embedding_model_from_the_environment(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    monkeypatch.setenv("GUIDEOS_KNOWLEDGE_PATH", str(tmp_path / "wissen.json"))
    monkeypatch.delenv("GUIDEOS_EMBEDDING_MODEL", raising=False)
    provider = FakeEmbeddingProvider()

    lexical = knowledge_service_from_env(provider)
    monkeypatch.setenv("GUIDEOS_EMBEDDING_MODEL", " bge-m3 ")
    embedding = knowledge_service_from_env(provider)

    assert lexical.store.path == tmp_path / "wissen.json"
    assert lexical.embedding_model is None
    assert embedding.embedding_model == "bge-m3"


def test_factory_prefers_postgres_when_a_database_is_configured(monkeypatch):
    from guideos.knowledge.postgres_store import PostgresKnowledgeStore

    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://guideos@localhost/guideos")

    store = knowledge_service_from_env(FakeEmbeddingProvider()).store

    assert isinstance(store, PostgresKnowledgeStore)


def test_search_returns_citable_passages_ranked_by_relevance(tmp_path):
    service = _service(tmp_path)
    heating = service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")
    service.add_document("verein-demo", "daemmung.md", INSULATION.encode(), "http://fake")

    passages = service.search(load_domain_pack(), "Wird der Heizungstausch gefördert?", "http://fake")

    assert passages[0].source_id == "D1"
    assert passages[0].document_id == heating.document_id
    assert passages[0].kind == "document" and passages[0].title == "Heizung"
    assert passages[0].locator == "Heizung"
    assert passages[0].content_hash == heating.content_hash
    assert passages[0].retrieved_at == heating.created_at
    assert "Wärmepumpe" in passages[0].text
    assert [p.source_id for p in passages] == [f"D{n}" for n in range(1, len(passages) + 1)]
    assert passages == sorted(passages, key=lambda p: p.score, reverse=True)


def test_search_without_documents_or_matches_returns_nothing_and_needs_no_provider(tmp_path):
    service = _service(tmp_path)
    pack = load_domain_pack()

    assert service.search(pack, "Heizung", "http://fake") == []
    service.add_document("other-demo", "heizung.md", HEATING.encode(), "http://fake")
    assert service.search(pack, "Heizung", "http://fake") == []
    service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")
    assert service.search(pack, "Photovoltaik", "http://fake") == []
    assert service.search(pack, "   ", "http://fake") == []


def test_search_limits_the_number_of_passages(tmp_path):
    service = _service(tmp_path)
    text = "\n\n".join(f"# Heizung {n}\n\nHeizung Variante {n}." for n in range(8))
    service.add_document("verein-demo", "viele.md", text.encode(), "http://fake")

    assert len(service.search(load_domain_pack(), "Heizung", "http://fake")) == 5
    assert len(service.search(load_domain_pack(), "Heizung", "http://fake", limit=2)) == 2


def test_web_pages_are_only_searched_when_the_pack_enables_them(tmp_path):
    service = _service(
        tmp_path,
        fetch=lambda url: _page("<title>Web</title><p>Heizung tauschen lohnt sich.</p>", url=url),
    )
    service.add_web_page("verein-demo", "https://www.klimaaktiv.at/heizung", "http://fake")
    service.add_web_page("verein-demo", "https://example.com/heizung", "http://fake")
    service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")
    pack = load_domain_pack()

    def kinds(web_sources):
        configured = pack.model_copy(update={"web_sources": web_sources})
        return sorted(
            (p.source_id, p.url) for p in service.search(configured, "Heizung", "http://fake")
        )

    assert kinds(None) == [("D1", None)]
    assert kinds(WebSourceSettings(enabled=False)) == [("D1", None)]
    assert kinds(WebSourceSettings(enabled=True)) == [
        ("D1", None),
        ("W1", "https://www.klimaaktiv.at/heizung"),
        ("W2", "https://example.com/heizung"),
    ]
    assert kinds(WebSourceSettings(enabled=True, allowed_domains=["klimaaktiv.at"])) == [
        ("D1", None),
        ("W1", "https://www.klimaaktiv.at/heizung"),
    ]


def test_configured_embedding_model_embeds_documents_and_ranks_by_similarity(tmp_path):
    provider = FakeEmbeddingProvider()
    service = _service(tmp_path, provider=provider, embedding_model="bge-m3")

    document = service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://ollama")
    service.add_document("verein-demo", "daemmung.md", INSULATION.encode(), "http://ollama")
    passages = service.search(load_domain_pack(), "Womit heize ich am besten?", "http://ollama")

    assert document.embedding_model == "bge-m3"
    assert document.chunks[0].embedding == [1.0, 0.01]
    assert provider.calls[0] == ("http://ollama", "bge-m3", [document.chunks[0].text])
    assert provider.calls[-1] == ("http://ollama", "bge-m3", ["Womit heize ich am besten?"])
    assert [p.title for p in passages] == ["Heizung", "Dämmung"]
    # Hybrid mode: the score is the fused rank score, not the raw cosine.
    assert passages[0].score > passages[1].score > 0


def test_embedding_failure_does_not_store_the_document(tmp_path):
    provider = FakeEmbeddingProvider(error=ProviderError("Modell fehlt"))
    service = _service(tmp_path, provider=provider, embedding_model="bge-m3")

    with pytest.raises(ProviderError):
        service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://ollama")

    assert service.list_documents("verein-demo") == []


def test_documents_without_current_embeddings_fall_back_to_lexical_search_until_reindexed(
    tmp_path,
):
    _service(tmp_path).add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")
    provider = FakeEmbeddingProvider()
    service = _service(tmp_path, provider=provider, embedding_model="bge-m3")
    pack = load_domain_pack()

    assert service.pending_reindex("verein-demo") == 1
    assert [p.title for p in service.search(pack, "Heizungstausch", "http://ollama")] == ["Heizung"]
    assert provider.calls == []

    assert service.reindex("verein-demo", "http://ollama") == 1

    assert service.pending_reindex("verein-demo") == 0
    stored = service.list_documents("verein-demo")[0]
    assert stored.embedding_model == "bge-m3" and stored.chunks[0].embedding == [1.0, 0.01]
    service.search(pack, "Womit heize ich?", "http://ollama")
    assert provider.calls[-1][2] == ["Womit heize ich?"]


def test_delete_document_removes_it_from_search(tmp_path):
    service = _service(tmp_path)
    document = service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")

    service.delete_document(document.document_id)

    assert service.list_documents("verein-demo") == []
    with pytest.raises(LookupError):
        service.delete_document(document.document_id)


def test_delete_domain_removes_all_documents_of_that_domain_only(tmp_path, caplog):
    service = _service(tmp_path)
    service.add_document("verein-demo", "heizung.md", HEATING.encode(), "http://fake")
    service.add_document("verein-demo", "daemmung.md", INSULATION.encode(), "http://fake")
    kept = service.add_document("other-demo", "heizung.md", HEATING.encode(), "http://fake")

    with caplog.at_level(logging.INFO, logger="guideos"):
        removed = service.delete_domain("verein-demo")

    assert removed == 2
    assert service.list_documents("verein-demo") == []
    assert service.list_documents("other-demo") == [kept]
    assert service.delete_domain("verein-demo") == 0
    assert "knowledge_domain_deleted domain_id=verein-demo documents=2" in [
        r.getMessage() for r in caplog.records
    ]


def test_knowledge_events_are_logged_without_content(tmp_path, caplog):
    service = _service(tmp_path)

    with caplog.at_level(logging.INFO, logger="guideos"):
        document = service.add_document(
            "verein-demo", "geheim.md", HEATING.encode(), "http://fake"
        )
        service.search(load_domain_pack(), "Vertrauliche Frage zur Heizung", "http://fake")
        service.delete_document(document.document_id)

    messages = [r.getMessage() for r in caplog.records if r.name == "guideos.knowledge.service"]
    assert messages == [
        f"knowledge_document_added domain_id=verein-demo document_id={document.document_id} "
        "kind=document chunks=1 embedding_model=None",
        "knowledge_searched domain_id=verein-demo mode=lexical passages=1",
        f"knowledge_document_deleted document_id={document.document_id}",
    ]
    assert not any("Vertrauliche" in m or "geheim" in m or "Wärmepumpe" in m for m in messages)


def _service(tmp_path, provider=None, embedding_model=None, fetch=None):
    return KnowledgeService(
        LocalKnowledgeStore(tmp_path / "knowledge.json"),
        provider=provider,
        embedding_model=embedding_model,
        fetch=fetch or (lambda url: pytest.fail("kein Abruf erwartet")),
    )


def _page(html, url="https://www.klimaaktiv.at/heizung"):
    return FetchedPage(url, html.encode(), "text/html", "utf-8")


def _document(document_id, domain_id):
    return KnowledgeDocument(
        document_id=document_id,
        domain_id=domain_id,
        kind="document",
        title="Titel",
        filename="titel.txt",
        content_hash="a" * 64,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        chunks=[KnowledgeChunk(locator="Seite 1", text="Text", embedding=[0.25, 0.5])],
    )


def test_local_store_keeps_original_files_until_no_document_uses_them(tmp_path):
    store = LocalKnowledgeStore(tmp_path / "knowledge.json")
    first = _document("doc_1", "verein-demo")
    second = _document("doc_2", "other-demo")
    assert first.content_hash == second.content_hash
    store.save(first)
    store.save(second)

    assert store.load_original(first.content_hash) is None
    store.save_original(first.content_hash, b"%PDF-original")

    reloaded = LocalKnowledgeStore(tmp_path / "knowledge.json")
    assert reloaded.load_original(first.content_hash) == b"%PDF-original"
    reloaded.delete("doc_1")
    assert reloaded.load_original(first.content_hash) == b"%PDF-original"
    reloaded.delete("doc_2")
    assert reloaded.load_original(first.content_hash) is None


def test_add_document_keeps_the_original_file(tmp_path):
    service = _service(tmp_path)
    raw = make_pdf(["Erste Seite", "Zweite Seite"])

    document = service.add_document("verein-demo", "leitfaden.pdf", raw, "http://fake")

    assert service.original(document.content_hash) == raw
    assert service.original("f" * 64) is None
    service.delete_document(document.document_id)
    assert service.original(document.content_hash) is None


def test_uploading_a_known_document_again_supplies_its_missing_original(tmp_path):
    service = _service(tmp_path)
    document = service.add_document("verein-demo", "a.txt", b"Gleicher Inhalt", "http://fake")
    # Documents stored before originals were kept have none.
    service.store.delete_original(document.content_hash)
    assert service.original(document.content_hash) is None

    again = service.add_document("verein-demo", "a.txt", b"Gleicher Inhalt", "http://fake")

    assert again == document
    assert service.list_documents("verein-demo") == [document]
    assert service.original(document.content_hash) == b"Gleicher Inhalt"
    with pytest.raises(KnowledgeError, match="bereits in der Wissensbasis"):
        service.add_document("verein-demo", "a.txt", b"Gleicher Inhalt", "http://fake")
