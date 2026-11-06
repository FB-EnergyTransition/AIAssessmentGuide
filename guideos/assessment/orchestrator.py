import logging
from datetime import UTC, datetime
from typing import Any

from guideos.assessment.contract import (
    AcceptProposedAnswerInput,
    AssessmentContractError,
    AssessmentErrorCode,
    AssessmentContractResponse,
    AssessmentInput,
    CompleteAssessmentInput,
    DiscardProposedAnswerInput,
    QuestionHelpInput,
    ReviseAnswerInput,
    StartAssessmentInput,
    SubmitAnswerInput,
    SubmitTextAnswerInput,
    to_contract_response,
)
from guideos.assessment.context_chat import (
    AssessmentChatError,
    LLMAssessmentChatAssistant,
    QuestionHelpResult,
)
from guideos.assessment.interpreter import (
    AnswerInterpretationError,
    LLMAnswerInterpreter,
)
from guideos.assessment.models import (
    MEASUREMENT_RAW_ANSWER,
    AssessmentAnswer,
    AssessmentChatMessage,
    AssessmentEvent,
    AssessmentProgress,
    AssessmentResponse,
    AssessmentSession,
    AssessmentValidationIssue,
    DerivedProposal,
    KnowledgeGeneration,
    ProposedAssessmentAnswer,
)
from guideos.assessment.repository import (
    AssessmentConflictError,
    AssessmentRepository,
)
from guideos.domains.models import (
    DerivationDefinition,
    DomainPack,
    QuestionDefinition,
    advisory_file_question,
)
from guideos.profiles.models import Profile
from guideos.profiles.repository import ProfileRepository
from guideos.providers.base import ProviderError
from guideos.questionnaire.engine import get_next_question, is_question_visible
from guideos.recommendations.llm import PROMPT_VERSION
from guideos.recommendations.service import (
    KnowledgeRecommender,
    RecommendationServiceError,
)
from guideos.reports.renderer import render_markdown_report
from guideos.rules.engine import evaluate_assessment
from guideos.tools.datasets import UNAVAILABLE_CODES, DatasetImporter, format_options, step_arguments
from guideos.tools.models import DatasetRef, ToolError
from guideos.tools.pointers import compare, numeric, resolve_pointer
from guideos.validation.engine import AnswerValidationError, validate_answer


logger = logging.getLogger(__name__)


class AssessmentWorkflowError(Exception):
    def __init__(
        self,
        message: str,
        code: AssessmentErrorCode = "invalid_input",
    ):
        super().__init__(message)
        self.code = code


