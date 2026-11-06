from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from guideos.chat import ChatState, create_initial_chat_state
from guideos.storage import ChatStorageError, decode_chat_state, encode_chat_state


class PostgresChatStateStore:
    """Chat history per owner as a JSONB document in PostgreSQL."""

    def __init__(self, database_url: str, owner: str):
        self.database_url = database_url
        self.owner = owner

    def load(self) -> ChatState:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT chat_data FROM chat_states WHERE owner = %s",
                    (self.owner,),
                ).fetchone()
        except psycopg.Error as exc:
            raise ChatStorageError("Die Chat-Datenbank ist nicht erreichbar.") from exc
        if row is None:
            return create_initial_chat_state()
        return decode_chat_state(row["chat_data"])

    def save(self, chat_state: ChatState) -> None:
        payload = json.dumps(encode_chat_state(chat_state), ensure_ascii=False)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO chat_states (owner, chat_data, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT (owner)
                    DO UPDATE SET chat_data = EXCLUDED.chat_data, updated_at = now()
                    """,
                    (self.owner, payload),
                )
        except psycopg.Error as exc:
            raise ChatStorageError("Die Chat-Datenbank ist nicht erreichbar.") from exc

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)
