import json

import pytest

from guideos.conversations import Conversation
from guideos.conversations.repository import (
    ConversationConflictError,
    ConversationRepository,
    ConversationRepositoryError,
    LocalConversationRepository,
    ParticipantConversationRepository,
)


def test_local_repository_satisfies_protocol(tmp_path):
    repository = LocalConversationRepository(tmp_path / "conversations.json")

    assert isinstance(repository, ConversationRepository)


def test_repository_creates_and_roundtrips_conversation(tmp_path):
    repository = LocalConversationRepository(tmp_path / ".guideos" / "conversations.json")
    conversation = Conversation.create(
        domain_id="verein-demo",
        title="PV und Speicher",
        participant_id="participant-1",
        profile_id="prof_1",
    )
    conversation.add_message("user", "Frage")

    repository.save(conversation)

    loaded = repository.get(conversation.conversation_id)
    assert loaded == conversation
    assert loaded.revision == 1
    assert repository.list() == [conversation]
    data = json.loads(repository.path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert list(data) == ["version", "conversations"]


def test_repository_updates_existing_conversation_without_duplicate(tmp_path):
    repository = LocalConversationRepository(tmp_path / "conversations.json")
    conversation = Conversation.create(domain_id="verein-demo", title="A")
    repository.save(conversation)

    conversation.add_message("user", "Nachtrag")
    repository.save(conversation)

    assert conversation.revision == 2
    assert [c.conversation_id for c in repository.list()] == [conversation.conversation_id]
    assert repository.get(conversation.conversation_id).messages[0].content == "Nachtrag"


def test_repository_rejects_stale_revision(tmp_path):
    repository = LocalConversationRepository(tmp_path / "conversations.json")
    conversation = Conversation.create(domain_id="verein-demo", title="A")
    repository.save(conversation)
    stale = repository.get(conversation.conversation_id)
    current = repository.get(conversation.conversation_id)
    repository.save(current)

    with pytest.raises(ConversationConflictError):
        repository.save(stale)
    with pytest.raises(ConversationConflictError):
        repository.save(Conversation.create(domain_id="verein-demo", title="B").model_copy(update={"revision": 3}))


def test_participant_repository_isolates_conversations(tmp_path):
    base = LocalConversationRepository(tmp_path / "conversations.json")
    first = ParticipantConversationRepository(base, "participant-1")
    second = ParticipantConversationRepository(base, "participant-2")
    conversation = Conversation.create(domain_id="verein-demo", title="A")

    first.save(conversation)

    assert conversation.participant_id == "participant-1"
    assert first.list() == [conversation]
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get(conversation.conversation_id)
    with pytest.raises(LookupError):
        second.delete(conversation.conversation_id)
    with pytest.raises(LookupError):
        second.save(conversation)
    assert base.get(conversation.conversation_id) == conversation


def test_repository_deletes_conversation(tmp_path):
    repository = LocalConversationRepository(tmp_path / "conversations.json")
    conversation = Conversation.create(domain_id="verein-demo", title="A")
    repository.save(conversation)

    repository.delete(conversation.conversation_id)

    assert repository.list() == []
    with pytest.raises(LookupError):
        repository.get(conversation.conversation_id)
    with pytest.raises(LookupError):
        repository.delete(conversation.conversation_id)


def test_repository_rejects_corrupt_or_unsupported_file(tmp_path):
    path = tmp_path / "conversations.json"
    path.write_text("{nicht json", encoding="utf-8")
    with pytest.raises(ConversationRepositoryError):
        LocalConversationRepository(path).list()

    path.write_text(json.dumps({"version": 99, "conversations": []}), encoding="utf-8")
    with pytest.raises(ConversationRepositoryError):
        LocalConversationRepository(path).list()


def test_repository_factory_selects_backend_from_environment(tmp_path, monkeypatch):
    from guideos.conversations.factory import conversation_repository_from_env
    from guideos.conversations.postgres_repository import PostgresConversationRepository

    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    local = conversation_repository_from_env(tmp_path / "conversations.json")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://test:test@localhost/test")
    postgres = conversation_repository_from_env(tmp_path / "conversations.json")

    assert isinstance(local, LocalConversationRepository)
    assert local.path == tmp_path / "conversations.json"
    assert isinstance(postgres, PostgresConversationRepository)


def test_repository_rejects_session_bound_files_of_version_one(tmp_path):
    """Conversations stored before profiles existed referenced a session;
    such files are refused with a hint instead of being migrated."""
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps({"version": 1, "conversations": []}), encoding="utf-8")

    with pytest.raises(ConversationRepositoryError, match="Version 1.*Profil.*löschen"):
        LocalConversationRepository(path).list()
