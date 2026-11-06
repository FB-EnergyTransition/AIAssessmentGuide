import pytest

from guideos.assessment.contract import (
    AcceptProposedAnswerInput,
    CompleteAssessmentInput,
    DiscardProposedAnswerInput,
    QuestionHelpInput,
    ReviseAnswerInput,
    StartAssessmentInput,
    SubmitAnswerInput,
    SubmitTextAnswerInput,
)
from guideos.assessment.context_chat import QuestionHelpResult
from guideos.assessment.orchestrator import (
    AssessmentOrchestrator,
    AssessmentWorkflowError,
)
from guideos.assessment.interpreter import InterpretationResult
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.domains.loader import load_domain_pack
from guideos.domains.models import DomainPack
from guideos.providers.base import ProviderError


@pytest.fixture
def orchestrator(tmp_path):
    return AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )


def test_start_creates_session_with_first_question(orchestrator):
    response = orchestrator.start()

    assert response.status == "in_progress"
    assert response.current_question.field_key == "club_type"
    assert response.progress.answered_required == 0
    assert response.progress.required_total == 5


def test_submit_validates_answer_and_advances(orchestrator):
    response = orchestrator.start()

    response = orchestrator.submit(
        response.session_id,
        "club_type",
        "sports",
    )

    assert response.accepted_answer.normalized_value == "sports"
    assert response.current_question.field_key == "founded_year"
    assert response.progress.answered_required == 1


def test_handle_starts_assessment(orchestrator):
    response = orchestrator.handle(StartAssessmentInput(locale="de"))

    assert response.status == "in_progress"
    assert response.current_question.field_key == "club_type"
    assert response.progress.required_total == 5


def test_handle_submits_structured_answer(orchestrator):
    response = orchestrator.start()

    response = orchestrator.handle(
        SubmitAnswerInput(
            session_id=response.session_id,
            question_id="club_type",
            value="sports",
        )
    )

    assert response.accepted_answer.normalized_value == "sports"
    assert response.current_question.field_key == "founded_year"


