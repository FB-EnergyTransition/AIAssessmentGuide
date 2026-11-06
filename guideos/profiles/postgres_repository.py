from __future__ import annotations

import json

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from guideos.profiles.models import Profile
from guideos.profiles.repository import (
    ProfileConflictError,
    ProfileRepositoryError,
    not_found,
)

UNAVAILABLE = "Die Profil-Datenbank ist nicht erreichbar."


class PostgresProfileRepository:
    """PostgreSQL implementation storing each profile as a JSONB snapshot;
    the table enforces one profile per participant and domain."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def list(self, participant_id: str | None = None) -> list[Profile]:
        query = "SELECT profile_data FROM profiles"
        params: tuple[str, ...] = ()
        if participant_id is not None:
            query += " WHERE participant_id = %s"
            params = (participant_id,)
        query += " ORDER BY created_at"
        try:
            with self._connect() as connection:
                rows = connection.execute(query, params).fetchall()
        except psycopg.Error as exc:
            raise ProfileRepositoryError(UNAVAILABLE) from exc
        return [Profile.model_validate(row["profile_data"]) for row in rows]

    def get(self, domain_id: str, participant_id: str | None = None) -> Profile:
        if participant_id is None:
            raise ProfileRepositoryError("PostgreSQL-Profile benötigen einen Teilnehmer-Schlüssel.")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT profile_data FROM profiles WHERE domain_id = %s AND participant_id = %s",
                    (domain_id, participant_id),
                ).fetchone()
        except psycopg.Error as exc:
            raise ProfileRepositoryError(UNAVAILABLE) from exc
        if row is None:
            raise not_found(domain_id)
        return Profile.model_validate(row["profile_data"])

    def save(self, profile: Profile) -> None:
        if profile.participant_id is None:
            raise ProfileRepositoryError("PostgreSQL-Profile benötigen einen Teilnehmer-Schlüssel.")
        snapshot = profile.model_dump(mode="json")
        try:
            with self._connect() as connection:
                if profile.revision == 0:
                    snapshot["revision"] = 1
                    try:
                        row = connection.execute(
                            """
                            INSERT INTO profiles (
                                profile_id, participant_id, domain_id, profile_data,
                                revision, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s::jsonb, 1, %s, %s)
                            RETURNING revision
                            """,
                            (
                                profile.profile_id,
                                profile.participant_id,
                                profile.domain_id,
                                json.dumps(snapshot),
                                profile.created_at,
                                profile.updated_at,
                            ),
                        ).fetchone()
                    except UniqueViolation as exc:
                        raise ProfileConflictError(
                            "Für diese Domäne gibt es bereits ein Profil des Teilnehmers."
                        ) from exc
                else:
                    next_revision = profile.revision + 1
                    snapshot["revision"] = next_revision
                    row = connection.execute(
                        """
                        UPDATE profiles
                        SET profile_data = %s::jsonb, revision = %s, updated_at = %s
                        WHERE profile_id = %s AND participant_id = %s AND revision = %s
                        RETURNING revision
                        """,
                        (
                            json.dumps(snapshot),
                            next_revision,
                            profile.updated_at,
                            profile.profile_id,
                            profile.participant_id,
                            profile.revision,
                        ),
                    ).fetchone()
        except ProfileConflictError:
            raise
        except psycopg.Error as exc:
            raise ProfileRepositoryError(UNAVAILABLE) from exc
        if row is None:
            raise ProfileConflictError("Das Profil wurde zwischenzeitlich geändert.")
        profile.revision = row["revision"]

    def delete(self, domain_id: str, participant_id: str | None = None) -> None:
        if participant_id is None:
            raise ProfileRepositoryError("PostgreSQL-Profile benötigen einen Teilnehmer-Schlüssel.")
        try:
            with self._connect() as connection:
                deleted = connection.execute(
                    "DELETE FROM profiles WHERE domain_id = %s AND participant_id = %s",
                    (domain_id, participant_id),
                ).rowcount
        except psycopg.Error as exc:
            raise ProfileRepositoryError(UNAVAILABLE) from exc
        if deleted == 0:
            raise not_found(domain_id)

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)
