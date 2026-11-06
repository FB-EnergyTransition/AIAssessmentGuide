"""Deletion of a participant's profile by the participant: profile, its
conversations and the assessment sessions of that domain go together."""

import pytest

from guideos.assessment.models import AssessmentSession
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import ConversationService
from guideos.domains.loader import load_domain_pack
from guideos.profiles.repository import LocalProfileRepository
from guideos.profiles.service import delete_profile_data

from tests.test_streamlit_adapter import _completed_response


def _stores(tmp_path):
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    return assessments, profiles, conversations


def test_delete_profile_data_removes_profile_chats_and_sessions_of_the_domain(tmp_path):
    assessments, profiles, conversations = _stores(tmp_path)
    pack = load_domain_pack()
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles)
    first = _completed_response(orchestrator)
    second = _completed_response(orchestrator)
    other = AssessmentSession.create(domain_id="other-demo", domain_version="1.1.0",
                                     domain_content_hash="x", locale="de")
    assessments.save(other)
    service = ConversationService(conversations, profiles, provider=None)
    chat = service.create(pack.domain_id)
    profile = profiles.get(pack.domain_id)

    summary = delete_profile_data(profiles, conversations, assessments, pack.domain_id)

    assert summary.profile_id == profile.profile_id
    assert summary.conversation_ids == [chat.conversation_id]
    assert sorted(summary.session_ids) == sorted([first.session_id, second.session_id])
    with pytest.raises(LookupError):
        profiles.get(pack.domain_id)
    assert conversations.list() == []
    assert [s.session_id for s in assessments.list()] == [other.session_id]


def test_delete_profile_data_without_profile_raises_lookup_error(tmp_path):
    assessments, profiles, conversations = _stores(tmp_path)
    with pytest.raises(LookupError):
        delete_profile_data(profiles, conversations, assessments, "verein-demo")
    assert assessments.list() == []
