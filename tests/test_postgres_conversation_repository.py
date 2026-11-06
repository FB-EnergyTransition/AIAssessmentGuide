import os
from uuid import uuid4

import pytest

from guideos.assessment.migrations import migrate_database
from guideos.conversations import Conversation
from guideos.conversations.postgres_repository import PostgresConversationRepository
from guideos.conversations.repository import (
    ConversationConflictError,
    ConversationRepositoryError,
    ParticipantConversationRepository,
)


POSTGRES_URL = os.environ.get("GUIDEOS_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="GUIDEOS_TEST_POSTGRES_URL is required for PostgreSQL integration tests.",
)


def test_postgres_repository_roundtrips_and_isolates_participants():
    assert POSTGRES_URL is not None
    migrate_database(POSTGRES_URL)
    base = PostgresConversationRepository(POSTGRES_URL)
    first = ParticipantConversationRepository(base, f"first_{uuid4().hex}")
    second = ParticipantConversationRepository(base, f"second_{uuid4().hex}")
    conversation = Conversation.create(
        domain_id="verein-demo", title="A", profile_id="prof_1"
    )
    conversation.add_message("user", "Frage")

    first.save(conversation)
    stale = first.get(conversation.conversation_id)
    current = first.get(conversation.conversation_id)
    current.add_message("assistant", "Antwort")
    first.save(current)

    reloaded = first.get(conversation.conversation_id)
    assert reloaded.revision == 2
    assert [m.content for m in reloaded.messages] == ["Frage", "Antwort"]
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get(conversation.conversation_id)
    with pytest.raises(ConversationConflictError):
        first.save(stale)

    first.delete(conversation.conversation_id)
    with pytest.raises(LookupError):
        first.get(conversation.conversation_id)


def test_postgres_repository_requires_participant():
    assert POSTGRES_URL is not None
    migrate_database(POSTGRES_URL)

    with pytest.raises(ConversationRepositoryError):
        PostgresConversationRepository(POSTGRES_URL).save(
            Conversation.create(domain_id="verein-demo", title="A")
        )
