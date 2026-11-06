import pytest
from pydantic import ValidationError

from guideos.assessment.contract import (
    AcceptProposedAnswerInput,
    AssessmentContractError,
    AssessmentContractResponse,
    CompleteAssessmentInput,
    DiscardProposedAnswerInput,
    ContractAnswer,
    ContractProgress,
    ContractQuestion,
    ContractQuestionOption,
    QuestionHelpInput,
    ReviseAnswerInput,
    StartAssessmentInput,
    SubmitAnswerInput,
    SubmitTextAnswerInput,
    parse_assessment_input,
    to_contract_response,
)
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.domains.loader import load_domain_pack


def test_contract_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        StartAssessmentInput.model_validate({"kind": "start", "extra": True})

    with pytest.raises(ValidationError):
        AssessmentContractError.model_validate(
            {
                "code": "invalid_input",
                "message": "ungültig",
                "extra": True,
            }
        )

    with pytest.raises(ValidationError):
        AssessmentContractResponse.model_validate(
            {
                "ok": False,
                "assistant_message": "Fehler",
                "errors": [{"code": "invalid_input", "message": "ungültig"}],
                "extra": True,
            }
        )


def test_all_assessment_inputs_serialize_stably():
    inputs = [
        StartAssessmentInput(locale="de"),
        SubmitAnswerInput(session_id="sess_1", question_id="q1", value=1),
        SubmitTextAnswerInput(
            session_id="sess_1",
            question_id="q1",
            text="Antwort",
            endpoint="http://fake",
            model="fake",
        ),
        ReviseAnswerInput(session_id="sess_1", question_id="q1", value=2),
        CompleteAssessmentInput(session_id="sess_1"),
        QuestionHelpInput(
            session_id="sess_1",
            question_id="q1",
            text="Hilfe?",
            endpoint="http://fake",
            model="fake",
        ),
        AcceptProposedAnswerInput(session_id="sess_1", message_id="msg_1"),
        DiscardProposedAnswerInput(session_id="sess_1", message_id="msg_1"),
    ]

    assert [item.model_dump(mode="json")["kind"] for item in inputs] == [
        "start",
        "submit_answer",
        "submit_text_answer",
        "revise_answer",
        "complete",
        "question_help",
        "accept_proposed_answer",
        "discard_proposed_answer",
    ]


def test_parse_assessment_input_rejects_removed_completion_chat():
    with pytest.raises(ValidationError):
        parse_assessment_input(
            {"kind": "completion_chat", "session_id": "s", "text": "x",
             "endpoint": "http://fake", "model": "fake"}
        )


def test_parse_assessment_input_accepts_known_payload_kinds():
    parsed = [
        parse_assessment_input({"kind": "start", "locale": "de"}),
        parse_assessment_input(
            {
                "kind": "submit_answer",
                "session_id": "sess_1",
                "question_id": "q1",
                "value": 1,
            }
        ),
        parse_assessment_input({"kind": "complete", "session_id": "sess_1"}),
        parse_assessment_input(
            {
                "kind": "question_help",
                "session_id": "sess_1",
                "question_id": "q1",
                "text": "Hilfe?",
                "endpoint": "http://fake",
                "model": "fake",
            }
        ),
    ]

    assert isinstance(parsed[0], StartAssessmentInput)
    assert isinstance(parsed[1], SubmitAnswerInput)
    assert isinstance(parsed[2], CompleteAssessmentInput)
    assert isinstance(parsed[3], QuestionHelpInput)


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "unknown"},
        {"kind": "complete"},
        {"kind": "start", "extra": True},
    ],
)
def test_parse_assessment_input_rejects_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        parse_assessment_input(payload)


def test_contract_response_serializes_to_json_with_datetime():
    response = AssessmentContractResponse(
        ok=True,
        session_id="sess_1",
        status="in_progress",
        assistant_message="Frage?",
        current_question=ContractQuestion(
            question_id="q1",
            field_key="field",
            field_type="select",
            text="Frage?",
            required=True,
            allow_unknown=False,
            options=[ContractQuestionOption(value="yes", label="Ja")],
        ),
        progress=ContractProgress(answered_required=0, required_total=1),
        answers={
            "field": ContractAnswer(
                question_id="q1",
                field_key="field",
                raw_answer="yes",
                normalized_value="yes",
                confidence=1.0,
            )
        },
    )

    payload = response.model_dump_json()

    assert '"ok":true' in payload
    assert '"current_question"' in payload


def test_to_contract_response_maps_assessment_state_result_report_and_chat(tmp_path):
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )
    response = _complete_assessment(orchestrator)

    contract = to_contract_response(response)

    assert contract.ok is True
    assert contract.session_id == response.session_id
    assert contract.status == "completed"
    assert contract.current_question is None
    assert contract.progress.required_total == 5
    assert contract.answers["club_type"].normalized_value == "sports"
    assert "score" not in contract.result.model_dump()
    assert contract.result.reason_codes
    assert contract.result.recommendations
    assert contract.report.format == "markdown"
    assert response.session_id in contract.report.content
    assert contract.chat_messages == []


