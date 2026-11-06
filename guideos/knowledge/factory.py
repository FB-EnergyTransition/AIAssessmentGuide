import os
from pathlib import Path

from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import (
    DEFAULT_KNOWLEDGE_STORAGE_PATH,
    KnowledgeStore,
    LocalKnowledgeStore,
)
from guideos.providers.base import LLMProvider


def knowledge_storage_path_from_env() -> Path:
    return Path(
        os.environ.get("GUIDEOS_KNOWLEDGE_PATH", str(DEFAULT_KNOWLEDGE_STORAGE_PATH))
    )


def knowledge_store_from_env(path: Path | None = None) -> KnowledgeStore:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.knowledge.postgres_store import PostgresKnowledgeStore

        return PostgresKnowledgeStore(database_url)
    return LocalKnowledgeStore(path or knowledge_storage_path_from_env())


def knowledge_service_from_env(
    provider: LLMProvider,
    path: Path | None = None,
) -> KnowledgeService:
    """Embeddings and reranking are opt-in: without GUIDEOS_EMBEDDING_MODEL the
    search is lexical, without GUIDEOS_RERANK_MODEL nothing reorders it."""
    return KnowledgeService(
        knowledge_store_from_env(path),
        provider=provider,
        embedding_model=os.environ.get("GUIDEOS_EMBEDDING_MODEL", "").strip() or None,
        rerank_model=os.environ.get("GUIDEOS_RERANK_MODEL", "").strip() or None,
    )
