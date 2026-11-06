import pytest
from pydantic import ValidationError

from guideos.conversations import Conversation, ConversationMessage, MessageSection
from guideos.knowledge.models import SourceRef


def test_new_conversation_starts_empty_and_references_profile():
    conversation = Conversation.create(
        domain_id="verein-demo",
        title="PV und Speicher",
        participant_id="participant-1",
        profile_id="prof_1",
    )

    assert conversation.conversation_id.startswith("conv_")
    assert conversation.messages == []
    assert conversation.revision == 0
    assert conversation.participant_id == "participant-1"
    assert conversation.profile_id == "prof_1"
    assert conversation.updated_at >= conversation.created_at


def test_two_conversations_share_profile_but_keep_histories_isolated():
    first = Conversation.create(
        domain_id="verein-demo", title="A", profile_id="prof_1"
    )
    second = Conversation.create(
        domain_id="verein-demo", title="B", profile_id="prof_1"
    )

    first.add_message("user", "Hallo aus A")

    assert first.conversation_id != second.conversation_id
    assert first.profile_id == second.profile_id
    assert [message.content for message in first.messages] == ["Hallo aus A"]
    assert second.messages == []


def test_add_message_appends_in_order_and_touches_updated_at():
    conversation = Conversation.create(domain_id="verein-demo", title="A")
    before = conversation.updated_at

    user = conversation.add_message("user", "Frage")
    assistant = conversation.add_message("assistant", "Antwort")

    assert isinstance(user, ConversationMessage)
    assert user.message_id.startswith("msg_")
    assert user.message_id != assistant.message_id
    assert [(m.role, m.content) for m in conversation.messages] == [
        ("user", "Frage"),
        ("assistant", "Antwort"),
    ]
    assert conversation.updated_at >= before


def test_conversation_models_are_strict():
    with pytest.raises(ValidationError):
        ConversationMessage(role="system", content="x")
    valid = Conversation.create(domain_id="verein-demo", title="A").model_dump(mode="json")
    with pytest.raises(ValidationError):
        Conversation.model_validate({**valid, "facts": []})


def test_conversation_roundtrips_through_json():
    conversation = Conversation.create(
        domain_id="verein-demo", title="A", profile_id="prof_1"
    )
    conversation.add_message("user", "Frage")

    restored = Conversation.model_validate(conversation.model_dump(mode="json"))

    assert restored == conversation


def test_messages_stored_before_sections_existed_still_load():
    stored = {
        "conversation_id": "conv_alt",
        "domain_id": "verein-demo",
        "title": "Alt",
        "messages": [
            {
                "message_id": "msg_1",
                "role": "assistant",
                "content": "Antwort",
                "created_at": "2026-09-01T10:00:00Z",
            }
        ],
    }

    conversation = Conversation.model_validate(stored)

    assert conversation.messages[0].sections == []


def test_add_message_keeps_sections_with_their_sources():
    conversation = Conversation.create(domain_id="verein-demo", title="A")
    section = MessageSection(
        text="Aussage", sources=[SourceRef(kind="document", title="Leitfaden", locator="Seite 2")]
    )

    message = conversation.add_message("assistant", "Aussage  \n_Quelle: …_", [section])

    assert message.sections == [section]
    reloaded = Conversation.model_validate(conversation.model_dump(mode="json"))
    assert reloaded.messages[0].sections[0].sources[0].locator == "Seite 2"


def test_result_session_id_marks_which_assessment_the_result_chat_reflects():
    conversation = Conversation.create(domain_id="verein-demo", title="Ergebnis", profile_id="prof_1")

    assert conversation.result_session_id is None
    conversation.result_session_id = "sess_1"
    assert Conversation.model_validate(conversation.model_dump(mode="json")) == conversation
