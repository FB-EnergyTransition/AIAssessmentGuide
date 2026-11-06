import os
from pathlib import Path

from guideos.tools.client import ToolClient
from guideos.tools.service import ToolService
from guideos.tools.store import DEFAULT_TOOL_SERVERS_PATH, LocalToolServerStore, ToolServerStore


def tool_servers_path_from_env() -> Path:
    return Path(os.environ.get("GUIDEOS_TOOL_SERVERS_PATH", str(DEFAULT_TOOL_SERVERS_PATH)))


def tool_server_store_from_env(path: Path | None = None) -> ToolServerStore:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.tools.postgres_store import PostgresToolServerStore

        return PostgresToolServerStore(database_url)
    return LocalToolServerStore(path or tool_servers_path_from_env())


def tool_service_from_env(path: Path | None = None) -> ToolService:
    return ToolService(tool_server_store_from_env(path), ToolClient())
