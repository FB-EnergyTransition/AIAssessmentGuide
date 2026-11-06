import json

import pytest

from guideos.assessment.models import (
    AssessmentAnswer,
    AssessmentChatMessage,
    AssessmentSession,
)
from guideos.assessment.repository import (
    AssessmentConflictError,
    AssessmentRepository,
    AssessmentRepositoryError,
    LocalAssessmentRepository,
    ParticipantAssessmentRepository,
)


def test_local_repository_satisfies_repository_protocol(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")

    assert isinstance(repository, AssessmentRepository)


def test_repository_creates_and_roundtrips_session(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / ".guideos" / "assessments.json")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    session.answers["members_count"] = AssessmentAnswer(
        question_id="members_count",
        field_key="members_count",
        raw_answer="120",
        normalized_value=120,
        confidence=1.0,
    )

    repository.save(session)

    loaded = repository.get(session.session_id)
    assert loaded == session
    assert repository.list() == [session]
    data = json.loads(repository.path.read_text(encoding="utf-8"))
    assert data["version"] == 1


def test_repository_roundtrips_assessment_chat_messages(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    session.chat_messages.append(
        AssessmentChatMessage(
            scope="post_completion",
            role="user",
            content="Warum diese Empfehlung?",
        )
    )

    repository.save(session)

    loaded = repository.get(session.session_id)
    assert loaded.chat_messages[0].scope == "post_completion"
    assert loaded.chat_messages[0].content == "Warum diese Empfehlung?"


def test_repository_loads_old_sessions_without_chat_messages(tmp_path):
    path = tmp_path / "assessments.json"
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    session_data = session.model_dump(mode="json")
    del session_data["chat_messages"]
    path.write_text(
        json.dumps({"version": 1, "sessions": [session_data]}),
        encoding="utf-8",
    )

    loaded = LocalAssessmentRepository(path).get(session.session_id)

    assert loaded.chat_messages == []


def test_repository_updates_existing_session_without_duplicate(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    repository.save(session)
    session.status = "review"

    repository.save(session)

    assert repository.list() == [session]


def test_repository_rejects_stale_session_revision(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    repository.save(session)
    stale_copy = repository.get(session.session_id)
    current_copy = repository.get(session.session_id)

    repository.save(current_copy)

    with pytest.raises(AssessmentConflictError):
        repository.save(stale_copy)


def test_participant_repository_hides_other_participants_sessions(tmp_path):
    base_repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    first = ParticipantAssessmentRepository(base_repository, "participant_first")
    second = ParticipantAssessmentRepository(base_repository, "participant_second")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )

    first.save(session)

    assert session.participant_id == "participant_first"
    assert first.list() == [session]
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get(session.session_id)
    with pytest.raises(LookupError):
        second.delete(session.session_id)


def test_repository_deletes_session(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    repository.save(session)

    repository.delete(session.session_id)

    assert repository.list() == []
    with pytest.raises(LookupError):
        repository.get(session.session_id)


def test_repository_rejects_corrupt_file(tmp_path):
    path = tmp_path / "assessments.json"
    path.write_text("{not-json}", encoding="utf-8")
    repository = LocalAssessmentRepository(path)

    with pytest.raises(AssessmentRepositoryError, match="JSON"):
        repository.list()


def test_repository_rejects_unsupported_version(tmp_path):
    path = tmp_path / "assessments.json"
    path.write_text(json.dumps({"version": 99, "sessions": []}), encoding="utf-8")
    repository = LocalAssessmentRepository(path)

    with pytest.raises(AssessmentRepositoryError, match="Version"):
        repository.list()
