import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from guideos.assessment.migrations import migrate_database
from guideos.chat import create_initial_chat_state
from guideos.domains.catalog import (
    DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    DomainCatalogError,
    activate_domain_version,
    list_domain_versions,
    load_active_domain_pack,
    load_domain_pack_version,
    save_domain_pack_draft_data,
)
from guideos.domains.postgres_catalog import PostgresCatalogStore
from guideos.knowledge.models import KnowledgeChunk, KnowledgeDocument
from guideos.knowledge.postgres_store import PostgresKnowledgeStore
from guideos.storage_postgres import PostgresChatStateStore


POSTGRES_URL = os.environ.get("GUIDEOS_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="GUIDEOS_TEST_POSTGRES_URL is required for PostgreSQL integration tests.",
)


def test_postgres_chat_store_roundtrips_per_owner():
    migrate_database(POSTGRES_URL)
    owner = f"owner_{uuid4().hex}"
    store = PostgresChatStateStore(POSTGRES_URL, owner)
    other = PostgresChatStateStore(POSTGRES_URL, f"other_{uuid4().hex}")

    assert store.load()["chats"][0]["messages"] == []
    state = create_initial_chat_state()
    state["chats"][0]["title"] = "Postgres-Chat"
    state["chats"][0]["messages"].append({"role": "user", "content": "Hallo"})
    store.save(state)
    state["chats"][0]["messages"].append({"role": "assistant", "content": "Servus"})
    store.save(state)

    loaded = store.load()
    assert loaded["chats"][0]["title"] == "Postgres-Chat"
    assert [m["content"] for m in loaded["chats"][0]["messages"]] == ["Hallo", "Servus"]
    assert other.load()["chats"][0]["messages"] == []


def test_postgres_catalog_store_saves_drafts_and_activates_versions():
    migrate_database(POSTGRES_URL)
    store = PostgresCatalogStore(POSTGRES_URL)
    domain_id = f"pg-demo-{uuid4().hex[:8]}"
    pack = _minimal_pack(domain_id, "1.0.0")

    manifest_before = store.read_active_versions()
    location = save_domain_pack_draft_data(pack, store=store)
    assert location == f"postgres:domain_pack_drafts/{domain_id}/1.0.0"

    refs = list_domain_versions(bundled_root=DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT, store=store)
    ref = next(item for item in refs if item.domain_id == domain_id)
    assert ref.source == "local" and ref.valid is True and ref.path is None

    activated = activate_domain_version(
        domain_id, "1.0.0", bundled_root=DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT, store=store
    )
    loaded = load_active_domain_pack(
        domain_id, bundled_root=DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT, store=store
    )
    assert activated.content_hash == loaded.content_hash
    manifest_after = store.read_active_versions()
    assert manifest_after[domain_id] == "1.0.0"
    # An existing manifest is preserved; without one the latest bundled version is recorded.
    expected_demo = manifest_before.get("verein-demo") if manifest_before else "1.0.0"
    assert manifest_after.get("verein-demo") == expected_demo

    pack["name"] = "Geändert"
    save_domain_pack_draft_data(pack, store=store)
    assert load_domain_pack_version(
        domain_id, "1.0.0", bundled_root=DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT, store=store
    ).name == "Geändert"

    pack["version"] = "1.0.1"
    save_domain_pack_draft_data(pack, store=store)
    store.delete_draft(domain_id, "1.0.1")
    assert [(d.domain_id, d.version) for d in store.list_drafts() if d.domain_id == domain_id] == [
        (domain_id, "1.0.0")
    ]
    with pytest.raises(DomainCatalogError, match="wurde nicht gefunden"):
        store.delete_draft(domain_id, "1.0.1")


def _minimal_pack(domain_id: str, version: str):
    return {
        "domain_id": domain_id,
        "version": version,
        "name": "Postgres Demo",
        "description": "Demo",
        "default_locale": "de",
        "questions": [
            {
                "question_id": "kind",
                "field_key": "kind",
                "text": "Art?",
                "field_type": "select",
                "required": True,
                "options": [{"value": "one", "label": "Eins"}],
            }
        ],
        "rules": [],
        "recommendations": [],
    }


def test_postgres_knowledge_store_roundtrips_documents_per_domain():
    migrate_database(POSTGRES_URL)
    store = PostgresKnowledgeStore(POSTGRES_URL)
    domain_id = f"domain_{uuid4().hex}"
    document = KnowledgeDocument(
        document_id=f"doc_{uuid4().hex}",
        domain_id=domain_id,
        kind="web",
        title="Heizungstausch",
        url="https://www.klimaaktiv.at/heizung",
        content_hash="a" * 64,
        embedding_model="bge-m3",
        created_at=datetime(2026, 10, 1, 8, 30, tzinfo=UTC),
        chunks=[KnowledgeChunk(locator="Förderung", text="Wärmepumpe", embedding=[0.1, -0.25])],
    )

    store.save(document)
    store.save(document.model_copy(update={"title": "Neu"}))

    assert store.list(domain_id) == [document.model_copy(update={"title": "Neu"})]
    assert store.list(f"other_{uuid4().hex}") == []
    store.delete(document.document_id)
    assert store.list(domain_id) == []
    with pytest.raises(LookupError):
        store.delete(document.document_id)


def test_postgres_knowledge_store_keeps_original_files_until_unused():
    migrate_database(POSTGRES_URL)
    store = PostgresKnowledgeStore(POSTGRES_URL)
    content_hash = uuid4().hex * 2
    documents = [
        KnowledgeDocument(
            document_id=f"doc_{uuid4().hex}",
            domain_id=f"domain_{uuid4().hex}",
            kind="document",
            title="Leitfaden",
            filename="leitfaden.pdf",
            content_hash=content_hash,
            created_at=datetime(2026, 10, 5, 8, 30, tzinfo=UTC),
            chunks=[KnowledgeChunk(locator="Seite 1", text="Wärmepumpe")],
        )
        for _ in range(2)
    ]
    for document in documents:
        store.save(document)

    assert store.load_original(content_hash) is None
    store.save_original(content_hash, b"%PDF-\x00original")
    store.save_original(content_hash, b"%PDF-\x00original")

    assert store.load_original(content_hash) == b"%PDF-\x00original"
    store.delete(documents[0].document_id)
    assert store.load_original(content_hash) == b"%PDF-\x00original"
    store.delete(documents[1].document_id)
    assert store.load_original(content_hash) is None
