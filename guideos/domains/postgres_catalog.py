from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from guideos.domains.catalog import DomainCatalogError, DraftPack, serialize_pack_data


class PostgresCatalogStore:
    """Admin drafts and active versions in PostgreSQL, shared by all processes."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def read_active_versions(self) -> dict[str, str] | None:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT domain_id, version FROM domain_active_versions"
                ).fetchall()
        except psycopg.Error as exc:
            raise DomainCatalogError("Die Domain-Datenbank ist nicht erreichbar.") from exc
        if not rows:
            return None
        return {row["domain_id"]: row["version"] for row in rows}

    def write_active_versions(self, versions: dict[str, str]) -> None:
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM domain_active_versions")
                connection.cursor().executemany(
                    """
                    INSERT INTO domain_active_versions (domain_id, version, updated_at)
                    VALUES (%s, %s, now())
                    """,
                    list(versions.items()),
                )
        except psycopg.Error as exc:
            raise DomainCatalogError("Die Domain-Datenbank ist nicht erreichbar.") from exc

    def list_drafts(self) -> list[DraftPack]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT domain_id, version, pack_text FROM domain_pack_drafts "
                    "ORDER BY domain_id, version"
                ).fetchall()
        except psycopg.Error as exc:
            raise DomainCatalogError("Die Domain-Datenbank ist nicht erreichbar.") from exc
        return [
            DraftPack(
                domain_id=row["domain_id"],
                version=row["version"],
                raw=row["pack_text"].encode("utf-8"),
                location=_location(row["domain_id"], row["version"]),
            )
            for row in rows
        ]

    def save_draft(self, domain_id: str, version: str, data: dict[str, Any]) -> str:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO domain_pack_drafts (domain_id, version, pack_text)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (domain_id, version)
                    DO UPDATE SET pack_text = EXCLUDED.pack_text, updated_at = now()
                    """,
                    (domain_id, version, serialize_pack_data(data)),
                )
        except psycopg.Error as exc:
            raise DomainCatalogError("Die Domain-Datenbank ist nicht erreichbar.") from exc
        return _location(domain_id, version)

    def delete_draft(self, domain_id: str, version: str) -> None:
        try:
            with self._connect() as connection:
                deleted = connection.execute(
                    "DELETE FROM domain_pack_drafts WHERE domain_id = %s AND version = %s",
                    (domain_id, version),
                ).rowcount
        except psycopg.Error as exc:
            raise DomainCatalogError("Die Domain-Datenbank ist nicht erreichbar.") from exc
        if deleted == 0:
            raise DomainCatalogError(f"Draft {domain_id}/{version} wurde nicht gefunden.")

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _location(domain_id: str, version: str) -> str:
    return f"postgres:domain_pack_drafts/{domain_id}/{version}"