def test_handle_submits_text_answer(tmp_path):
    interpreter = FakeInterpreter(
        InterpretationResult(
            decision="valid",
            normalized_value="sports",
            confidence=0.9,
            reason_code=None,
            follow_up=None,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        interpreter=interpreter,
    )
    response = orchestrator.start()

    response = orchestrator.handle(
        SubmitTextAnswerInput(
            session_id=response.session_id,
            question_id="club_type",
            text="ein Einfamilienhaus",
            endpoint="http://fake",
            model="fake",
        )
    )

    assert response.accepted_answer.raw_answer == "ein Einfamilienhaus"
    assert response.accepted_answer.confidence == 0.9


def test_handle_revises_answer(orchestrator):
    response = _reach_review(orchestrator)

    response = orchestrator.handle(
        ReviseAnswerInput(
            session_id=response.session_id,
            question_id="members_count",
            value=145,
        )
    )

    assert response.status == "review"
    assert response.answers["members_count"].normalized_value == 145


def test_handle_completes_assessment(orchestrator):
    response = _reach_review(orchestrator)

    response = orchestrator.handle(
        CompleteAssessmentInput(session_id=response.session_id)
    )

    assert response.status == "completed"
    assert response.result.recommendations
    assert "score" not in response.result.model_dump()


def test_handle_question_help(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Damit ist der Gebäudetyp gemeint.",
            proposes_answer=False,
            normalized_value=None,
            confidence=0.0,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()

    response = orchestrator.handle(
        QuestionHelpInput(
            session_id=response.session_id,
            question_id="club_type",
            text="Was bedeutet das?",
            endpoint="http://fake",
            model="fake",
        )
    )

    assert response.answers == {}
    assert response.chat_messages[-1].content == "Damit ist der Gebäudetyp gemeint."


def test_handle_accepts_proposed_answer(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Das klingt nach einem Einfamilienhaus.",
            proposes_answer=True,
            normalized_value="sports",
            confidence=0.82,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()
    proposed = orchestrator.ask_question_help(
        response.session_id,
        "club_type",
        "Es ist ein Einfamilienhaus.",
        "http://fake",
        "fake",
    )

    response = orchestrator.handle(
        AcceptProposedAnswerInput(
            session_id=proposed.session_id,
            message_id=proposed.chat_messages[-1].message_id,
        )
    )

    assert response.accepted_answer.normalized_value == "sports"
    assert response.current_question.field_key == "founded_year"


def test_handle_discards_proposed_answer(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Das klingt nach einem Einfamilienhaus.",
            proposes_answer=True,
            normalized_value="sports",
            confidence=0.82,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()
    proposed = orchestrator.ask_question_help(
        response.session_id,
        "club_type",
        "Es ist ein Einfamilienhaus.",
        "http://fake",
        "fake",
    )

    response = orchestrator.handle(
        DiscardProposedAnswerInput(
            session_id=proposed.session_id,
            message_id=proposed.chat_messages[-1].message_id,
        )
    )

    assert response.chat_messages[-1].proposed_answer is None
    assert response.current_question.field_key == "club_type"


def test_handle_contract_starts_assessment(orchestrator):
    response = orchestrator.handle_contract(StartAssessmentInput(locale="de"))

    assert response.ok is True
    assert response.status == "in_progress"
    assert response.current_question.field_key == "club_type"
    assert response.progress.required_total == 5
    assert response.errors == []


def test_handle_contract_missing_session_returns_error(orchestrator):
    response = orchestrator.handle_contract(
        CompleteAssessmentInput(session_id="sess_missing")
    )

    assert response.ok is False
    assert response.session_id == "sess_missing"
    assert response.errors[0].code == "session_not_found"


def test_handle_contract_wrong_question_returns_error(orchestrator):
    started = orchestrator.start()

    response = orchestrator.handle_contract(
        SubmitAnswerInput(
            session_id=started.session_id,
            question_id="members_count",
            value=120,
        )
    )

    assert response.ok is False
    assert response.session_id == started.session_id
    assert response.errors[0].code == "invalid_question"


def test_handle_contract_complete_before_review_returns_error(orchestrator):
    started = orchestrator.start()

    response = orchestrator.handle_contract(
        CompleteAssessmentInput(session_id=started.session_id)
    )

    assert response.ok is False
    assert response.session_id == started.session_id
    assert response.errors[0].code == "invalid_state"


def test_handle_contract_question_help_in_review_returns_invalid_state(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Damit ist der Gebäudetyp gemeint.",
            proposes_answer=False,
            normalized_value=None,
            confidence=0.0,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = _reach_review(orchestrator)

    response = orchestrator.handle_contract(
        QuestionHelpInput(
            session_id=response.session_id,
            question_id="club_type",
            text="Was bedeutet das?",
            endpoint="http://fake",
            model="fake",
        )
    )

    assert response.ok is False
    assert response.errors[0].code == "invalid_state"


def test_handle_contract_uncertain_text_does_not_mutate_session(tmp_path):
    interpreter = FakeInterpreter(
        InterpretationResult(
            decision="uncertain",
            normalized_value=None,
            confidence=0.4,
            reason_code="AMBIGUOUS",
            follow_up="Meinst du ein Einfamilienhaus?",
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        interpreter=interpreter,
    )
    started = orchestrator.start()

    response = orchestrator.handle_contract(
        SubmitTextAnswerInput(
            session_id=started.session_id,
            question_id="club_type",
            text="ein Haus",
            endpoint="http://fake",
            model="fake",
        )
    )

    assert response.ok is False
    assert response.errors[0].code == "interpretation_uncertain"
    assert response.validation_errors[0].code == "uncertain"
    assert orchestrator.get(started.session_id).answers == {}


def test_handle_contract_question_help_proposal_does_not_mutate_answers(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Das klingt nach einem Einfamilienhaus.",
            proposes_answer=True,
            normalized_value="sports",
            confidence=0.82,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    started = orchestrator.start()

    response = orchestrator.handle_contract(
        QuestionHelpInput(
            session_id=started.session_id,
            question_id="club_type",
            text="Es ist ein Einfamilienhaus.",
            endpoint="http://fake",
            model="fake",
        )
    )

    assert response.ok is True
    assert response.answers == {}
    assert response.chat_messages[-1].proposed_answer.normalized_value == (
        "sports"
    )
    assert orchestrator.get(started.session_id).answers == {}


def test_submit_rejects_invalid_value_without_mutating_session(orchestrator):
    response = orchestrator.start()

    invalid = orchestrator.submit(
        response.session_id,
        "club_type",
        "castle",
    )

    assert invalid.accepted_answer is None
    assert invalid.current_question.field_key == "club_type"
    assert invalid.validation_errors[0].code == "invalid_option"
    assert orchestrator.get(response.session_id).progress.answered_required == 0


def test_submit_rejects_answer_for_non_current_question(orchestrator):
    response = orchestrator.start()

    with pytest.raises(AssessmentWorkflowError, match="aktuelle"):
        orchestrator.submit(response.session_id, "members_count", 120)


def test_required_unknown_and_optional_skip_reach_review(orchestrator):
    response = orchestrator.start()
    answers = [
        ("club_type", "sports"),
        ("founded_year", None),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", None),
        ("annual_budget_eur", None),
    ]

    for question_id, value in answers:
        response = orchestrator.submit(response.session_id, question_id, value)

    assert response.status == "review"
    assert response.current_question is None
    assert response.progress.answered_required == 5


def test_revise_revalidates_answer_and_returns_review(orchestrator):
    response = _reach_review(orchestrator)

    response = orchestrator.revise(
        response.session_id,
        "members_count",
        145,
    )

    assert response.status == "review"
    assert response.answers["members_count"].normalized_value == 145


def test_complete_requires_review_state(orchestrator):
    response = orchestrator.start()

    with pytest.raises(AssessmentWorkflowError, match="Review"):
        orchestrator.complete(response.session_id)


def test_complete_marks_session_completed(orchestrator):
    response = _reach_review(orchestrator)

    completed = orchestrator.complete(response.session_id)

    assert completed.status == "completed"
    assert completed.current_question is None
    assert completed.result.recommendations
    assert completed.report.format == "markdown"


def test_conditional_required_question_does_not_block_review(tmp_path):
    orchestrator = _conditional_orchestrator(tmp_path)
    response = orchestrator.start()

    response = orchestrator.submit(response.session_id, "club_type", "social")

    assert response.status == "review"
    assert response.current_question is None
    assert response.progress.answered_required == 1
    assert response.progress.required_total == 1


def test_revise_removes_answers_that_become_invisible(tmp_path):
    orchestrator = _conditional_orchestrator(tmp_path)
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "club_type", "house")
    response = orchestrator.submit(response.session_id, "funding_source", "public_grants")
    assert response.status == "review"

    response = orchestrator.revise(response.session_id, "club_type", "social")

    assert response.status == "review"
    assert response.current_question is None
    assert "funding_source" not in response.answers
    assert response.progress.answered_required == 1
    assert response.progress.required_total == 1


def test_revise_returns_to_in_progress_when_new_question_becomes_visible(tmp_path):
    orchestrator = _conditional_orchestrator(tmp_path)
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "club_type", "social")
    assert response.status == "review"

    response = orchestrator.revise(response.session_id, "club_type", "house")

    assert response.status == "in_progress"
    assert response.current_question.field_key == "funding_source"
    assert response.progress.answered_required == 1
    assert response.progress.required_total == 2


class FakeInterpreter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def interpret(self, endpoint, model, question, text):
        if self.error:
            raise self.error
        return self.result


class FakeChatAssistant:
    def __init__(self, help_result=None, error=None):
        self.help_result = help_result
        self.error = error
        self.question_chat_text = None

    def ask_question_help(self, endpoint, model, pack, session, question, text):
        return self.help_result

    def respond_to_question(self, endpoint, model, pack, session, question, text):
        self.question_chat_text = text
        if self.error:
            raise self.error
        return self.help_result



def test_submit_text_accepts_valid_interpretation(tmp_path):
    interpreter = FakeInterpreter(
        InterpretationResult(
            decision="valid",
            normalized_value="sports",
            confidence=0.9,
            reason_code=None,
            follow_up=None,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        interpreter=interpreter,
    )
    response = orchestrator.start()

    response = orchestrator.submit_text(
        response.session_id,
        "club_type",
        "ein Einfamilienhaus",
        "http://fake",
        "fake",
    )

    assert response.accepted_answer.raw_answer == "ein Einfamilienhaus"
    assert response.accepted_answer.confidence == 0.9


def test_submit_text_uncertain_does_not_mutate_session(tmp_path):
    interpreter = FakeInterpreter(
        InterpretationResult(
            decision="uncertain",
            normalized_value=None,
            confidence=0.4,
            reason_code="AMBIGUOUS",
            follow_up="Meinst du ein Einfamilienhaus?",
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        interpreter=interpreter,
    )
    response = orchestrator.start()

    uncertain = orchestrator.submit_text(
        response.session_id,
        "club_type",
        "ein Haus",
        "http://fake",
        "fake",
    )

    assert uncertain.validation_errors[0].code == "uncertain"
    assert uncertain.assistant_message == "Meinst du ein Einfamilienhaus?"
    assert orchestrator.get(response.session_id).answers == {}


def test_question_help_explains_without_mutating_answers(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Damit ist der Gebäudetyp gemeint.",
            proposes_answer=False,
            normalized_value=None,
            confidence=0.0,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()

    updated = orchestrator.ask_question_help(
        response.session_id,
        "club_type",
        "Was bedeutet das?",
        "http://fake",
        "fake",
    )

    assert updated.answers == {}
    assert [message.role for message in updated.chat_messages] == [
        "user",
        "assistant",
    ]
    assert updated.chat_messages[-1].proposed_answer is None


def test_question_help_proposal_requires_explicit_confirmation(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Das klingt nach einem Einfamilienhaus.",
            proposes_answer=True,
            normalized_value="sports",
            confidence=0.82,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()

    proposed = orchestrator.ask_question_help(
        response.session_id,
        "club_type",
        "Es ist ein Einfamilienhaus.",
        "http://fake",
        "fake",
    )

    assert proposed.answers == {}
    message_id = proposed.chat_messages[-1].message_id
    assert proposed.chat_messages[-1].proposed_answer.normalized_value == (
        "sports"
    )

    accepted = orchestrator.accept_proposed_answer(
        proposed.session_id,
        message_id,
    )

    assert accepted.accepted_answer.normalized_value == "sports"
    assert accepted.accepted_answer.raw_answer == "Es ist ein Einfamilienhaus."
    assert accepted.accepted_answer.confidence == 0.82


def test_question_help_invalid_proposal_returns_validation_feedback(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Ich würde Schloss vorschlagen.",
            proposes_answer=True,
            normalized_value="castle",
            confidence=0.9,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = orchestrator.start()

    updated = orchestrator.ask_question_help(
        response.session_id,
        "club_type",
        "Ein Schloss.",
        "http://fake",
        "fake",
    )

    assert updated.validation_errors[0].code == "invalid_option"
    assert updated.answers == {}
    assert updated.chat_messages[-1].content == "Ich würde Schloss vorschlagen."
    assert updated.chat_messages[-1].proposed_answer is None


def test_question_help_only_allowed_in_progress(tmp_path):
    chat_assistant = FakeChatAssistant(
        QuestionHelpResult(
            message="Antwort",
            proposes_answer=False,
            normalized_value=None,
            confidence=0.0,
        )
    )
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=chat_assistant,
    )
    response = _reach_review(orchestrator)

    with pytest.raises(AssessmentWorkflowError, match="laufenden"):
        orchestrator.ask_question_help(
            response.session_id,
            "club_type",
            "Warum?",
            "http://fake",
            "fake",
        )


def _chat_orchestrator(tmp_path, help_result=None, error=None):
    return AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=FakeChatAssistant(help_result=help_result, error=error),
    )


def _proposal(normalized_value, message="Alles klar.", proposes_answer=True):
    return QuestionHelpResult(
        message=message,
        proposes_answer=proposes_answer,
        normalized_value=normalized_value,
        confidence=0.9,
    )


def test_question_chat_accepts_valid_answer_and_advances(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal("sports"))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "Es ist ein Einfamilienhaus",
        "http://fake",
        "fake",
    )

    assert updated.accepted_answer.normalized_value == "sports"
    assert updated.accepted_answer.raw_answer == "Es ist ein Einfamilienhaus"
    assert updated.accepted_answer.confidence == 0.9
    assert updated.current_question.field_key == "founded_year"
    assert updated.assistant_message == updated.current_question.text
    assert updated.validation_errors == []
    assert [message.role for message in updated.chat_messages] == ["user"]
    assert all(
        message.scope == "question_help" and message.question_id == "club_type"
        for message in updated.chat_messages
    )
    assert updated.chat_messages[-1].content == "Es ist ein Einfamilienhaus"
    assert updated.chat_messages[-1].proposed_answer is None


def test_question_chat_accepted_reply_ignores_llm_wording(tmp_path):
    orchestrator = _chat_orchestrator(
        tmp_path, _proposal("sports", "Meinst du ein Einfamilienhaus?")
    )
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "Sportverein",
        "http://fake",
        "fake",
    )

    assert updated.accepted_answer.normalized_value == "sports"
    assert [message.role for message in updated.chat_messages] == ["user"]
    assert "Meinst du" not in " ".join(m.content for m in updated.chat_messages)


def test_get_orders_answers_by_pack_question_order(tmp_path):
    repository = LocalAssessmentRepository(tmp_path / "assessments.json")
    orchestrator = AssessmentOrchestrator(load_domain_pack(), repository)
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "club_type", "social")
    response = orchestrator.submit(response.session_id, "founded_year", 1990)
    response = orchestrator.submit(response.session_id, "members_count", 80)
    session = repository.get(response.session_id)
    session.answers = dict(reversed(list(session.answers.items())))
    repository.save(session)

    loaded = orchestrator.get(response.session_id)

    assert list(loaded.answers) == ["club_type", "founded_year", "members_count"]


def test_question_chat_reply_does_not_mutate_answers(tmp_path):
    orchestrator = _chat_orchestrator(
        tmp_path,
        _proposal(None, "Damit ist der Gebäudetyp gemeint.", proposes_answer=False),
    )
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "Was bedeutet das?",
        "http://fake",
        "fake",
    )

    assert updated.answers == {}
    assert updated.current_question.field_key == "club_type"
    assert updated.validation_errors == []
    assert updated.assistant_message == "Damit ist der Gebäudetyp gemeint."
    reloaded = orchestrator.get(response.session_id)
    assert [message.content for message in reloaded.chat_messages] == [
        "Was bedeutet das?",
        "Damit ist der Gebäudetyp gemeint.",
    ]


def test_question_chat_rejects_invalid_value_deterministically(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal("castle"))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "Ein Schloss.",
        "http://fake",
        "fake",
    )

    assert updated.answers == {}
    assert updated.validation_errors == []
    assert updated.chat_messages[-1].role == "assistant"
    assert updated.chat_messages[-1].content == (
        "Die ausgewählte Option ist nicht gültig."
    )
    assert updated.assistant_message == "Die ausgewählte Option ist nicht gültig."


def test_question_chat_rejects_out_of_range_number(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal(1500))
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "club_type", "social")

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "founded_year",
        "1500",
        "http://fake",
        "fake",
    )

    assert list(updated.answers) == ["club_type"]
    assert updated.chat_messages[-1].content == (
        "Der Wert muss mindestens 1800 betragen."
    )


def test_question_chat_stores_unknown_when_allowed(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal(None))
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "club_type", "social")

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "founded_year",
        "weiß ich nicht",
        "http://fake",
        "fake",
    )

    assert updated.accepted_answer.normalized_value is None
    assert updated.current_question.field_key == "members_count"
    assert [message.role for message in updated.chat_messages] == ["user"]


