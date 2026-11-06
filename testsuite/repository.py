from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol


KINDS = ("runs", "cases", "events", "assertions", "artifacts", "ratings")
TERMINAL = {"passed", "failed", "error", "cancelled", "skipped"}


def validate_id(value: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("Ungültige Lauf-ID.")


class RunRepository(Protocol):
    def write(self, kind: str, record: dict) -> None: ...
    def read(self, run_id: str) -> dict[str, list[dict]]: ...
    def runs(self) -> list[dict]: ...
    def delete(self, run_id: str) -> None: ...


class JsonlRepository:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id):
        validate_id(run_id)
        path = self.directory / f"{run_id}.jsonl"
        if path.is_symlink():
            raise ValueError("Symbolische Ergebnisdateien sind nicht erlaubt.")
        return path

    def write(self, kind: str, record: dict) -> None:
        if kind not in KINDS:
            raise ValueError("Unbekannter Ergebnistyp.")
        line = json.dumps({"kind": kind, "data": record}, ensure_ascii=False, allow_nan=False)
        with self._path(record["run_id"]).open("a", encoding="utf-8") as output:
            output.write(line + "\n")
            output.flush()
            os.fsync(output.fileno())

    def read(self, run_id: str) -> dict[str, list[dict]]:
        state = {kind: {} for kind in KINDS}
        with self._path(run_id).open(encoding="utf-8") as source:
            for line in source:
                entry = json.loads(line)
                state[entry["kind"]][entry["data"]["id"]] = entry["data"]
        return {kind: list(records.values()) for kind, records in state.items()}

    def runs(self) -> list[dict]:
        return [run for path in sorted(self.directory.glob("*.jsonl")) for run in self.read(path.stem)["runs"]]

    def delete(self, run_id: str) -> None:
        if self.read(run_id)["runs"][0]["status"] not in TERMINAL:
            raise ValueError("Aktive Läufe dürfen nicht gelöscht werden.")
        self._path(run_id).unlink()


def repository_from_env() -> RunRepository:
    """Ergebnisspeicher für Werkzeuge neben der Runner-CLI, etwa die Bewertungsansicht."""
    database_url = os.environ.get("GUIDEOS_TESTSUITE_DATABASE_URL")
    if database_url:
        from testsuite.postgres import PostgresRepository, migrate
        migrate(database_url)
        return PostgresRepository(database_url)
    return JsonlRepository(Path(os.environ.get("GUIDEOS_TESTSUITE_RESULTS_DIR", ".guideos/testsuite")))
