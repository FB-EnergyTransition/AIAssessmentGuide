import json

import pytest

from guideos.chat import (
    append_assistant_message,
    append_user_message,
    create_initial_chat_state,
    create_next_chat,
)
from guideos.storage import ChatStorageError, load_chat_state, save_chat_state


def test_load_chat_state_returns_default_for_missing_file(tmp_path):
    chat_state = load_chat_state(tmp_path / "missing" / "chats.json")

    assert chat_state == create_initial_chat_state()


def test_save_and_load_chat_state_roundtrips_multiple_chats(tmp_path):
    path = tmp_path / ".guideos" / "chats.json"
    chat_state = create_initial_chat_state()
    append_user_message(chat_state["chats"][0]["messages"], "Hallo")
    append_assistant_message(chat_state["chats"][0]["messages"], "Hi")
    create_next_chat(chat_state)
    append_user_message(chat_state["chats"][1]["messages"], "Zweiter Chat")

    save_chat_state(chat_state, path)

    assert load_chat_state(path) == chat_state


def test_save_chat_state_writes_versioned_json(tmp_path):
    path = tmp_path / ".guideos" / "chats.json"
    chat_state = create_initial_chat_state()

    save_chat_state(chat_state, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "version": 1,
        "active_chat_id": 1,
        "next_chat_id": 2,
        "chats": [{"id": 1, "title": "Neuer Chat", "messages": []}],
    }


def test_load_chat_state_rejects_malformed_json(tmp_path):
    path = tmp_path / "chats.json"
    path.write_text("{not-json}", encoding="utf-8")

    with pytest.raises(ChatStorageError, match="JSON"):
        load_chat_state(path)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"version": 99, "active_chat_id": 1, "next_chat_id": 2, "chats": []},
        {"version": 1, "active_chat_id": 1, "next_chat_id": 2, "chats": []},
        {
            "version": 1,
            "active_chat_id": 99,
            "next_chat_id": 2,
            "chats": [{"id": 1, "title": "Chat", "messages": []}],
        },
        {
            "version": 1,
            "active_chat_id": 1,
            "next_chat_id": 1,
            "chats": [{"id": 1, "title": "Chat", "messages": []}],
        },
        {
            "version": 1,
            "active_chat_id": 1,
            "next_chat_id": 2,
            "chats": [{"id": 1, "title": "Chat", "messages": [{"role": "user"}]}],
        },
    ],
)
def test_load_chat_state_rejects_invalid_schema(tmp_path, data):
    path = tmp_path / "chats.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ChatStorageError):
        load_chat_state(path)


def test_chat_state_store_from_env_selects_backend(monkeypatch, tmp_path):
    from guideos.storage import LocalChatStateStore, chat_state_store_from_env
    from guideos.storage_postgres import PostgresChatStateStore

    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    local = chat_state_store_from_env(tmp_path / "chats.json")
    assert isinstance(local, LocalChatStateStore)

    state = create_initial_chat_state()
    state["chats"][0]["title"] = "Gespeichert"
    local.save(state)
    assert local.load()["chats"][0]["title"] == "Gespeichert"

    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://user:pw@db/guideos")
    monkeypatch.setenv("GUIDEOS_PARTICIPANT_ID", "tester")
    store = chat_state_store_from_env(tmp_path / "chats.json")
    assert isinstance(store, PostgresChatStateStore)
    assert store.owner == "tester"