def test_question_chat_requires_value_when_unknown_not_allowed(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal(None))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "weiß ich nicht",
        "http://fake",
        "fake",
    )

    assert updated.answers == {}
    assert updated.chat_messages[-1].content == "Diese Angabe ist erforderlich."


def test_question_chat_rejects_empty_text(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal("sports"))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "   ",
        "http://fake",
        "fake",
    )

    assert updated.validation_errors[0].code == "required"
    assert updated.chat_messages == []


def test_question_chat_reports_provider_failure_without_persisting(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, error=ProviderError("down"))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id,
        "club_type",
        "Haus",
        "http://fake",
        "fake",
    )

    assert updated.validation_errors[0].code == "chat_failed"
    assert updated.validation_errors[0].field_key == "__chat__"
    assert orchestrator.get(response.session_id).chat_messages == []


def test_question_chat_requires_in_progress_session(tmp_path):
    orchestrator = _chat_orchestrator(tmp_path, _proposal("sports"))
    response = _reach_review(orchestrator)

    with pytest.raises(AssessmentWorkflowError) as excinfo:
        orchestrator.submit_question_chat(
            response.session_id,
            "club_type",
            "Haus",
            "http://fake",
            "fake",
        )

    assert excinfo.value.code == "invalid_state"


def test_question_chat_requires_chat_assistant(tmp_path):
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )
    response = orchestrator.start()

    with pytest.raises(AssessmentWorkflowError) as excinfo:
        orchestrator.submit_question_chat(
            response.session_id,
            "club_type",
            "Haus",
            "http://fake",
            "fake",
        )

    assert excinfo.value.code == "llm_unavailable"


