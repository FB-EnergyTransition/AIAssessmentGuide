from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from guideos.conversations.models import Conversation
from guideos.conversations.repository import (
    ConversationConflictError,
    ConversationRepositoryError,
)


class PostgresConversationRepository:
    """PostgreSQL implementation storing each conversation as a JSONB snapshot."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def list(self, participant_id: str | None = None) -> list[Conversation]:
        query = "SELECT conversation_data FROM conversations"
        params: tuple[str, ...] = ()
        if participant_id is not None:
            query += " WHERE participant_id = %s"
            params = (participant_id,)
        query += " ORDER BY created_at"
        try:
            with self._connect() as connection:
                rows = connection.execute(query, params).fetchall()
        except psycopg.Error as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatenbank ist nicht erreichbar."
            ) from exc
        return [Conversation.model_validate(row["conversation_data"]) for row in rows]

    def get(
        self,
        conversation_id: str,
        participant_id: str | None = None,
    ) -> Conversation:
        query = "SELECT conversation_data FROM conversations WHERE conversation_id = %s"
        params: tuple[str, ...] = (conversation_id,)
        if participant_id is not None:
            query += " AND participant_id = %s"
            params = (conversation_id, participant_id)
        try:
            with self._connect() as connection:
                row = connection.execute(query, params).fetchone()
        except psycopg.Error as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatenbank ist nicht erreichbar."
            ) from exc
        if row is None:
            raise LookupError(f"Konversation {conversation_id} wurde nicht gefunden.")
        return Conversation.model_validate(row["conversation_data"])

    def save(self, conversation: Conversation) -> None:
        if conversation.participant_id is None:
            raise ConversationRepositoryError(
                "PostgreSQL-Konversationen benötigen einen Teilnehmer-Schlüssel."
            )
        snapshot = conversation.model_dump(mode="json")
        try:
            with self._connect() as connection:
                if conversation.revision == 0:
                    snapshot["revision"] = 1
                    row = connection.execute(
                        """
                        INSERT INTO conversations (
                            conversation_id, participant_id, domain_id,
                            profile_id, title, conversation_data,
                            revision, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 1, %s, %s)
                        ON CONFLICT (conversation_id) DO NOTHING
                        RETURNING revision
                        """,
                        (
                            conversation.conversation_id,
                            conversation.participant_id,
                            conversation.domain_id,
                            conversation.profile_id,
                            conversation.title,
                            json.dumps(snapshot),
                            conversation.created_at,
                            conversation.updated_at,
                        ),
                    ).fetchone()
                else:
                    next_revision = conversation.revision + 1
                    snapshot["revision"] = next_revision
                    row = connection.execute(
                        """
                        UPDATE conversations
                        SET conversation_data = %s::jsonb, title = %s,
                            revision = %s, updated_at = %s
                        WHERE conversation_id = %s AND participant_id = %s
                          AND revision = %s
                        RETURNING revision
                        """,
                        (
                            json.dumps(snapshot),
                            conversation.title,
                            next_revision,
                            conversation.updated_at,
                            conversation.conversation_id,
                            conversation.participant_id,
                            conversation.revision,
                        ),
                    ).fetchone()
        except psycopg.Error as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatenbank ist nicht erreichbar."
            ) from exc
        if row is None:
            raise ConversationConflictError(
                "Die Konversation wurde zwischenzeitlich geändert."
            )
        conversation.revision = row["revision"]

    def delete(self, conversation_id: str, participant_id: str | None = None) -> None:
        query = "DELETE FROM conversations WHERE conversation_id = %s"
        params: tuple[str, ...] = (conversation_id,)
        if participant_id is not None:
            query += " AND participant_id = %s"
            params = (conversation_id, participant_id)
        try:
            with self._connect() as connection:
                deleted = connection.execute(query, params).rowcount
        except psycopg.Error as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatenbank ist nicht erreichbar."
            ) from exc
        if deleted == 0:
            raise LookupError(f"Konversation {conversation_id} wurde nicht gefunden.")

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)
