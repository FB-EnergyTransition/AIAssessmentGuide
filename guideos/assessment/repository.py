from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from guideos.assessment.models import AssessmentSession


ASSESSMENT_STORAGE_VERSION = 1
DEFAULT_ASSESSMENT_STORAGE_PATH = Path(".guideos") / "assessments.json"


class AssessmentRepositoryError(Exception):
    pass


class AssessmentConflictError(AssessmentRepositoryError):
    pass


@runtime_checkable
class AssessmentRepository(Protocol):
    def list(self, participant_id: str | None = None) -> list[AssessmentSession]:
        ...

    def get(
        self,
        session_id: str,
        participant_id: str | None = None,
    ) -> AssessmentSession:
        ...

    def save(self, session: AssessmentSession) -> None:
        ...

    def delete(self, session_id: str, participant_id: str | None = None) -> None:
        ...


class LocalAssessmentRepository:
    def __init__(self, path: Path = DEFAULT_ASSESSMENT_STORAGE_PATH):
        self.path = path

    def list(self, participant_id: str | None = None) -> list[AssessmentSession]:
        sessions = self._load()
        if participant_id is None:
            return sessions
        return [session for session in sessions if session.participant_id == participant_id]

    def get(
        self,
        session_id: str,
        participant_id: str | None = None,
    ) -> AssessmentSession:
        for session in self.list(participant_id):
            if session.session_id == session_id:
                return session
        raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")

    def save(self, session: AssessmentSession) -> None:
        sessions = self._load()
        for index, existing in enumerate(sessions):
            if existing.session_id == session.session_id:
                if session.revision != existing.revision:
                    raise AssessmentConflictError(
                        "Die Assessment-Session wurde zwischenzeitlich geändert."
                    )
                session.revision = existing.revision + 1
                sessions[index] = session
                break
        else:
            if session.revision != 0:
                raise AssessmentConflictError(
                    "Die Assessment-Session wurde zwischenzeitlich erstellt."
                )
            session.revision = 1
            sessions.append(session)
        self._write(sessions)

    def delete(self, session_id: str, participant_id: str | None = None) -> None:
        sessions = self._load()
        remaining = [
            session
            for session in sessions
            if not (
                session.session_id == session_id
                and (participant_id is None or session.participant_id == participant_id)
            )
        ]
        if len(remaining) == len(sessions):
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")
        self._write(remaining)

    def _load(self) -> list[AssessmentSession]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datei enthält kein gültiges JSON."
            ) from exc
        except OSError as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datei konnte nicht gelesen werden."
            ) from exc

        return self._decode(data)

    def _decode(self, data: Any) -> list[AssessmentSession]:
        if not isinstance(data, dict):
            raise AssessmentRepositoryError(
                "Die Assessment-Datei hat ein ungültiges Format."
            )
        if data.get("version") != ASSESSMENT_STORAGE_VERSION:
            raise AssessmentRepositoryError(
                "Die Assessment-Datei hat eine nicht unterstützte Version."
            )
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            raise AssessmentRepositoryError(
                "Die Assessment-Datei enthält keine gültige Session-Liste."
            )
        try:
            decoded = [AssessmentSession.model_validate(item) for item in sessions]
        except ValidationError as exc:
            raise AssessmentRepositoryError(
                "Die Assessment-Datei enthält ungültige Session-Daten."
            ) from exc
        session_ids = [session.session_id for session in decoded]
        if len(session_ids) != len(set(session_ids)):
            raise AssessmentRepositoryError(
                "Die Assessment-Datei enthält doppelte Session-IDs."
            )
        return decoded

    def _write(self, sessions: list[AssessmentSession]) -> None:
        payload = {
            "version": ASSESSMENT_STORAGE_VERSION,
            "sessions": [
                session.model_dump(mode="json") for session in sessions
            ],
        }
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise AssessmentRepositoryError(
                "Die Assessment-Datei konnte nicht gespeichert werden."
            ) from exc


class ParticipantAssessmentRepository:
    """Restricts an assessment repository to one anonymous pilot participant."""

    def __init__(self, repository: AssessmentRepository, participant_id: str):
        self.repository = repository
        self.participant_id = participant_id

    def list(self, participant_id: str | None = None) -> list[AssessmentSession]:
        if participant_id not in {None, self.participant_id}:
            return []
        return self.repository.list(self.participant_id)

    def get(
        self,
        session_id: str,
        participant_id: str | None = None,
    ) -> AssessmentSession:
        if participant_id not in {None, self.participant_id}:
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")
        return self.repository.get(session_id, self.participant_id)

    def save(self, session: AssessmentSession) -> None:
        if session.participant_id is None:
            session.participant_id = self.participant_id
        if session.participant_id != self.participant_id:
            raise LookupError(f"Assessment-Session {session.session_id} wurde nicht gefunden.")
        self.repository.save(session)

    def delete(self, session_id: str, participant_id: str | None = None) -> None:
        if participant_id not in {None, self.participant_id}:
            raise LookupError(f"Assessment-Session {session_id} wurde nicht gefunden.")
        self.repository.delete(session_id, self.participant_id)