def _reach_review(orchestrator):
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
    return response


def _conditional_orchestrator(tmp_path):
    return AssessmentOrchestrator(
        _conditional_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )


def _conditional_pack():
    return DomainPack.model_validate(
        {
            "domain_id": "demo",
            "version": "1.0.0",
            "name": "Demo",
            "description": "Demo",
            "default_locale": "de",
            "content_hash": "a" * 64,
            "questions": [
                {
                    "question_id": "club_type",
                    "field_key": "club_type",
                    "text": "Gebäudetyp?",
                    "field_type": "select",
                    "required": True,
                    "options": [
                        {"value": "house", "label": "Haus"},
                        {"value": "social", "label": "Sozial- oder Hilfsverein"},
                    ],
                },
                {
                    "question_id": "funding_source",
                    "field_key": "funding_source",
                    "text": "Heizung?",
                    "field_type": "select",
                    "required": True,
                    "visible_if": {
                        "field_key": "club_type",
                        "operator": "equals",
                        "value": "house",
                    },
                    "options": [{"value": "public_grants", "label": "Öffentliche Förderungen"}],
                },
            ],
            "rules": [],
            "recommendations": [],
        }
    )


def test_revise_multiselect_removes_dependent_answer(tmp_path):
    orchestrator = _typed_orchestrator(tmp_path)
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "channels", ["website", "email"])
    assert response.current_question.field_key == "online_share"
    response = orchestrator.submit(response.session_id, "online_share", 40)
    response = orchestrator.submit(response.session_id, "has_website", "nein")
    response = orchestrator.submit(response.session_id, "founded_on", "12.03.2024")
    assert response.status == "review"
    assert response.answers["has_website"].normalized_value is False
    assert response.answers["founded_on"].normalized_value == "2024-03-12"
    assert response.answers["channels"].raw_answer == "website, email"
    assert response.answers["has_website"].raw_answer == "nein"

    response = orchestrator.revise(response.session_id, "channels", ["email"])

    assert response.status == "review"
    assert "online_share" not in response.answers
    assert response.answers["channels"].normalized_value == ["email"]


