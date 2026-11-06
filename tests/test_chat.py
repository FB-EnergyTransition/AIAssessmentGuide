import pytest

from guideos.chat import (
    ChatValidationError,
    DEFAULT_CHAT_TITLE,
    append_user_message,
    clear_chat,
    create_chat,
    create_initial_chat_state,
    create_next_chat,
    delete_chat,
    derive_chat_title,
    get_active_chat,
    rollback_pending_user_message,
    validate_chat_input,
)


def test_validate_chat_input_normalizes_values():
    endpoint, model, prompt = validate_chat_input(
        " http://localhost:11434/ ",
        " llama3.2 ",
        " Hallo ",
    )

    assert endpoint == "http://localhost:11434"
    assert model == "llama3.2"
    assert prompt == "Hallo"


@pytest.mark.parametrize(
    ("endpoint", "model", "prompt"),
    [
        ("", "llama3.2", "Hallo"),
        ("localhost:11434", "llama3.2", "Hallo"),
        ("http://localhost:11434", "", "Hallo"),
        ("http://localhost:11434", "llama3.2", ""),
    ],
)
def test_validate_chat_input_rejects_missing_or_invalid_values(endpoint, model, prompt):
    with pytest.raises(ChatValidationError):
        validate_chat_input(endpoint, model, prompt)


def test_append_user_message_mutates_history():
    messages = [{"role": "assistant", "content": "Hi"}]

    append_user_message(messages, "Hallo")

    assert messages == [
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Hallo"},
    ]


def test_rollback_pending_user_message_removes_last_user_message_only():
    messages = [
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Hallo"},
    ]

    rollback_pending_user_message(messages)

    assert messages == [{"role": "assistant", "content": "Hi"}]


def test_rollback_pending_user_message_keeps_non_user_tail():
    messages = [{"role": "assistant", "content": "Hi"}]

    rollback_pending_user_message(messages)

    assert messages == [{"role": "assistant", "content": "Hi"}]


def test_create_chat_starts_with_default_title_and_empty_history():
    chat = create_chat(7)

    assert chat == {"id": 7, "title": DEFAULT_CHAT_TITLE, "messages": []}


def test_create_initial_chat_state_starts_with_one_active_chat():
    chat_state = create_initial_chat_state()

    assert chat_state == {
        "chats": [{"id": 1, "title": DEFAULT_CHAT_TITLE, "messages": []}],
        "active_chat_id": 1,
        "next_chat_id": 2,
    }


def test_create_next_chat_appends_and_activates_new_chat():
    chat_state = create_initial_chat_state()

    chat = create_next_chat(chat_state)

    assert chat == {"id": 2, "title": DEFAULT_CHAT_TITLE, "messages": []}
    assert chat_state == {
        "chats": [
            {"id": 1, "title": DEFAULT_CHAT_TITLE, "messages": []},
            {"id": 2, "title": DEFAULT_CHAT_TITLE, "messages": []},
        ],
        "active_chat_id": 2,
        "next_chat_id": 3,
    }


def test_get_active_chat_returns_matching_chat():
    chats = [create_chat(1), create_chat(2)]

    active_chat = get_active_chat(chats, 2)

    assert active_chat is chats[1]


def test_get_active_chat_raises_for_unknown_chat():
    with pytest.raises(LookupError):
        get_active_chat([create_chat(1)], 99)


def test_derive_chat_title_uses_normalized_prompt():
    assert derive_chat_title("  Hallo   GuideOS  ") == "Hallo GuideOS"


def test_derive_chat_title_falls_back_for_blank_prompt():
    assert derive_chat_title("   ") == DEFAULT_CHAT_TITLE


def test_derive_chat_title_truncates_long_prompt():
    title = derive_chat_title("Wie kann ich diesen sehr langen Prompt sinnvoll kürzen?")

    assert title == "Wie kann ich diesen sehr langen..."


def test_clear_chat_removes_messages_only():
    chat = create_chat(1, title="Bestehender Chat")
    append_user_message(chat["messages"], "Hallo")

    clear_chat(chat)

    assert chat == {"id": 1, "title": "Bestehender Chat", "messages": []}


def test_delete_inactive_chat_keeps_active_chat():
    chat_state = create_initial_chat_state()
    create_next_chat(chat_state)
    chat_state["active_chat_id"] = 1

    delete_chat(chat_state, 2)

    assert chat_state == {
        "chats": [{"id": 1, "title": DEFAULT_CHAT_TITLE, "messages": []}],
        "active_chat_id": 1,
        "next_chat_id": 3,
    }


def test_delete_active_chat_selects_remaining_chat():
    chat_state = create_initial_chat_state()
    create_next_chat(chat_state)

    delete_chat(chat_state, 2)

    assert chat_state == {
        "chats": [{"id": 1, "title": DEFAULT_CHAT_TITLE, "messages": []}],
        "active_chat_id": 1,
        "next_chat_id": 3,
    }


def test_delete_last_chat_creates_new_empty_chat():
    chat_state = create_initial_chat_state()

    delete_chat(chat_state, 1)

    assert chat_state == {
        "chats": [{"id": 2, "title": DEFAULT_CHAT_TITLE, "messages": []}],
        "active_chat_id": 2,
        "next_chat_id": 3,
    }


def test_delete_chat_raises_for_unknown_chat():
    chat_state = create_initial_chat_state()

    with pytest.raises(LookupError):
        delete_chat(chat_state, 99)
