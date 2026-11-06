import os
from uuid import uuid4

import pytest

from guideos.assessment.migrations import migrate_database
from guideos.assessment.models import AssessmentSession
from guideos.assessment.postgres_repository import PostgresAssessmentRepository
from guideos.assessment.repository import (
    AssessmentConflictError,
    ParticipantAssessmentRepository,
)


POSTGRES_URL = os.environ.get("GUIDEOS_TEST_POSTGRES_URL")


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="GUIDEOS_TEST_POSTGRES_URL is required for PostgreSQL integration tests.",
)
def test_postgres_repository_roundtrips_and_isolates_participants():
    assert POSTGRES_URL is not None
    migrate_database(POSTGRES_URL)
    base_repository = PostgresAssessmentRepository(POSTGRES_URL)
    first = ParticipantAssessmentRepository(base_repository, f"first_{uuid4().hex}")
    second = ParticipantAssessmentRepository(base_repository, f"second_{uuid4().hex}")
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )

    first.save(session)
    stale_copy = first.get(session.session_id)
    current_copy = first.get(session.session_id)
    first.save(current_copy)

    assert first.get(session.session_id).revision == 2
    assert second.list() == []
    with pytest.raises(LookupError):
        second.get(session.session_id)
    with pytest.raises(AssessmentConflictError):
        first.save(stale_copy)