def test_raw_value_renders_lists_and_booleans():
    from guideos.assessment.orchestrator import _raw_value

    assert _raw_value(["a", "b"]) == "a, b"
    assert _raw_value(True) == "true"
    assert _raw_value(False) == "false"
    assert _raw_value(None) == "unbekannt"
    assert _raw_value(12) == "12"


def _typed_orchestrator(tmp_path):
    pack = DomainPack.model_validate(
        {
            "domain_id": "typed",
            "version": "1.0.0",
            "name": "Typen",
            "description": "Demo",
            "default_locale": "de",
            "content_hash": "a" * 64,
            "questions": [
                {
                    "question_id": "channels",
                    "field_key": "channels",
                    "text": "Kanäle?",
                    "field_type": "multiselect",
                    "required": True,
                    "options": [
                        {"value": "email", "label": "E-Mail"},
                        {"value": "website", "label": "Website"},
                    ],
                },
                {
                    "question_id": "online_share",
                    "field_key": "online_share",
                    "text": "Online-Anteil?",
                    "field_type": "number",
                    "required": True,
                    "visible_if": {
                        "field_key": "channels",
                        "operator": "contains",
                        "value": "website",
                    },
                },
                {
                    "question_id": "has_website",
                    "field_key": "has_website",
                    "text": "Website?",
                    "field_type": "boolean",
                    "required": True,
                },
                {
                    "question_id": "founded_on",
                    "field_key": "founded_on",
                    "text": "Gründung?",
                    "field_type": "date",
                    "required": True,
                },
            ],
            "rules": [],
            "recommendations": [],
        }
    )
    return AssessmentOrchestrator(
        pack, LocalAssessmentRepository(tmp_path / "assessments.json")
    )