class AssessmentOrchestrator:
    def __init__(
        self,
        pack: DomainPack,
        repository: AssessmentRepository,
        interpreter: LLMAnswerInterpreter | None = None,
        chat_assistant: LLMAssessmentChatAssistant | None = None,
        knowledge_recommender: KnowledgeRecommender | None = None,
        profiles: ProfileRepository | None = None,
        dataset_importer: DatasetImporter | None = None,
    ):
        self.pack = pack
        self.repository = repository
        self.interpreter = interpreter
        self.chat_assistant = chat_assistant
        self.knowledge_recommender = knowledge_recommender
        self.profiles = profiles
        # File questions: GuideOS imports the upload through the pack's dataset
        # tool when the answer is accepted and keeps only the reference.
        self.dataset_importer = dataset_importer

    def handle(self, input: AssessmentInput) -> AssessmentResponse:
        if isinstance(input, StartAssessmentInput):
            return self.start(locale=input.locale)
        if isinstance(input, SubmitAnswerInput):
            return self.submit(
                input.session_id,
                input.question_id,
                input.value,
                raw_answer=input.raw_answer,
                confidence=input.confidence,
            )
        if isinstance(input, SubmitTextAnswerInput):
            return self.submit_text(
                input.session_id,
                input.question_id,
                input.text,
                input.endpoint,
                input.model,
            )
        if isinstance(input, ReviseAnswerInput):
            return self.revise(
                input.session_id,
                input.question_id,
                input.value,
                raw_answer=input.raw_answer,
                confidence=input.confidence,
            )
        if isinstance(input, CompleteAssessmentInput):
            return self.complete(input.session_id, input.endpoint, input.model)
        if isinstance(input, QuestionHelpInput):
            return self.ask_question_help(
                input.session_id,
                input.question_id,
                input.text,
                input.endpoint,
                input.model,
            )
        if isinstance(input, AcceptProposedAnswerInput):
            return self.accept_proposed_answer(input.session_id, input.message_id)
        if isinstance(input, DiscardProposedAnswerInput):
            return self.discard_proposed_answer(input.session_id, input.message_id)
        raise AssessmentWorkflowError("Unbekannter Assessment-Input.")

    def handle_contract(
        self,
        input: AssessmentInput,
    ) -> AssessmentContractResponse:
        try:
            return to_contract_response(self.handle(input))
        except LookupError as exc:
            return _contract_error_response(
                input,
                "session_not_found",
                str(exc),
            )
        except ProviderError as exc:
            return _contract_error_response(
                input,
                "provider_error",
                str(exc),
            )
        except AssessmentConflictError as exc:
            return _contract_error_response(
                input,
                "session_conflict",
                str(exc),
            )
        except AssessmentWorkflowError as exc:
            return _contract_error_response(
                input,
                exc.code,
                str(exc),
            )

    def start(self, locale: str | None = None) -> AssessmentResponse:
        session = AssessmentSession.create(
            domain_id=self.pack.domain_id,
            domain_version=self.pack.version,
            domain_content_hash=self.pack.content_hash,
            locale=locale or self.pack.default_locale,
        )
        next_question = get_next_question(self.pack, session)
        session.current_question_id = (
            next_question.question_id if next_question else None
        )
        self.repository.save(session)
        logger.info(
            "session_started session_id=%s domain_id=%s domain_version=%s",
            session.session_id,
            session.domain_id,
            session.domain_version,
        )
        return self._response(session)

    def get(self, session_id: str) -> AssessmentResponse:
        return self._response(self._load_session(session_id))

    def submit(
        self,
        session_id: str,
        question_id: str,
        value: Any,
        *,
        raw_answer: str | None = None,
        confidence: float = 1.0,
    ) -> AssessmentResponse:
        session = self._load_session(session_id)
        if session.status != "in_progress":
            raise AssessmentWorkflowError(
                "Antworten können nur während eines laufenden Assessments "
                "eingereicht werden.",
                code="invalid_state",
            )
        question = self._get_question(question_id)
        if question.question_id != session.current_question_id:
            raise AssessmentWorkflowError(
                "Die Antwort gehört nicht zur aktuellen Frage.",
                code="invalid_question",
            )
        return self._accept(session, question, value, raw_answer, confidence)

    def revise(
        self,
        session_id: str,
        question_id: str,
        value: Any,
        *,
        raw_answer: str | None = None,
        confidence: float = 1.0,
    ) -> AssessmentResponse:
        session = self._load_session(session_id)
        if session.status != "review":
            raise AssessmentWorkflowError(
                "Antworten können nur im Review geändert werden.",
                code="invalid_state",
            )
        question = self._get_question(question_id)
        if question.field_key not in session.answers:
            raise AssessmentWorkflowError(
                "Nur bereits beantwortete Fragen können geändert werden.",
                code="invalid_state",
            )
        return self._accept(
            session,
            question,
            value,
            raw_answer,
            confidence,
            keep_review=True,
        )

    def submit_text(
        self,
        session_id: str,
        question_id: str,
        text: str,
        endpoint: str,
        model: str,
    ) -> AssessmentResponse:
        if self.interpreter is None:
            raise AssessmentWorkflowError(
                "Für Freitextantworten ist kein LLM-Interpreter konfiguriert.",
                code="llm_unavailable",
            )
        session = self._load_session(session_id)
        if session.status != "in_progress":
            raise AssessmentWorkflowError(
                "Freitext kann nur während eines laufenden Assessments "
                "eingereicht werden.",
                code="invalid_state",
            )
        question = self._get_question(question_id)
        if question.question_id != session.current_question_id:
            raise AssessmentWorkflowError(
                "Die Antwort gehört nicht zur aktuellen Frage.",
                code="invalid_question",
            )
        try:
            interpretation = self.interpreter.interpret(
                endpoint,
                model,
                question,
                text,
            )
        except (ProviderError, AnswerInterpretationError) as exc:
            issue = AssessmentValidationIssue(
                field_key=question.field_key,
                code="interpretation_failed",
                message=str(exc),
            )
            return self._response(session, validation_errors=[issue])

        if interpretation.decision != "valid":
            issue = AssessmentValidationIssue(
                field_key=question.field_key,
                code=interpretation.decision,
                message=interpretation.follow_up
                or "Die Antwort konnte nicht eindeutig übernommen werden.",
            )
            return self._response(
                session,
                validation_errors=[issue],
                assistant_message=issue.message,
            )
        return self.submit(
            session_id,
            question_id,
            interpretation.normalized_value,
            raw_answer=text,
            confidence=interpretation.confidence,
        )

    def complete(
        self,
        session_id: str,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> AssessmentResponse:
        """Evaluate the rules, then, with a model given and a recommender
        configured, generate the first knowledge recommendations. Their
        failure never blocks completion: the result records the error code
        and the result chat offers to retry. With a profile repository the
        participant's profile of the domain is created or replaced."""
        session = self._load_session(session_id)
        if session.status != "review":
            raise AssessmentWorkflowError(
                "Das Assessment muss vor dem Abschluss im Review sein.",
                code="invalid_state",
            )
        now = datetime.now(UTC)
        self._run_completion_steps(session)
        session.result = evaluate_assessment(self.pack, session)
        if self._knowledge_enabled() and endpoint and model:
            self._generate_knowledge_recommendations(session, endpoint, model)
        session.report = render_markdown_report(
            self.pack,
            session,
            session.result,
        )
        session.status = "completed"
        session.completed_at = now
        session.updated_at = now
        session.events.append(AssessmentEvent(event_type="session_completed"))
        profile = self._profile_for(session)
        self.repository.save(session)
        logger.info("session_completed session_id=%s", session.session_id)
        if profile is not None:
            assert self.profiles is not None
            self.profiles.save(profile)
            logger.info(
                "%s session_id=%s profile_id=%s",
                session.events[-1].event_type,
                session.session_id,
                profile.profile_id,
            )
        return self._response(session)

    def _profile_for(self, session: AssessmentSession) -> Profile | None:
        """The participant's profile of the domain, created from this
        completed session or, if one exists, replaced by it under its stable
        id. Not saved yet; the session event names what happened."""
        if self.profiles is None:
            return None
        try:
            profile = self.profiles.get(self.pack.domain_id, session.participant_id)
            previous_dataset = profile.dataset
            profile.replace_with(session)
            event_type = "profile_replaced"
        except LookupError:
            profile = Profile.from_session(session)
            previous_dataset = None
            event_type = "profile_created"
        self._carry_dataset(session, profile, previous_dataset)
        session.events.append(
            AssessmentEvent(event_type=event_type, payload={"profile_id": profile.profile_id})
        )
        return profile

    def _carry_dataset(
        self, session: AssessmentSession, profile: Profile, previous: DatasetRef | None
    ) -> None:
        """With a file question for the advisory, the profile's dataset is
        the one the completed session carries (or none); a dataset of the
        earlier profile the session does not bring along is deleted on the
        server. Packs without such a question keep the panel's dataset."""
        question = advisory_file_question(self.pack)
        if question is None:
            return
        answer = session.answers.get(question.field_key)
        value = answer.normalized_value if answer is not None else None
        current = (
            DatasetRef.model_validate(value)
            if isinstance(value, dict) and value.get("dataset_id") else None
        )
        if previous is not None and (current is None or current.dataset_id != previous.dataset_id):
            settings = self.pack.tools
            if self.dataset_importer is not None and settings is not None:
                self.dataset_importer.delete(
                    self.pack.domain_id, session.participant_id, settings.delete_tool, previous.dataset_id
                )
            profile.events = [
                *profile.events,
                AssessmentEvent(event_type="dataset_deleted",
                                payload={"dataset_id": previous.dataset_id, "session_id": session.session_id}),
            ]
        if current is None or previous is None or current.dataset_id != previous.dataset_id:
            # The first analysis ran in the session right after the import.
            profile.tool_results = list(session.tool_results) if current is not None else []
        profile.dataset = current

    def _knowledge_enabled(self) -> bool:
        settings = self.pack.knowledge_recommendations
        return self.knowledge_recommender is not None and (
            settings is None or settings.enabled
        )

    def _generate_knowledge_recommendations(
        self, session: AssessmentSession, endpoint: str, model: str
    ) -> None:
        assert self.knowledge_recommender is not None and session.result is not None
        try:
            recommendations, generation, excluded = self.knowledge_recommender(
                self.pack, session, endpoint, model
            )
        except RecommendationServiceError as exc:
            session.result.knowledge_generation = KnowledgeGeneration(
                model=model, prompt_version=PROMPT_VERSION, error_code=exc.code
            )
            session.events.append(
                AssessmentEvent(
                    event_type="knowledge_recommendations_failed",
                    payload={"code": exc.code},
                )
            )
            logger.warning(
                "knowledge_recommendations_failed session_id=%s code=%s",
                session.session_id,
                exc.code,
            )
            return
        session.result.knowledge_recommendations = recommendations
        session.result.knowledge_generation = generation
        session.result.excluded = excluded
        session.events.append(
            AssessmentEvent(
                event_type="knowledge_recommendations_generated",
                payload={"count": len(recommendations)},
            )
        )
        logger.info(
            "knowledge_recommendations_persisted session_id=%s count=%d model=%s",
            session.session_id,
            len(recommendations),
            model,
        )

    def ask_question_help(
        self,
        session_id: str,
        question_id: str,
        text: str,
        endpoint: str,
        model: str,
    ) -> AssessmentResponse:
        if self.chat_assistant is None:
            raise AssessmentWorkflowError(
                "Für Rückfragen ist kein LLM-Assistant konfiguriert.",
                code="llm_unavailable",
            )
        normalized_text = text.strip()
        if not normalized_text:
            issue = AssessmentValidationIssue(
                field_key="__chat__",
                code="required",
                message="Bitte gib zuerst eine Rückfrage ein.",
            )
            return self._response(
                self._load_session(session_id),
                validation_errors=[issue],
            )

        session = self._load_session(session_id)
        if session.status != "in_progress":
            raise AssessmentWorkflowError(
                "Rückfragen zur aktuellen Frage sind nur während eines "
                "laufenden Assessments möglich.",
                code="invalid_state",
            )
        question = self._get_question(question_id)
        if question.question_id != session.current_question_id:
            raise AssessmentWorkflowError(
                "Die Rückfrage gehört nicht zur aktuellen Frage.",
                code="invalid_question",
            )

        try:
            result = self.chat_assistant.ask_question_help(
                endpoint,
                model,
                self.pack,
                session,
                question,
                normalized_text,
            )
        except (ProviderError, AssessmentChatError) as exc:
            issue = AssessmentValidationIssue(
                field_key="__chat__",
                code="chat_failed",
                message=str(exc),
            )
            return self._response(session, validation_errors=[issue])

        proposed_answer = None
        validation_errors = []
        if result.proposes_answer:
            try:
                normalized = validate_answer(question, result.normalized_value)
            except AnswerValidationError as exc:
                validation_errors.append(
                    AssessmentValidationIssue(
                        field_key=question.field_key,
                        code=exc.code,
                        message=str(exc),
                    )
                )
            else:
                proposed_answer = ProposedAssessmentAnswer(
                    question_id=question.question_id,
                    field_key=question.field_key,
                    raw_answer=normalized_text,
                    normalized_value=normalized,
                    confidence=result.confidence,
                )

        session.chat_messages.append(
            AssessmentChatMessage(
                scope="question_help",
                role="user",
                content=normalized_text,
                question_id=question.question_id,
            )
        )
        session.chat_messages.append(
            AssessmentChatMessage(
                scope="question_help",
                role="assistant",
                content=result.message,
                question_id=question.question_id,
                proposed_answer=proposed_answer,
            )
        )
        session.events.append(
            AssessmentEvent(
                event_type="question_help_chat",
                payload={
                    "question_id": question.question_id,
                    "proposed_answer": proposed_answer is not None,
                },
            )
        )
        session.updated_at = datetime.now(UTC)
        self.repository.save(session)
        return self._response(session, validation_errors=validation_errors)

    def submit_question_chat(
        self,
        session_id: str,
        question_id: str,
        text: str,
        endpoint: str,
        model: str,
    ) -> AssessmentResponse:
        if self.chat_assistant is None:
            raise AssessmentWorkflowError(
                "Für Chat-Antworten ist kein LLM-Assistant konfiguriert.",
                code="llm_unavailable",
            )
        session = self._load_session(session_id)
        normalized_text = text.strip()
        if not normalized_text:
            issue = AssessmentValidationIssue(
                field_key="__chat__",
                code="required",
                message="Bitte gib zuerst eine Antwort oder Rückfrage ein.",
            )
            return self._response(session, validation_errors=[issue])
        if session.status != "in_progress":
            raise AssessmentWorkflowError(
                "Chat-Antworten sind nur während eines laufenden Assessments "
                "möglich.",
                code="invalid_state",
            )
        question = self._get_question(question_id)
        if question.question_id != session.current_question_id:
            raise AssessmentWorkflowError(
                "Die Nachricht gehört nicht zur aktuellen Frage.",
                code="invalid_question",
            )

        direct = _direct_chat_value(question, normalized_text)
        direct_reply = _direct_chat_reply(question, normalized_text)
        if direct is not _ASK_LLM:
            # Deterministic input needs no model: the validation engine decides.
            result = QuestionHelpResult(
                message="", proposes_answer=True, normalized_value=direct, confidence=1.0
            )
        elif direct_reply is not None:
            # Questions about the available options are answered from the pack.
            result = QuestionHelpResult(
                message=direct_reply, proposes_answer=False, normalized_value=None, confidence=1.0
            )
        else:
            try:
                result = self.chat_assistant.respond_to_question(
                    endpoint,
                    model,
                    self.pack,
                    session,
                    question,
                    normalized_text,
                )
            except (ProviderError, AssessmentChatError) as exc:
                issue = AssessmentValidationIssue(
                    field_key="__chat__",
                    code="chat_failed",
                    message=str(exc),
                )
                return self._response(session, validation_errors=[issue])

        session.chat_messages.append(
            AssessmentChatMessage(
                scope="question_help",
                role="user",
                content=normalized_text,
                question_id=question.question_id,
            )
        )
        reply = result.message
        accept = False
        accepted_value = None
        if result.proposes_answer:
            try:
                accepted_value = validate_answer(question, result.normalized_value)
                accept = True
            except AnswerValidationError as exc:
                reply = str(exc)
        if not accept:
            # An accepted answer needs no confirmation message: the transcript
            # shows the stored value and the next question follows directly.
            session.chat_messages.append(
                AssessmentChatMessage(
                    scope="question_help",
                    role="assistant",
                    content=reply,
                    question_id=question.question_id,
                )
            )
        session.events.append(
            AssessmentEvent(
                event_type="question_chat",
                payload={
                    "question_id": question.question_id,
                    "answer_accepted": accept,
                },
            )
        )
        if accept:
            return self._accept(
                session,
                question,
                accepted_value,
                normalized_text,
                result.confidence,
            )
        session.updated_at = datetime.now(UTC)
        self.repository.save(session)
        return self._response(session, assistant_message=reply)

    def accept_proposed_answer(
        self,
        session_id: str,
        message_id: str,
    ) -> AssessmentResponse:
        session = self._load_session(session_id)
        if session.status != "in_progress":
            raise AssessmentWorkflowError(
                "Antwortvorschläge können nur während eines laufenden "
                "Assessments übernommen werden.",
                code="invalid_state",
            )
        message = self._get_chat_message(session, message_id)
        proposed = message.proposed_answer
        if proposed is None:
            raise AssessmentWorkflowError(
                "Diese Chatnachricht enthält keinen Antwortvorschlag.",
                code="proposal_not_found",
            )
        question = self._get_question(proposed.question_id)
        if question.question_id != session.current_question_id:
            raise AssessmentWorkflowError(
                "Der Antwortvorschlag gehört nicht zur aktuellen Frage.",
                code="invalid_question",
            )
        return self._accept(
            session,
            question,
            proposed.normalized_value,
            proposed.raw_answer,
            proposed.confidence,
        )

    def discard_proposed_answer(
        self,
        session_id: str,
        message_id: str,
    ) -> AssessmentResponse:
        session = self._load_session(session_id)
        message = self._get_chat_message(session, message_id)
        if message.proposed_answer is None:
            return self._response(session)
        message.proposed_answer = None
        session.updated_at = datetime.now(UTC)
        self.repository.save(session)
        return self._response(session)

    def _ordered_answers(
        self, session: AssessmentSession
    ) -> dict[str, AssessmentAnswer]:
        """Answers in pack question order; storage backends do not keep it."""
        return {
            question.field_key: session.answers[question.field_key]
            for question in self.pack.questions
            if question.field_key in session.answers
        }

    def _accept(
        self,
        session: AssessmentSession,
        question: QuestionDefinition,
        value: Any,
        raw_answer: str | None,
        confidence: float,
        *,
        keep_review: bool = False,
    ) -> AssessmentResponse:
        try:
            normalized = validate_answer(question, value)
        except AnswerValidationError as exc:
            issue = AssessmentValidationIssue(
                field_key=question.field_key,
                code=exc.code,
                message=str(exc),
            )
            logger.info(
                "answer_rejected session_id=%s field_key=%s code=%s",
                session.session_id,
                question.field_key,
                exc.code,
            )
            return self._response(session, validation_errors=[issue])

        previous = session.answers.get(question.field_key)
        imported = False
        if question.field_type == "file":
            if normalized is not None and "dataset_id" not in normalized:
                normalized = self._import_file(session, question, normalized)
                if isinstance(normalized, AssessmentValidationIssue):
                    return self._response(session, validation_errors=[normalized])
                imported = True
            if normalized is not None:
                raw_answer = str(normalized.get("filename") or "")

        answer = AssessmentAnswer(
            question_id=question.question_id,
            field_key=question.field_key,
            raw_answer=raw_answer if raw_answer is not None else _raw_value(value),
            normalized_value=normalized,
            confidence=confidence,
        )
        session.answers[question.field_key] = answer
        if question.field_type == "file":
            self._delete_replaced_dataset(session, question, previous, normalized)
            if question is advisory_file_question(self.pack) and (imported or normalized is None):
                # A new dataset (or none): earlier derivations no longer hold.
                self._clear_derivations(session)
                if imported:
                    self._analyze_dataset(session, normalized)
                    self._apply_derivations(session, normalized)
        session.events.append(
            AssessmentEvent(
                event_type="answer_accepted",
                payload={"field_key": question.field_key},
            )
        )
        session.updated_at = datetime.now(UTC)
        self._remove_invisible_answers(session)

        next_question = get_next_question(self.pack, session)
        session.current_question_id = (
            next_question.question_id if next_question else None
        )
        if next_question is None:
            session.status = "review"
        elif keep_review:
            session.status = "in_progress"

        self.repository.save(session)
        logger.info(
            "answer_accepted session_id=%s field_key=%s status=%s",
            session.session_id,
            question.field_key,
            session.status,
        )
        return self._response(session, accepted_answer=answer)

    def _import_file(
        self,
        session: AssessmentSession,
        question: QuestionDefinition,
        upload: dict[str, Any],
    ) -> dict[str, Any] | AssessmentValidationIssue:
        """Import the validated upload through the pack's dataset tool. A
        failure is a validation issue for the question (tool_error for a
        professional finding, tool_server_unavailable when no server could
        be used), so the question stays open and nothing is stored."""
        settings = self.pack.tools
        if self.dataset_importer is None or settings is None or question.upload is None:
            return AssessmentValidationIssue(
                field_key=question.field_key,
                code="tool_server_unavailable",
                message="Für diese Domäne ist kein Berechnungsdienst konfiguriert; die Datei kann "
                        "derzeit nicht importiert werden.",
            )
        tool = question.upload.tool or settings.dataset_tool
        try:
            reference, _ = self.dataset_importer.import_text(
                self.pack.domain_id, session.participant_id, tool, upload["filename"],
                upload["content"], upload.get("format_hint", "auto"), question.upload.arguments,
                field_key=question.field_key,
            )
        except ToolError as exc:
            code = "tool_server_unavailable" if exc.code in UNAVAILABLE_CODES else "tool_error"
            logger.info(
                "dataset_import_failed session_id=%s field_key=%s code=%s",
                session.session_id, question.field_key, exc.code,
            )
            return AssessmentValidationIssue(
                field_key=question.field_key, code=code,
                message=f"Die Messdaten konnten nicht importiert werden: {exc.message}",
            )
        session.events.append(AssessmentEvent(
            event_type="dataset_imported",
            payload={"field_key": question.field_key, "dataset_id": reference.dataset_id,
                     "server": reference.server_name},
        ))
        logger.info(
            "dataset_imported session_id=%s field_key=%s dataset_id=%s server=%s",
            session.session_id, question.field_key, reference.dataset_id, reference.server_name,
        )
        return reference.model_dump(mode="json")

    # --- first analysis and derivations (pack block tools) ---------------------

    def _analyze_dataset(self, session: AssessmentSession, reference: dict[str, Any]) -> None:
        """Run the pack's first analysis on the dataset just imported, step by
        step; the results stay on the session (and later on the profile) as
        the tool results the advisory may cite. A failing step is recorded
        and skipped, the import stands."""
        settings = self.pack.tools
        if settings is None or self.dataset_importer is None:
            return
        for step in settings.analysis_steps():
            self._run_analysis_step(session, reference["dataset_id"], step, step.arguments)

    def _run_completion_steps(self, session: AssessmentSession) -> None:
        """Steps of tools.at_completion need answers (arguments_from_answers);
        they run once the assessment completes, on the advisory dataset."""
        settings = self.pack.tools
        question = advisory_file_question(self.pack)
        if settings is None or self.dataset_importer is None or question is None:
            return
        answer = session.answers.get(question.field_key)
        value = answer.normalized_value if answer is not None else None
        if not isinstance(value, dict) or not value.get("dataset_id"):
            return
        answers = {key: item.normalized_value for key, item in session.answers.items()}
        for step in settings.completion_steps():
            self._run_analysis_step(session, value["dataset_id"], step, step_arguments(step, answers))

    def _run_analysis_step(self, session: AssessmentSession, dataset_id: str, step, arguments: dict[str, Any]) -> None:
        """One analysis step: the result replaces an earlier one of the same
        tool; a failure is recorded and skipped."""
        try:
            result = self.dataset_importer.analyze(
                self.pack.domain_id, session.participant_id, step.tool, dataset_id, arguments,
            )
        except ToolError as exc:
            logger.warning("first_analysis_failed session_id=%s tool=%s code=%s",
                           session.session_id, step.tool, exc.code)
            session.events.append(AssessmentEvent(
                event_type="analysis_failed", payload={"tool": step.tool, "code": exc.code},
            ))
            return
        session.tool_results = [
            *[item for item in session.tool_results if item.call.name != step.tool], result,
        ]
        session.events.append(AssessmentEvent(
            event_type="analysis_completed",
            payload={"tool": step.tool, "calculation_id": result.calculation_id},
        ))
        logger.info("first_analysis_completed session_id=%s tool=%s calculation_id=%s",
                    session.session_id, step.tool, result.calculation_id)

    def _apply_derivations(self, session: AssessmentSession, reference: dict[str, Any]) -> None:
        """Answers and proposals from the import and analysis results, as the
        pack's derivations define them. Derived values pass validation like
        any answer; a participant's own answer is never overwritten."""
        settings = self.pack.tools
        if settings is None:
            return
        outputs: dict[str, tuple[dict[str, Any], str | None]] = {
            settings.dataset_tool: (dict(reference.get("details") or {}), None),
        }
        for result in session.tool_results:
            outputs[result.call.name] = (result.structured_content or {}, result.calculation_id)
        questions = {question.field_key: question for question in self.pack.questions}
        for derivation in settings.derive:
            if derivation.tool not in outputs:
                continue
            output, calculation_id = outputs[derivation.tool]
            question = questions[derivation.field_key]
            value = _derived_value(derivation, output)
            if value is None:
                continue
            try:
                normalized = validate_answer(question, _coerce(question, value, derivation.decimals))
            except AnswerValidationError as exc:
                logger.info("derivation_rejected session_id=%s field_key=%s code=%s",
                            session.session_id, derivation.field_key, exc.code)
                continue
            existing = session.answers.get(derivation.field_key)
            if existing is not None and existing.origin == "user" and existing.normalized_value is not None:
                # A participant's own answer stands; only "unknown" is filled in.
                continue
            if derivation.mode == "answer":
                session.answers[derivation.field_key] = AssessmentAnswer(
                    question_id=question.question_id,
                    field_key=question.field_key,
                    raw_answer=MEASUREMENT_RAW_ANSWER,
                    normalized_value=normalized,
                    confidence=1.0,
                    origin="measurement",
                    calculation_id=calculation_id,
                )
                session.events.append(AssessmentEvent(
                    event_type="answer_derived",
                    payload={"field_key": question.field_key, "tool": derivation.tool,
                             "calculation_id": calculation_id},
                ))
                logger.info("answer_derived session_id=%s field_key=%s tool=%s",
                            session.session_id, question.field_key, derivation.tool)
            else:
                confidence = (
                    resolve_pointer(output, derivation.confidence_pointer)
                    if derivation.confidence_pointer else None
                )
                session.proposals[derivation.field_key] = DerivedProposal(
                    question_id=question.question_id,
                    field_key=question.field_key,
                    normalized_value=normalized,
                    tool=derivation.tool,
                    calculation_id=calculation_id,
                    confidence=str(confidence) if confidence is not None else None,
                    note=derivation.note,
                )
                session.events.append(AssessmentEvent(
                    event_type="answer_proposed",
                    payload={"field_key": question.field_key, "tool": derivation.tool,
                             "calculation_id": calculation_id},
                ))

    def _clear_derivations(self, session: AssessmentSession) -> None:
        for field_key in [key for key, answer in session.answers.items() if answer.origin == "measurement"]:
            del session.answers[field_key]
        session.proposals = {}
        session.tool_results = []

    def _delete_replaced_dataset(
        self,
        session: AssessmentSession,
        question: QuestionDefinition,
        previous: AssessmentAnswer | None,
        normalized: dict[str, Any] | None,
    ) -> None:
        """After a file answer changed: the dataset of the earlier answer is
        deleted on the server unless the new answer still references it."""
        if previous is None or not isinstance(previous.normalized_value, dict):
            return
        old_id = previous.normalized_value.get("dataset_id")
        if not old_id or (normalized is not None and normalized.get("dataset_id") == old_id):
            return
        self._delete_dataset(session, question.field_key, old_id)

    def _delete_dataset(self, session: AssessmentSession, field_key: str, dataset_id: str) -> None:
        settings = self.pack.tools
        if self.dataset_importer is None or settings is None:
            logger.warning("dataset_delete_skipped session_id=%s dataset_id=%s reason=no_tools",
                           session.session_id, dataset_id)
            return
        self.dataset_importer.delete(
            self.pack.domain_id, session.participant_id, settings.delete_tool, dataset_id
        )
        session.events.append(AssessmentEvent(
            event_type="dataset_deleted", payload={"field_key": field_key, "dataset_id": dataset_id}
        ))

    def _remove_invisible_answers(self, session: AssessmentSession) -> None:
        questions = {
            question.field_key: question for question in self.pack.questions
        }
        changed = True
        while changed:
            changed = False
            for field_key in list(session.answers):
                question = questions.get(field_key)
                if question is None:
                    continue
                if not is_question_visible(self.pack, session, question):
                    removed = session.answers.pop(field_key)
                    changed = True
                    value = removed.normalized_value
                    if question.field_type == "file" and isinstance(value, dict) and value.get("dataset_id"):
                        self._delete_dataset(session, field_key, value["dataset_id"])

    def _load_session(self, session_id: str) -> AssessmentSession:
        session = self.repository.get(session_id)
        if (
            session.domain_id != self.pack.domain_id
            or session.domain_version != self.pack.version
            or session.domain_content_hash != self.pack.content_hash
        ):
            raise AssessmentWorkflowError(
                "Die Domain-Version der Session ist nicht mehr verfügbar.",
                code="domain_version_unavailable",
            )
        return session

    def _get_question(self, question_id: str) -> QuestionDefinition:
        for question in self.pack.questions:
            if question.question_id == question_id:
                return question
        raise AssessmentWorkflowError(
            f"Unbekannte Frage: {question_id}.",
            code="invalid_question",
        )

    def _get_chat_message(
        self,
        session: AssessmentSession,
        message_id: str,
    ) -> AssessmentChatMessage:
        for message in session.chat_messages:
            if message.message_id == message_id:
                return message
        raise AssessmentWorkflowError(
            f"Chatnachricht {message_id} wurde nicht gefunden.",
            code="proposal_not_found",
        )

    def _response(
        self,
        session: AssessmentSession,
        *,
        accepted_answer: AssessmentAnswer | None = None,
        validation_errors: list[AssessmentValidationIssue] | None = None,
        assistant_message: str | None = None,
    ) -> AssessmentResponse:
        current_question = None
        if session.current_question_id:
            current_question = self._get_question(session.current_question_id)
        visible_required = [
            question
            for question in self.pack.questions
            if question.required and is_question_visible(self.pack, session, question)
        ]
        answered_required = sum(
            question.field_key in session.answers for question in visible_required
        )
        required_total = len(visible_required)
        return AssessmentResponse(
            session_id=session.session_id,
            status=session.status,
            assistant_message=assistant_message
            or _assistant_message(session.status, current_question),
            current_question=current_question,
            progress=AssessmentProgress(
                answered_required=answered_required,
                required_total=required_total,
            ),
            answers=self._ordered_answers(session),
            accepted_answer=accepted_answer,
            validation_errors=validation_errors or [],
            proposal=(
                session.proposals.get(current_question.field_key) if current_question is not None else None
            ),
            result=session.result,
            report=session.report,
            chat_messages=session.chat_messages,
            answered_questions=[
                question for question in self.pack.questions if question.field_key in session.answers
            ],
            upload_formats=self._upload_formats(current_question),
        )

    def _upload_formats(self, question: QuestionDefinition | None) -> list[str]:
        if question is None or question.field_type != "file":
            return []
        tools = self.dataset_importer.tools if self.dataset_importer is not None else None
        return format_options(tools, self.pack, question)


_ASK_LLM = object()
UNKNOWN_PHRASES = {
    "unbekannt", "weiß ich nicht", "weiss ich nicht", "keine ahnung", "ich weiß es nicht",
    "ich weiss es nicht", "kenne ich nicht", "überspringen",
}


def direct_chat_value(question: QuestionDefinition, text: str) -> Any:
    """Public name of _direct_chat_value for the profile chat."""
    return _direct_chat_value(question, text)


def _direct_chat_value(question: QuestionDefinition, text: str) -> Any:
    """Value for a chat message that needs no model, or ``_ASK_LLM``.

    Unknown phrases become ``None``; choice answers are matched against option
    values and labels; numbers, dates and Ja/Nein must parse deterministically;
    free text counts as the answer unless it is a question. Everything else is
    left to the chat assistant.
    """
    lowered = text.strip().lower().rstrip(".!")
    if lowered in UNKNOWN_PHRASES:
        return None
    if question.field_type in ("select", "multiselect"):
        by_name = {}
        for option in question.options:
            by_name[option.value.lower()] = option.value
            by_name[option.label.lower()] = option.value
        parts = [part.strip().lower() for part in text.split(",") if part.strip()]
        if not parts or any(part not in by_name for part in parts):
            return _ASK_LLM
        values = [by_name[part] for part in parts]
        if question.field_type == "select":
            return values[0] if len(values) == 1 else _ASK_LLM
        return values
    if question.field_type == "text":
        return _ASK_LLM if text.rstrip().endswith("?") else text
    try:
        validate_answer(question, text)
    except AnswerValidationError as exc:
        if exc.code == "invalid_type":
            return _ASK_LLM
    return text


OPTION_QUESTION_WORDS = ("option", "möglichkeit", "auswahl", "zur wahl", "auswählen", "wählen")


def _direct_chat_reply(question: QuestionDefinition, text: str) -> str | None:
    """Pack-based reply to "which options are there?", or None for other messages."""
    if not question.options:
        return None
    lowered = text.strip().lower()
    asks_for_options = any(word in lowered for word in OPTION_QUESTION_WORDS) or (
        "welche" in lowered and "gibt es" in lowered
    )
    if not asks_for_options:
        return None
    is_question = lowered.endswith("?") or lowered.startswith(("welche", "was "))
    if not is_question or " passt " in lowered or " für " in lowered:
        return None
    return "Mögliche Antworten: " + ", ".join(option.label for option in question.options) + "."


def _raw_value(value: Any) -> str:
    if value is None:
        return "unbekannt"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _assistant_message(
    status: str,
    current_question: QuestionDefinition | None,
) -> str:
    if current_question:
        return current_question.text
    if status == "review":
        return "Bitte prüfe deine Angaben vor dem Abschluss."
    return "Das Assessment ist abgeschlossen."


def _contract_error_response(
    input: AssessmentInput,
    code: AssessmentErrorCode,
    message: str,
) -> AssessmentContractResponse:
    return AssessmentContractResponse(
        ok=False,
        session_id=getattr(input, "session_id", None),
        assistant_message=message,
        errors=[
            AssessmentContractError(
                code=code,
                message=message,
                question_id=getattr(input, "question_id", None),
            )
        ],
    )


def _derived_value(derivation: DerivationDefinition, output: dict[str, Any]) -> Any:
    """The raw value a derivation yields from a tool output, None when its
    condition fails, the pointer is empty or the map has no entry."""
    if derivation.when is not None:
        observed = resolve_pointer(output, derivation.when.pointer)
        if observed is None or not compare(observed, derivation.when.operator, derivation.when.value):
            return None
    value = resolve_pointer(output, derivation.pointer) if derivation.pointer else derivation.value
    if value is None:
        return None
    if derivation.map is not None:
        key = str(value).lower() if isinstance(value, bool) else str(value)
        if key not in derivation.map:
            return None
        value = derivation.map[key]
    return value


def _coerce(question: QuestionDefinition, value: Any, decimals: int | None) -> Any:
    """Numbers arrive as strings from tool outputs (Decimal); turn them into
    numbers for numeric questions and round as the derivation asks."""
    if question.field_type in ("integer", "number"):
        number = numeric(value)
        if number is None:
            return value
        if decimals is not None:
            number = round(number, decimals)
        if question.field_type == "integer" or decimals == 0:
            return int(round(number))
        return number
    return value
