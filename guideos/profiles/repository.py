from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from guideos.profiles.models import Profile

PROFILE_STORAGE_VERSION = 1
DEFAULT_PROFILE_STORAGE_PATH = Path(".guideos") / "profiles.json"


class ProfileRepositoryError(Exception):
    pass


class ProfileConflictError(ProfileRepositoryError):
    pass


def not_found(domain_id: str) -> LookupError:
    return LookupError(f"Für die Domäne {domain_id} gibt es noch kein Profil.")


@runtime_checkable
class ProfileRepository(Protocol):
    """At most one profile per participant and domain."""

    def list(self, participant_id: str | None = None) -> list[Profile]: ...

    def get(self, domain_id: str, participant_id: str | None = None) -> Profile: ...

    def save(self, profile: Profile) -> None: ...

    def delete(self, domain_id: str, participant_id: str | None = None) -> None: ...


class LocalProfileRepository:
    def __init__(self, path: Path = DEFAULT_PROFILE_STORAGE_PATH):
        self.path = path

    def list(self, participant_id: str | None = None) -> list[Profile]:
        profiles = self._load()
        if participant_id is None:
            return profiles
        return [item for item in profiles if item.participant_id == participant_id]

    def get(self, domain_id: str, participant_id: str | None = None) -> Profile:
        for item in self._load():
            if item.domain_id == domain_id and item.participant_id == participant_id:
                return item
        raise not_found(domain_id)

    def save(self, profile: Profile) -> None:
        profiles = self._load()
        for index, existing in enumerate(profiles):
            if existing.profile_id == profile.profile_id:
                if profile.revision != existing.revision:
                    raise ProfileConflictError("Das Profil wurde zwischenzeitlich geändert.")
                profile.revision = existing.revision + 1
                profiles[index] = profile
                break
        else:
            if profile.revision != 0:
                raise ProfileConflictError("Das Profil wurde zwischenzeitlich erstellt.")
            if any(
                item.domain_id == profile.domain_id
                and item.participant_id == profile.participant_id
                for item in profiles
            ):
                raise ProfileConflictError(
                    "Für diese Domäne gibt es bereits ein Profil des Teilnehmers."
                )
            profile.revision = 1
            profiles.append(profile)
        self._write(profiles)

    def delete(self, domain_id: str, participant_id: str | None = None) -> None:
        profiles = self._load()
        remaining = [
            item
            for item in profiles
            if not (item.domain_id == domain_id and item.participant_id == participant_id)
        ]
        if len(remaining) == len(profiles):
            raise not_found(domain_id)
        self._write(remaining)

    def _load(self) -> list[Profile]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileRepositoryError("Die Profil-Datei enthält kein gültiges JSON.") from exc
        except OSError as exc:
            raise ProfileRepositoryError("Die Profil-Datei konnte nicht gelesen werden.") from exc
        return self._decode(data)

    def _decode(self, data: Any) -> list[Profile]:
        if not isinstance(data, dict):
            raise ProfileRepositoryError("Die Profil-Datei hat ein ungültiges Format.")
        if data.get("version") != PROFILE_STORAGE_VERSION:
            raise ProfileRepositoryError("Die Profil-Datei hat eine nicht unterstützte Version.")
        profiles = data.get("profiles")
        if not isinstance(profiles, list):
            raise ProfileRepositoryError("Die Profil-Datei enthält keine gültige Profil-Liste.")
        try:
            decoded = [Profile.model_validate(item) for item in profiles]
        except ValidationError as exc:
            raise ProfileRepositoryError("Die Profil-Datei enthält ungültige Profil-Daten.") from exc
        keys = [(item.participant_id, item.domain_id) for item in decoded]
        if len(keys) != len(set(keys)):
            raise ProfileRepositoryError(
                "Die Profil-Datei enthält mehrere Profile für dieselbe Domäne und denselben Teilnehmer."
            )
        return decoded

    def _write(self, profiles: list[Profile]) -> None:
        payload = {
            "version": PROFILE_STORAGE_VERSION,
            "profiles": [item.model_dump(mode="json") for item in profiles],
        }
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ProfileRepositoryError("Die Profil-Datei konnte nicht gespeichert werden.") from exc


class ParticipantProfileRepository:
    """Restricts a profile repository to one participant."""

    def __init__(self, repository: ProfileRepository, participant_id: str):
        self.repository = repository
        self.participant_id = participant_id

    def list(self, participant_id: str | None = None) -> list[Profile]:
        if participant_id not in {None, self.participant_id}:
            return []
        return self.repository.list(self.participant_id)

    def get(self, domain_id: str, participant_id: str | None = None) -> Profile:
        if participant_id not in {None, self.participant_id}:
            raise not_found(domain_id)
        return self.repository.get(domain_id, self.participant_id)

    def save(self, profile: Profile) -> None:
        if profile.participant_id is None:
            profile.participant_id = self.participant_id
        if profile.participant_id != self.participant_id:
            raise not_found(profile.domain_id)
        self.repository.save(profile)

    def delete(self, domain_id: str, participant_id: str | None = None) -> None:
        if participant_id not in {None, self.participant_id}:
            raise not_found(domain_id)
        self.repository.delete(domain_id, self.participant_id)