def _typed_chat_orchestrator(tmp_path, help_result=None):
    orchestrator = _typed_orchestrator(tmp_path)
    orchestrator.chat_assistant = FakeChatAssistant(help_result=help_result)
    return orchestrator


def _text_pack_orchestrator(tmp_path, *, allow_unknown, help_result=None):
    pack = DomainPack.model_validate(
        {
            "domain_id": "texty",
            "version": "1.0.0",
            "name": "Text",
            "description": "Demo",
            "default_locale": "de",
            "content_hash": "a" * 64,
            "questions": [
                {
                    "question_id": "company_name",
                    "field_key": "company_name",
                    "text": "Name?",
                    "field_type": "text",
                    "required": True,
                    "allow_unknown": allow_unknown,
                    "validation": {"min_length": 2},
                },
                {
                    "question_id": "employees",
                    "field_key": "employees",
                    "text": "Beschäftigte?",
                    "field_type": "integer",
                    "required": True,
                    "validation": {"minimum": 0, "maximum": 100},
                },
            ],
            "rules": [],
            "recommendations": [],
        }
    )
    return AssessmentOrchestrator(
        pack,
        LocalAssessmentRepository(tmp_path / "assessments.json"),
        chat_assistant=FakeChatAssistant(help_result=help_result),
    )


