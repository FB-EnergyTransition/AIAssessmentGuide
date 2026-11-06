import json
import os
from pathlib import Path
from typing import Any

from guideos.chat import Chat, ChatState, Message, create_initial_chat_state


CHAT_STORAGE_VERSION = 1
DEFAULT_CHAT_STORAGE_PATH = Path(".guideos") / "chats.json"


class ChatStorageError(Exception):
    pass


def load_chat_state(path: Path = DEFAULT_CHAT_STORAGE_PATH) -> ChatState:
    if not path.exists():
        return create_initial_chat_state()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ChatStorageError("Die Chat-Datei enthält kein gültiges JSON.") from exc
    except OSError as exc:
        raise ChatStorageError("Die Chat-Datei konnte nicht gelesen werden.") from exc

    return _decode_chat_state(data)


def save_chat_state(
    chat_state: ChatState,
    path: Path = DEFAULT_CHAT_STORAGE_PATH,
) -> None:
    payload = encode_chat_state(chat_state)
    temp_path = path.with_name(f".{path.name}.tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ChatStorageError("Die Chat-Datei konnte nicht gespeichert werden.") from exc


def encode_chat_state(chat_state: ChatState) -> dict[str, Any]:
    return {
        "version": CHAT_STORAGE_VERSION,
        "active_chat_id": chat_state["active_chat_id"],
        "next_chat_id": chat_state["next_chat_id"],
        "chats": chat_state["chats"],
    }


def decode_chat_state(data: Any) -> ChatState:
    return _decode_chat_state(data)


class LocalChatStateStore:
    """Chat history as a versioned JSON file (the default without PostgreSQL)."""

    def __init__(self, path: Path = DEFAULT_CHAT_STORAGE_PATH):
        self.path = path

    def load(self) -> ChatState:
        return load_chat_state(self.path)

    def save(self, chat_state: ChatState) -> None:
        save_chat_state(chat_state, self.path)


def chat_state_store_from_env(path: Path = DEFAULT_CHAT_STORAGE_PATH):
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.pilot.access import local_participant_id
        from guideos.storage_postgres import PostgresChatStateStore

        return PostgresChatStateStore(database_url, local_participant_id())
    return LocalChatStateStore(path)


def _decode_chat_state(data: Any) -> ChatState:
    if not isinstance(data, dict):
        raise ChatStorageError("Die Chat-Datei hat ein ungültiges Format.")
    if data.get("version") != CHAT_STORAGE_VERSION:
        raise ChatStorageError("Die Chat-Datei hat eine nicht unterstützte Version.")

    active_chat_id = data.get("active_chat_id")
    next_chat_id = data.get("next_chat_id")
    chats_data = data.get("chats")

    if not _is_int(active_chat_id) or not _is_int(next_chat_id):
        raise ChatStorageError("Die Chat-Datei enthält ungültige Chat-IDs.")
    if not isinstance(chats_data, list) or not chats_data:
        raise ChatStorageError("Die Chat-Datei enthält keine gültige Chat-Liste.")

    chats = [_decode_chat(chat_data) for chat_data in chats_data]
    chat_ids = [chat["id"] for chat in chats]
    if len(set(chat_ids)) != len(chat_ids):
        raise ChatStorageError("Die Chat-Datei enthält doppelte Chat-IDs.")
    if active_chat_id not in chat_ids:
        raise ChatStorageError("Der aktive Chat wurde in der Chat-Datei nicht gefunden.")
    if next_chat_id <= max(chat_ids):
        raise ChatStorageError("Die nächste Chat-ID ist in der Chat-Datei ungültig.")

    return {
        "chats": chats,
        "active_chat_id": active_chat_id,
        "next_chat_id": next_chat_id,
    }


def _decode_chat(data: Any) -> Chat:
    if not isinstance(data, dict):
        raise ChatStorageError("Die Chat-Datei enthält einen ungültigen Chat.")

    chat_id = data.get("id")
    title = data.get("title")
    messages_data = data.get("messages")

    if not _is_int(chat_id) or not isinstance(title, str):
        raise ChatStorageError("Die Chat-Datei enthält ungültige Chat-Metadaten.")
    if not isinstance(messages_data, list):
        raise ChatStorageError("Die Chat-Datei enthält ungültige Nachrichten.")

    return {
        "id": chat_id,
        "title": title,
        "messages": [_decode_message(message_data) for message_data in messages_data],
    }


def _decode_message(data: Any) -> Message:
    if not isinstance(data, dict):
        raise ChatStorageError("Die Chat-Datei enthält eine ungültige Nachricht.")

    role = data.get("role")
    content = data.get("content")
    if not isinstance(role, str) or not isinstance(content, str):
        raise ChatStorageError("Die Chat-Datei enthält ungültige Nachrichtendaten.")

    return {"role": role, "content": content}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
