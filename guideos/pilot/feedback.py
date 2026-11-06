from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


class PilotFeedbackError(Exception):
    pass


@dataclass(frozen=True)
class PilotFeedback:
    feedback_id: str
    session_id: str
    rating: int
    comment: str | None
    created_at: datetime


class PostgresPilotFeedbackRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def get(self, session_id: str, participant_id: str) -> PilotFeedback | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT feedback_id, session_id, rating, comment, created_at
                    FROM pilot_feedback
                    WHERE session_id = %s AND participant_id = %s
                    """,
                    (session_id, participant_id),
                ).fetchone()
        except psycopg.Error as exc:
            raise PilotFeedbackError("Pilot-Feedback konnte nicht geladen werden.") from exc
        return _feedback_from_row(row) if row else None

    def save(
        self,
        session_id: str,
        participant_id: str,
        rating: int,
        comment: str | None,
    ) -> PilotFeedback:
        if not 1 <= rating <= 5:
            raise ValueError("Die Bewertung muss zwischen 1 und 5 liegen.")
        normalized_comment = comment.strip() if comment else None
        if normalized_comment and len(normalized_comment) > 2000:
            raise ValueError("Das Feedback darf maximal 2000 Zeichen enthalten.")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO pilot_feedback (
                        feedback_id, session_id, participant_id, rating, comment
                    )
                    SELECT %s, session_id, participant_id, %s, %s
                    FROM assessment_sessions
                    WHERE session_id = %s AND participant_id = %s
                    ON CONFLICT (session_id) DO NOTHING
                    RETURNING feedback_id, session_id, rating, comment, created_at
                    """,
                    (
                        str(uuid4()),
                        rating,
                        normalized_comment,
                        session_id,
                        participant_id,
                    ),
                ).fetchone()
        except psycopg.Error as exc:
            raise PilotFeedbackError("Pilot-Feedback konnte nicht gespeichert werden.") from exc
        if row is None:
            existing = self.get(session_id, participant_id)
            if existing:
                return existing
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")
        return _feedback_from_row(row)

    def export_rows(self) -> list[dict[str, object]]:
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT f.session_id, s.domain_id, s.domain_version,
                           f.rating, f.comment, f.created_at
                    FROM pilot_feedback f
                    JOIN assessment_sessions s ON s.session_id = f.session_id
                    ORDER BY f.created_at
                    """
                ).fetchall()
        except psycopg.Error as exc:
            raise PilotFeedbackError("Pilot-Feedback konnte nicht exportiert werden.") from exc

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _feedback_from_row(row: dict[str, object]) -> PilotFeedback:
    return PilotFeedback(
        feedback_id=str(row["feedback_id"]),
        session_id=str(row["session_id"]),
        rating=int(row["rating"]),
        comment=str(row["comment"]) if row["comment"] is not None else None,
        created_at=row["created_at"],  # type: ignore[arg-type]
    )