def test_question_chat_stores_text_answer_without_llm(tmp_path):
    orchestrator = _text_pack_orchestrator(tmp_path, allow_unknown=False)
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id, "company_name", " DCFDW ", "http://fake", "fake"
    )

    assert updated.accepted_answer.normalized_value == "DCFDW"
    assert updated.accepted_answer.confidence == 1.0
    assert updated.current_question.field_key == "employees"
    assert orchestrator.chat_assistant.question_chat_text is None
    assert [m.role for m in updated.chat_messages] == ["user"]


def test_question_chat_stores_number_and_reports_range_without_llm(tmp_path):
    orchestrator = _text_pack_orchestrator(tmp_path, allow_unknown=False)
    response = orchestrator.start()
    orchestrator.submit(response.session_id, "company_name", "Bäckerei")

    rejected = orchestrator.submit_question_chat(
        response.session_id, "employees", "500", "http://fake", "fake"
    )
    assert "employees" not in rejected.answers
    assert rejected.chat_messages[-1].role == "assistant"
    assert "höchstens 100" in rejected.chat_messages[-1].content
    assert orchestrator.chat_assistant.question_chat_text is None

    accepted = orchestrator.submit_question_chat(
        response.session_id, "employees", "1", "http://fake", "fake"
    )
    assert accepted.accepted_answer.normalized_value == 1
    assert accepted.status == "review"
    assert orchestrator.chat_assistant.question_chat_text is None


