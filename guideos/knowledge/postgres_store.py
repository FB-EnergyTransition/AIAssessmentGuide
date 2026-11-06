from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from guideos.knowledge.models import KnowledgeDocument
from guideos.knowledge.store import KnowledgeStoreError, not_found


class PostgresKnowledgeStore:
    """Knowledge base in PostgreSQL, one JSONB snapshot per document, shared
    by all processes and participants."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def list(self, domain_id: str) -> list[KnowledgeDocument]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT document_data FROM knowledge_documents "
                    "WHERE domain_id = %s ORDER BY created_at, document_id",
                    (domain_id,),
                ).fetchall()
        except psycopg.Error as exc:
            raise _unreachable() from exc
        return [KnowledgeDocument.model_validate(row["document_data"]) for row in rows]

    def save(self, document: KnowledgeDocument) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        document_id, domain_id, document_data, created_at
                    ) VALUES (%s, %s, %s::jsonb, %s)
                    ON CONFLICT (document_id)
                    DO UPDATE SET document_data = EXCLUDED.document_data
                    """,
                    (
                        document.document_id,
                        document.domain_id,
                        json.dumps(document.model_dump(mode="json")),
                        document.created_at,
                    ),
                )
        except psycopg.Error as exc:
            raise _unreachable() from exc

    def delete(self, document_id: str) -> None:
        try:
            with self._connect() as connection:
                deleted = connection.execute(
                    "DELETE FROM knowledge_documents WHERE document_id = %s "
                    "RETURNING document_data->>'content_hash' AS content_hash",
                    (document_id,),
                ).fetchone()
                if deleted is not None:
                    connection.execute(
                        "DELETE FROM knowledge_files WHERE content_hash = %s AND NOT EXISTS ("
                        "SELECT 1 FROM knowledge_documents "
                        "WHERE document_data->>'content_hash' = %s)",
                        (deleted["content_hash"], deleted["content_hash"]),
                    )
        except psycopg.Error as exc:
            raise _unreachable() from exc
        if deleted is None:
            raise not_found(document_id)

    def save_original(self, content_hash: str, raw: bytes) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO knowledge_files (content_hash, content) VALUES (%s, %s) "
                    "ON CONFLICT (content_hash) DO NOTHING",
                    (content_hash, raw),
                )
        except psycopg.Error as exc:
            raise _unreachable() from exc

    def load_original(self, content_hash: str) -> bytes | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT content FROM knowledge_files WHERE content_hash = %s",
                    (content_hash,),
                ).fetchone()
        except psycopg.Error as exc:
            raise _unreachable() from exc
        return bytes(row["content"]) if row else None

    def delete_original(self, content_hash: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM knowledge_files WHERE content_hash = %s", (content_hash,)
                )
        except psycopg.Error as exc:
            raise _unreachable() from exc

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _unreachable() -> KnowledgeStoreError:
    return KnowledgeStoreError("Die Datenbank der Wissensbasis ist nicht erreichbar.")
