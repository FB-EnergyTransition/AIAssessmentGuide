from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from guideos.domains.models import DomainPack
from guideos.knowledge.ingestion import (
    ExtractedText,
    FetchedPage,
    chunk_segments,
    extract_document,
    extract_web_page,
    fetch_web_page,
    require_http,
)
from guideos.knowledge.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeError,
    KnowledgeKind,
    RetrievedPassage,
)
from guideos.knowledge.search import cosine_scores, lexical_scores, reciprocal_rank_fusion
from guideos.knowledge.store import KnowledgeStore
from guideos.providers.base import LLMProvider, ProviderError


logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 32
DEFAULT_PASSAGE_LIMIT = 5
# Candidates handed to the reranker: a few more than the final list.
RERANK_CANDIDATES = 12


class KnowledgeService:
    """Maintains a domain's knowledge base and retrieves citable passages.

    Retrieval is a deterministic step before any LLM call. Without an
    embedding model the search is lexical; with one, documents are embedded
    on ingestion and ranked by cosine similarity."""

    def __init__(
        self,
        store: KnowledgeStore,
        provider: LLMProvider | None = None,
        embedding_model: str | None = None,
        fetch: Callable[[str], FetchedPage] = fetch_web_page,
        rerank_model: str | None = None,
    ):
        self.store = store
        self.provider = provider
        self.embedding_model = embedding_model if provider is not None else None
        self.rerank_model = rerank_model if provider is not None else None
        self.fetch = fetch

    def list_documents(self, domain_id: str) -> list[KnowledgeDocument]:
        return self.store.list(domain_id)

    def add_document(
        self,
        domain_id: str,
        filename: str,
        raw: bytes,
        endpoint: str,
        title: str | None = None,
    ) -> KnowledgeDocument:
        """Index a document and keep its original file. Uploading a known
        document again only supplies an original that is still missing."""
        content_hash = hashlib.sha256(raw).hexdigest()
        existing = next(
            (item for item in self.store.list(domain_id) if item.content_hash == content_hash),
            None,
        )
        if existing is not None:
            if self.store.load_original(content_hash) is not None:
                raise KnowledgeError("Dieses Dokument ist bereits in der Wissensbasis.")
            self.store.save_original(content_hash, raw)
            logger.info("knowledge_original_added document_id=%s", existing.document_id)
            return existing
        extracted = extract_document(filename, raw)
        document = self._store_document(
            domain_id, "document", extracted, content_hash, endpoint,
            title=title, filename=filename,
        )
        self.store.save_original(content_hash, raw)
        return document

    def original(self, content_hash: str) -> bytes | None:
        """The uploaded file a document was extracted from, if it was kept."""
        return self.store.load_original(content_hash)

    def add_web_page(
        self,
        domain_id: str,
        url: str,
        endpoint: str,
        allowed_domains: Sequence[str] = (),
    ) -> KnowledgeDocument:
        """Fetch a page once and index it like a document. Fetching the same
        address again creates a new source version when the content changed:
        the earlier copy is marked superseded, no longer searched, but kept so
        that citations stay traceable. Unchanged content keeps the stored
        version."""
        require_http(url)
        _require_allowed(url, allowed_domains)
        page = self.fetch(url)
        _require_allowed(page.url, allowed_domains)
        content_hash = hashlib.sha256(page.raw).hexdigest()
        current = [
            item
            for item in self.store.list(domain_id)
            if item.kind == "web" and item.url == page.url and item.superseded_by is None
        ]
        unchanged = next((item for item in current if item.content_hash == content_hash), None)
        if unchanged is not None:
            logger.info(
                "knowledge_web_page_unchanged domain_id=%s document_id=%s",
                domain_id,
                unchanged.document_id,
            )
            return unchanged
        extracted = extract_web_page(page)
        document = self._store_document(
            domain_id, "web", extracted, content_hash, endpoint, url=page.url
        )
        for existing in current:
            self.store.save(existing.model_copy(update={"superseded_by": document.document_id}))
            logger.info(
                "knowledge_web_page_superseded domain_id=%s document_id=%s replaced_by=%s",
                domain_id,
                existing.document_id,
                document.document_id,
            )
        return document

    def refetch_web_page(
        self,
        domain_id: str,
        document_id: str,
        endpoint: str,
        allowed_domains: Sequence[str] = (),
    ) -> KnowledgeDocument:
        """Fetch the address of a stored web page again (see add_web_page)."""
        document = next(
            (item for item in self.store.list(domain_id) if item.document_id == document_id), None
        )
        if document is None or document.kind != "web" or not document.url:
            raise KnowledgeError("Nur hinterlegte Webseiten lassen sich neu abrufen.")
        return self.add_web_page(domain_id, document.url, endpoint, allowed_domains)

    def superseded_documents(self, domain_id: str) -> list[KnowledgeDocument]:
        return [item for item in self.store.list(domain_id) if item.superseded_by is not None]

    def delete_superseded(self, domain_id: str) -> int:
        """Remove the replaced versions of a domain's web pages; returns how
        many were removed. Citations keep hash and excerpt either way."""
        superseded = self.superseded_documents(domain_id)
        for document in superseded:
            self.store.delete(document.document_id)
        logger.info(
            "knowledge_superseded_deleted domain_id=%s documents=%d", domain_id, len(superseded)
        )
        return len(superseded)

    def delete_document(self, document_id: str) -> None:
        self.store.delete(document_id)
        logger.info("knowledge_document_deleted document_id=%s", document_id)

    def delete_domain(self, domain_id: str) -> int:
        """Remove every document of a domain; returns how many were removed."""
        documents = self.store.list(domain_id)
        for document in documents:
            self.store.delete(document.document_id)
        logger.info(
            "knowledge_domain_deleted domain_id=%s documents=%d", domain_id, len(documents)
        )
        return len(documents)

    def pending_reindex(self, domain_id: str) -> int:
        """Documents not embedded with the configured embedding model."""
        if self.embedding_model is None:
            return 0
        return sum(
            1
            for item in self.store.list(domain_id)
            if item.embedding_model != self.embedding_model
        )

    def reindex(self, domain_id: str, endpoint: str) -> int:
        updated = 0
        for document in self.store.list(domain_id):
            if self.embedding_model is None or document.embedding_model == self.embedding_model:
                continue
            chunks = self._embedded(document.chunks, endpoint)
            self.store.save(
                document.model_copy(
                    update={"chunks": chunks, "embedding_model": self.embedding_model}
                )
            )
            updated += 1
        return updated

    def search(
        self,
        pack: DomainPack,
        query: str,
        endpoint: str,
        limit: int = DEFAULT_PASSAGE_LIMIT,
    ) -> list[RetrievedPassage]:
        """The best-matching passages for a query (see search_with_mode)."""
        return self.search_with_mode(pack, query, endpoint, limit)[0]

    def search_with_mode(
        self,
        pack: DomainPack,
        query: str,
        endpoint: str,
        limit: int = DEFAULT_PASSAGE_LIMIT,
    ) -> tuple[list[RetrievedPassage], str]:
        """The best-matching passages and how they were found: "lexical"
        (BM25), "hybrid" (BM25 and embeddings fused by reciprocal rank once
        every document of the domain is embedded with the configured model),
        each with "+rerank" when a reranker reordered the candidates. A
        failing reranker is logged and the fused order stands. Web pages are
        only used if the pack version enables them and their host is allowed."""
        web = pack.web_sources
        documents = [
            item
            for item in self.store.list(pack.domain_id)
            if item.superseded_by is None
            and (
                item.kind == "document"
                or (web is not None and web.enabled and _host_allowed(item.url, web.allowed_domains))
            )
        ]
        candidates = [(document, chunk) for document in documents for chunk in document.chunks]
        if not candidates or not query.strip():
            return [], "lexical"
        lexical = lexical_scores(query, [chunk.text for _, chunk in candidates])
        lexical_ranking = [
            index for index, score in sorted(enumerate(lexical), key=lambda item: item[1], reverse=True)
            if score > 0
        ]
        if self.embedding_model is not None and all(
            item.embedding_model == self.embedding_model for item in documents
        ):
            mode = "hybrid"
            vector = self.provider.embed(endpoint, self.embedding_model, [query])[0]
            semantic = cosine_scores(vector, [chunk.embedding or [] for _, chunk in candidates])
            semantic_ranking = [
                index for index, score in sorted(enumerate(semantic), key=lambda item: item[1], reverse=True)
                if score > 0
            ][: max(limit, RERANK_CANDIDATES)]
            ranked = reciprocal_rank_fusion([lexical_ranking[: max(limit, RERANK_CANDIDATES)], semantic_ranking])
        else:
            mode = "lexical"
            ranked = [(index, lexical[index]) for index in lexical_ranking]
        ranked = ranked[: RERANK_CANDIDATES if self.rerank_model else limit]
        if self.rerank_model is not None and ranked:
            try:
                scores = self.provider.rerank(
                    endpoint,
                    self.rerank_model,
                    query,
                    [candidates[index][1].text for index, _ in ranked],
                )
                ranked = sorted(
                    zip((index for index, _ in ranked), scores, strict=True),
                    key=lambda item: item[1],
                    reverse=True,
                )
                mode += "+rerank"
            except ProviderError as exc:
                logger.warning(
                    "knowledge_rerank_failed domain_id=%s model=%s error=%s",
                    pack.domain_id,
                    self.rerank_model,
                    type(exc).__name__,
                )
        ranked = ranked[:limit]
        counters = {"document": 0, "web": 0}
        passages = []
        for index, score in ranked:
            document, chunk = candidates[index]
            counters[document.kind] += 1
            prefix = "D" if document.kind == "document" else "W"
            passages.append(
                RetrievedPassage(
                    source_id=f"{prefix}{counters[document.kind]}",
                    kind=document.kind,
                    document_id=document.document_id,
                    title=document.title,
                    locator=chunk.locator,
                    url=document.url,
                    content_hash=document.content_hash,
                    retrieved_at=document.created_at,
                    text=chunk.text,
                    score=float(score),
                )
            )
        logger.info(
            "knowledge_searched domain_id=%s mode=%s passages=%d",
            pack.domain_id,
            mode,
            len(passages),
        )
        return passages, mode

    def _store_document(
        self,
        domain_id: str,
        kind: KnowledgeKind,
        extracted: ExtractedText,
        content_hash: str,
        endpoint: str,
        *,
        title: str | None = None,
        filename: str | None = None,
        url: str | None = None,
    ) -> KnowledgeDocument:
        chunks = self._embedded(chunk_segments(extracted.segments), endpoint)
        document = KnowledgeDocument(
            document_id=f"doc_{uuid4().hex}",
            domain_id=domain_id,
            kind=kind,
            title=(title or "").strip() or extracted.title,
            filename=filename,
            url=url,
            content_hash=content_hash,
            embedding_model=self.embedding_model,
            created_at=datetime.now(UTC),
            chunks=chunks,
        )
        self.store.save(document)
        logger.info(
            "knowledge_document_added domain_id=%s document_id=%s kind=%s chunks=%d "
            "embedding_model=%s",
            domain_id,
            document.document_id,
            kind,
            len(chunks),
            self.embedding_model,
        )
        return document

    def _embedded(self, chunks: list[KnowledgeChunk], endpoint: str) -> list[KnowledgeChunk]:
        if self.embedding_model is None:
            return chunks
        embedded = []
        for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
            batch = chunks[start:start + EMBEDDING_BATCH_SIZE]
            vectors = self.provider.embed(
                endpoint, self.embedding_model, [chunk.text for chunk in batch]
            )
            embedded.extend(
                chunk.model_copy(update={"embedding": vector})
                for chunk, vector in zip(batch, vectors, strict=True)
            )
        return embedded


def _host_allowed(url: str | None, allowed_domains: Sequence[str]) -> bool:
    if not allowed_domains:
        return True
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _require_allowed(url: str, allowed_domains: Sequence[str]) -> None:
    if not _host_allowed(url, allowed_domains):
        host = urlparse(url).hostname or url
        raise KnowledgeError(
            f"{host} ist für diese Domain nicht erlaubt (erlaubt: {', '.join(allowed_domains)})."
        )