def test_question_chat_sends_questions_and_unparseable_input_to_llm(tmp_path):
    orchestrator = _text_pack_orchestrator(
        tmp_path, allow_unknown=False,
        help_result=_proposal(None, "Gemeint ist der Firmenname.", proposes_answer=False),
    )
    response = orchestrator.start()

    reply = orchestrator.submit_question_chat(
        response.session_id, "company_name", "Was ist gemeint?", "http://fake", "fake"
    )
    assert orchestrator.chat_assistant.question_chat_text == "Was ist gemeint?"
    assert reply.chat_messages[-1].content == "Gemeint ist der Firmenname."
    assert "company_name" not in reply.answers

    orchestrator.submit(response.session_id, "company_name", "Bäckerei")
    orchestrator.chat_assistant.help_result = _proposal(3)
    accepted = orchestrator.submit_question_chat(
        response.session_id, "employees", "wir sind zu dritt", "http://fake", "fake"
    )
    assert orchestrator.chat_assistant.question_chat_text == "wir sind zu dritt"
    assert accepted.accepted_answer.normalized_value == 3


def test_question_chat_treats_unknown_phrases_deterministically(tmp_path):
    orchestrator = _text_pack_orchestrator(tmp_path, allow_unknown=True)
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id, "company_name", "Weiß ich nicht.", "http://fake", "fake"
    )
    assert updated.accepted_answer.normalized_value is None
    assert orchestrator.chat_assistant.question_chat_text is None

    strict = _text_pack_orchestrator(tmp_path / "b", allow_unknown=False)
    response = strict.start()
    rejected = strict.submit_question_chat(
        response.session_id, "company_name", "unbekannt", "http://fake", "fake"
    )
    assert "company_name" not in rejected.answers
    assert rejected.chat_messages[-1].content == "Diese Angabe ist erforderlich."
    assert strict.chat_assistant.question_chat_text is None


def test_question_chat_maps_option_labels_and_dates_without_llm(tmp_path):
    orchestrator = _typed_chat_orchestrator(tmp_path)
    response = orchestrator.start()

    channels = orchestrator.submit_question_chat(
        response.session_id, "channels", "e-mail, Website", "http://fake", "fake"
    )
    assert channels.accepted_answer.normalized_value == ["email", "website"]
    assert orchestrator.chat_assistant.question_chat_text is None

    orchestrator.submit(response.session_id, "online_share", 10)
    website = orchestrator.submit_question_chat(
        response.session_id, "has_website", "Nein", "http://fake", "fake"
    )
    assert website.accepted_answer.normalized_value is False

    founded = orchestrator.submit_question_chat(
        response.session_id, "founded_on", "12.3.1998", "http://fake", "fake"
    )
    assert founded.accepted_answer.normalized_value == "1998-03-12"
    assert orchestrator.chat_assistant.question_chat_text is None


def test_question_chat_asks_llm_when_choice_text_matches_no_option(tmp_path):
    orchestrator = _typed_chat_orchestrator(tmp_path, help_result=_proposal(["email"]))
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id, "channels", "Die Leute schreiben mir meistens", "http://fake", "fake"
    )

    assert orchestrator.chat_assistant.question_chat_text == "Die Leute schreiben mir meistens"
    assert updated.accepted_answer.normalized_value == ["email"]


@pytest.mark.parametrize(
    "text",
    ["welche Branchen gibt es?", "Welche Optionen habe ich", "was kann ich auswählen?",
     "Welche Möglichkeiten gibt es", "Was steht zur Auswahl?"],
)
def test_question_chat_lists_options_deterministically(tmp_path, text):
    orchestrator = _typed_chat_orchestrator(tmp_path)
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id, "channels", text, "http://fake", "fake"
    )

    assert orchestrator.chat_assistant.question_chat_text is None
    assert "channels" not in updated.answers
    reply = updated.chat_messages[-1]
    assert reply.role == "assistant"
    assert reply.content == "Mögliche Antworten: E-Mail, Website."


def test_question_chat_sends_other_help_questions_about_options_to_llm(tmp_path):
    orchestrator = _typed_chat_orchestrator(
        tmp_path, help_result=_proposal(None, "E-Mail passt.", proposes_answer=False)
    )
    response = orchestrator.start()

    updated = orchestrator.submit_question_chat(
        response.session_id, "channels", "Welche Option passt für Newsletter?", "http://fake", "fake"
    )

    assert orchestrator.chat_assistant.question_chat_text == "Welche Option passt für Newsletter?"
    assert updated.chat_messages[-1].content == "E-Mail passt."
