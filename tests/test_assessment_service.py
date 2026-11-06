from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.assessment.service import handle_assessment_payload
from guideos.domains.loader import load_domain_pack


def test_handle_assessment_payload_starts_assessment(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    response = handle_assessment_payload(
        orchestrator,
        {"kind": "start", "locale": "de"},
    )

    assert response.ok is True
    assert response.session_id is not None
    assert response.status == "in_progress"
    assert response.current_question.field_key == "club_type"


def test_handle_assessment_payload_returns_invalid_input_for_bad_payload(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    response = handle_assessment_payload(
        orchestrator,
        {"kind": "complete"},
    )

    assert response.ok is False
    assert response.errors[0].code == "invalid_input"


def test_handle_assessment_payload_returns_missing_session_error(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    response = handle_assessment_payload(
        orchestrator,
        {"kind": "complete", "session_id": "sess_missing"},
    )

    assert response.ok is False
    assert response.session_id == "sess_missing"
    assert response.errors[0].code == "session_not_found"


def test_handle_assessment_payload_returns_wrong_question_error(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    started = handle_assessment_payload(
        orchestrator,
        {"kind": "start", "locale": "de"},
    )

    response = handle_assessment_payload(
        orchestrator,
        {
            "kind": "submit_answer",
            "session_id": started.session_id,
            "question_id": "members_count",
            "value": 120,
        },
    )

    assert response.ok is False
    assert response.session_id == started.session_id
    assert response.errors[0].code == "invalid_question"


def _orchestrator(tmp_path):
    return AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )
