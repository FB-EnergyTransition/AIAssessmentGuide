from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from guideos.assessment.models import AssessmentSession
from guideos.assessment.repository import (
    AssessmentConflictError,
    AssessmentRepositoryError,
)


class PostgresAssessmentRepository:
    """PostgreSQL implementation storing the session state as a JSONB snapshot."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def list(self, participant_id: str | None = None) -> list[AssessmentSession]:
        query = "SELECT session_data FROM assessment_sessions"
        params: tuple[str, ...] = ()
        if participant_id is not None:
            query += " WHERE participant_id = %s"
            params = (participant_id,)
        query += " ORDER BY created_at"
        try:
            with self._connect() as connection:
                rows = connection.execute(query, params).fetchall()
        except psycopg.Error as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datenbank ist nicht erreichbar."
            ) from exc
        return [AssessmentSession.model_validate(row["session_data"]) for row in rows]

    def get(
        self,
        session_id: str,
        participant_id: str | None = None,
    ) -> AssessmentSession:
        query = "SELECT session_data FROM assessment_sessions WHERE session_id = %s"
        params: tuple[str, ...] = (session_id,)
        if participant_id is not None:
            query += " AND participant_id = %s"
            params = (session_id, participant_id)
        try:
            with self._connect() as connection:
                row = connection.execute(query, params).fetchone()
        except psycopg.Error as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datenbank ist nicht erreichbar."
            ) from exc
        if row is None:
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")
        return AssessmentSession.model_validate(row["session_data"])

    def save(self, session: AssessmentSession) -> None:
        if session.participant_id is None:
            raise AssessmentRepositoryError(
                "PostgreSQL-Assessment-Sessions benötigen einen Teilnehmer-Schlüssel."
            )
        snapshot = session.model_dump(mode="json")
        try:
            with self._connect() as connection:
                if session.revision == 0:
                    snapshot["revision"] = 1
                    row = connection.execute(
                        """
                        INSERT INTO assessment_sessions (
                            session_id, participant_id, domain_id, domain_version,
                            domain_content_hash, session_data, revision, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 1, %s, %s)
                        ON CONFLICT (session_id) DO NOTHING
                        RETURNING revision
                        """,
                        (
                            session.session_id,
                            session.participant_id,
                            session.domain_id,
                            session.domain_version,
                            session.domain_content_hash,
                            json.dumps(snapshot),
                            session.created_at,
                            session.updated_at,
                        ),
                    ).fetchone()
                else:
                    next_revision = session.revision + 1
                    snapshot["revision"] = next_revision
                    row = connection.execute(
                        """
                        UPDATE assessment_sessions
                        SET session_data = %s::jsonb, revision = %s, updated_at = %s
                        WHERE session_id = %s AND participant_id = %s AND revision = %s
                        RETURNING revision
                        """,
                        (
                            json.dumps(snapshot),
                            next_revision,
                            session.updated_at,
                            session.session_id,
                            session.participant_id,
                            session.revision,
                        ),
                    ).fetchone()
        except psycopg.Error as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datenbank ist nicht erreichbar."
            ) from exc
        if row is None:
            raise AssessmentConflictError(
                "Die Assessment-Session wurde zwischenzeitlich geändert."
            )
        session.revision = row["revision"]

    def delete(self, session_id: str, participant_id: str | None = None) -> None:
        query = "DELETE FROM assessment_sessions WHERE session_id = %s"
        params: tuple[str, ...] = (session_id,)
        if participant_id is not None:
            query += " AND participant_id = %s"
            params = (session_id, participant_id)
        try:
            with self._connect() as connection:
                deleted = connection.execute(query, params).rowcount
        except psycopg.Error as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datenbank ist nicht erreichbar."
            ) from exc
        if deleted == 0:
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)
