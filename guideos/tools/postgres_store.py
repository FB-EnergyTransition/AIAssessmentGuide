from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from guideos.tools.models import ToolServer
from guideos.tools.store import ToolStoreError, duplicate_name


class PostgresToolServerStore:
    """Registrations in table mcp_servers (one JSONB snapshot per server,
    UNIQUE domain_id + name). The token is stored in clear text like the pilot
    credentials in .env; the Domain-Admin is not production auth."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def list(self, domain_id: str) -> list[ToolServer]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT server_data FROM mcp_servers WHERE domain_id = %s ORDER BY name",
                    (domain_id,),
                ).fetchall()
        except psycopg.Error as exc:
            raise _unreachable() from exc
        return [ToolServer.model_validate(row["server_data"]) for row in rows]

    def get(self, domain_id: str, name: str) -> ToolServer | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT server_data FROM mcp_servers WHERE domain_id = %s AND name = %s",
                    (domain_id, name),
                ).fetchone()
        except psycopg.Error as exc:
            raise _unreachable() from exc
        return ToolServer.model_validate(row["server_data"]) if row else None

    def save(self, server: ToolServer) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO mcp_servers (server_id, domain_id, name, server_data, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (server_id) DO UPDATE SET
                        name = EXCLUDED.name, server_data = EXCLUDED.server_data,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (server.server_id, server.domain_id, server.name,
                     json.dumps(server.model_dump(mode="json")), server.updated_at),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise duplicate_name(server) from exc
        except psycopg.Error as exc:
            raise _unreachable() from exc

    def delete(self, domain_id: str, name: str) -> bool:
        try:
            with self._connect() as connection:
                result = connection.execute(
                    "DELETE FROM mcp_servers WHERE domain_id = %s AND name = %s", (domain_id, name)
                )
                return (result.rowcount or 0) > 0
        except psycopg.Error as exc:
            raise _unreachable() from exc

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _unreachable() -> ToolStoreError:
    return ToolStoreError("Die Datenbank der Werkzeugserver ist nicht erreichbar.")
