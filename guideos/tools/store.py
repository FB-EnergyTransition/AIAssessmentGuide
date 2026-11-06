"""Persistence of the tool server registrations: a versioned JSON file
locally (.guideos/mcp_servers.json), PostgreSQL in the pilot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from guideos.tools.models import ToolServer

TOOL_SERVER_STORAGE_VERSION = 1
DEFAULT_TOOL_SERVERS_PATH = Path(".guideos") / "mcp_servers.json"


class ToolStoreError(Exception):
    pass


class ToolServerStore(Protocol):
    def list(self, domain_id: str) -> list[ToolServer]: ...

    def get(self, domain_id: str, name: str) -> ToolServer | None: ...

    def save(self, server: ToolServer) -> None: ...

    def delete(self, domain_id: str, name: str) -> bool: ...


def duplicate_name(server: ToolServer) -> ToolStoreError:
    return ToolStoreError(
        f"Ein Server mit dem Namen {server.name} existiert bereits für die Domäne {server.domain_id}."
    )


class LocalToolServerStore:
    def __init__(self, path: Path = DEFAULT_TOOL_SERVERS_PATH) -> None:
        self.path = path

    def list(self, domain_id: str) -> list[ToolServer]:
        return [item for item in self._load() if item.domain_id == domain_id]

    def get(self, domain_id: str, name: str) -> ToolServer | None:
        return next((item for item in self.list(domain_id) if item.name == name), None)

    def save(self, server: ToolServer) -> None:
        servers = self._load()
        for existing in servers:
            if (existing.domain_id, existing.name) == (server.domain_id, server.name) \
                    and existing.server_id != server.server_id:
                raise duplicate_name(server)
        for index, existing in enumerate(servers):
            if existing.server_id == server.server_id:
                servers[index] = server
                break
        else:
            servers.append(server)
        self._write(servers)

    def delete(self, domain_id: str, name: str) -> bool:
        servers = self._load()
        remaining = [item for item in servers if (item.domain_id, item.name) != (domain_id, name)]
        if len(remaining) == len(servers):
            return False
        self._write(remaining)
        return True

    def _load(self) -> list[ToolServer]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolStoreError("Die Datei der Werkzeugserver enthält kein gültiges JSON.") from exc
        except OSError as exc:
            raise ToolStoreError("Die Datei der Werkzeugserver konnte nicht gelesen werden.") from exc
        if not isinstance(data, dict) or data.get("version") != TOOL_SERVER_STORAGE_VERSION:
            raise ToolStoreError("Die Datei der Werkzeugserver hat eine nicht unterstützte Version.")
        try:
            return [ToolServer.model_validate(item) for item in data.get("servers")]
        except (TypeError, ValidationError) as exc:
            raise ToolStoreError("Die Datei der Werkzeugserver enthält ungültige Daten.") from exc

    def _write(self, servers: list[ToolServer]) -> None:
        payload = {"version": TOOL_SERVER_STORAGE_VERSION,
                   "servers": [item.model_dump(mode="json") for item in servers]}
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ToolStoreError("Die Datei der Werkzeugserver konnte nicht gespeichert werden.") from exc