def test_to_contract_response_adds_error_for_validation_errors(tmp_path):
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )
    response = orchestrator.start()
    invalid = orchestrator.submit(
        response.session_id,
        "club_type",
        "castle",
    )

    contract = to_contract_response(invalid)

    assert contract.ok is False
    assert contract.validation_errors[0].code == "invalid_option"
    assert contract.errors[0].code == "validation_failed"
    assert contract.errors[0].field_key == "club_type"


def _complete_assessment(orchestrator):
    response = orchestrator.start()
    for question_id, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        response = orchestrator.submit(response.session_id, question_id, value)
    return orchestrator.complete(response.session_id)


def test_contract_question_carries_type_widget_and_validation():
    from guideos.assessment.contract import to_contract_response
    from guideos.assessment.models import AssessmentResponse
    from guideos.domains.models import QuestionDefinition

    def project(question):
        response = AssessmentResponse(
            session_id="sess_1",
            status="in_progress",
            assistant_message="Frage?",
            current_question=question,
            progress={"answered_required": 0, "required_total": 1},
            answers={},
        )
        return to_contract_response(response).current_question

    channels = project(
        QuestionDefinition.model_validate(
            {
                "question_id": "channels",
                "field_key": "channels",
                "text": "Kanäle?",
                "field_type": "multiselect",
                "required": True,
                "options": [{"value": "email", "label": "E-Mail"}],
                "validation": {"max_count": 3},
            }
        )
    )
    assert channels.field_type == "multiselect"
    assert channels.widget == "checkboxes"
    assert channels.validation.max_count == 3
    assert channels.validation.minimum is None

    summary = project(
        QuestionDefinition.model_validate(
            {
                "question_id": "summary",
                "field_key": "summary",
                "text": "Beschreibung?",
                "field_type": "text",
                "widget": "textarea",
                "required": False,
                "validation": {"max_length": 500, "pattern": "^.+$"},
            }
        )
    )
    assert summary.widget == "textarea"
    assert summary.validation.max_length == 500
    assert summary.validation.pattern == "^.+$"

    founded = project(
        QuestionDefinition.model_validate(
            {
                "question_id": "founded",
                "field_key": "founded",
                "text": "Gründung?",
                "field_type": "date",
                "required": True,
                "validation": {"min_date": "1900-01-01"},
            }
        )
    )
    assert founded.field_type == "date"
    assert founded.widget is None
    assert founded.validation.min_date == "1900-01-01"
    payload = founded.model_dump_json()
    assert '"widget":null' in payload and '"min_date":"1900-01-01"' in payload


def test_contract_question_carries_the_upload_block_of_file_questions():
    from guideos.assessment.contract import to_contract_response
    from guideos.assessment.models import AssessmentResponse
    from guideos.domains.models import QuestionDefinition

    question = QuestionDefinition.model_validate({
        "question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
        "field_type": "file", "required": False,
        "upload": {"extensions": ["csv"], "max_bytes": 2048, "arguments": {"direction": "export"}},
    })
    response = AssessmentResponse(
        session_id="sess_1", status="in_progress", assistant_message="Frage?",
        current_question=question, progress={"answered_required": 0, "required_total": 0}, answers={},
    )

    projected = to_contract_response(response).current_question

    assert projected.field_type == "file" and projected.widget is None
    assert projected.upload.model_dump() == {"extensions": ["csv"], "max_bytes": 2048, "formats": ["auto"]}
    plain = to_contract_response(response.model_copy(update={"current_question": question.model_copy(
        update={"field_type": "text", "upload": None})})).current_question
    assert plain.upload is None


def test_contract_response_lists_the_answered_questions_and_upload_formats(tmp_path):
    """Clients without the pack need the definition of every answered
    question (for revisions) and the format hints of a file question."""
    from guideos.assessment.contract import to_contract_response
    from guideos.assessment.models import AssessmentProgress, AssessmentResponse
    from tests.test_streamlit_adapter import build_demo_assessment, FakeProvider

    orchestrator = build_demo_assessment(FakeProvider(), tmp_path / "assessments.json")
    started = orchestrator.start()
    answered = orchestrator.submit(started.session_id, "club_type", "sports")

    contract = to_contract_response(answered)
    assert [q.question_id for q in contract.questions] == ["club_type"]
    assert contract.questions[0].text and contract.questions[0].options[0].value == "sports"

    from tests.pack_fixtures import tools_pack
    file_question = next(q for q in tools_pack().questions if q.field_type == "file")
    response = AssessmentResponse(
        session_id="sess_x", status="in_progress", assistant_message="", current_question=file_question,
        progress=AssessmentProgress(answered_required=0, required_total=1), answers={},
        upload_formats=["auto", "netz_noe"],
    )
    assert to_contract_response(response).current_question.upload.formats == ["auto", "netz_noe"]
