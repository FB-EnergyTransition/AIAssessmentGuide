from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from guideos.assessment.models import (
    AssessmentAnswer,
    AssessmentChatMessage,
    AssessmentProgress,
    AssessmentReport,
    AssessmentResponse,
    AssessmentResult,
    AssessmentStatus,
    AssessmentValidationIssue,
    ExcludedRecommendation,
    KnowledgeGeneration,
    LLMRecommendation,
    ProposedAssessmentAnswer,
    RecommendationResult,
    ResolvedFigure,
)
from guideos.domains.models import QuestionDefinition, QuestionOption, effective_widget


class AssessmentContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartAssessmentInput(AssessmentContractModel):
    kind: Literal["start"] = "start"
    locale: str | None = None


class SubmitAnswerInput(AssessmentContractModel):
    kind: Literal["submit_answer"] = "submit_answer"
    session_id: str
    question_id: str
    value: Any
    raw_answer: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class SubmitTextAnswerInput(AssessmentContractModel):
    kind: Literal["submit_text_answer"] = "submit_text_answer"
    session_id: str
    question_id: str
    text: str
    endpoint: str
    model: str


class ReviseAnswerInput(AssessmentContractModel):
    kind: Literal["revise_answer"] = "revise_answer"
    session_id: str
    question_id: str
    value: Any
    raw_answer: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class CompleteAssessmentInput(AssessmentContractModel):
    kind: Literal["complete"] = "complete"
    session_id: str
    # With both given, the first knowledge recommendations are generated at
    # completion; without them completion stays deterministic.
    endpoint: str | None = None
    model: str | None = None


class QuestionHelpInput(AssessmentContractModel):
    kind: Literal["question_help"] = "question_help"
    session_id: str
    question_id: str
    text: str
    endpoint: str
    model: str


class AcceptProposedAnswerInput(AssessmentContractModel):
    kind: Literal["accept_proposed_answer"] = "accept_proposed_answer"
    session_id: str
    message_id: str


class DiscardProposedAnswerInput(AssessmentContractModel):
    kind: Literal["discard_proposed_answer"] = "discard_proposed_answer"
    session_id: str
    message_id: str


AssessmentInput: TypeAlias = (
    StartAssessmentInput
    | SubmitAnswerInput
    | SubmitTextAnswerInput
    | ReviseAnswerInput
    | CompleteAssessmentInput
    | QuestionHelpInput
    | AcceptProposedAnswerInput
    | DiscardProposedAnswerInput
)

AssessmentInputAdapter = TypeAdapter(
    Annotated[AssessmentInput, Field(discriminator="kind")]
)


def parse_assessment_input(payload: dict[str, Any]) -> AssessmentInput:
    return AssessmentInputAdapter.validate_python(payload)


AssessmentErrorCode: TypeAlias = Literal[
    "session_not_found",
    "session_conflict",
    "domain_version_unavailable",
    "invalid_input",
    "invalid_question",
    "invalid_state",
    "validation_failed",
    "interpretation_uncertain",
    "llm_unavailable",
    "proposal_not_found",
    "provider_error",
]


class AssessmentContractError(AssessmentContractModel):
    code: AssessmentErrorCode
    message: str
    field_key: str | None = None
    question_id: str | None = None


class ContractQuestionOption(AssessmentContractModel):
    value: str
    label: str


class ContractQuestionValidation(AssessmentContractModel):
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    min_date: str | None = None
    max_date: str | None = None


class ContractUpload(AssessmentContractModel):
    """What a client must send for a file question: a value
    {"filename", "content" (decoded text), "format_hint"} with one of these
    extensions and at most max_bytes of text. The tool and its fixed
    arguments stay inside GuideOS."""

    extensions: list[str]
    max_bytes: int
    # Format hints the import tool accepts, "auto" first (only on the current question).
    formats: list[str] = Field(default_factory=lambda: ["auto"])


class ContractQuestion(AssessmentContractModel):
    question_id: str
    field_key: str
    field_type: Literal["select", "multiselect", "integer", "number", "text", "boolean", "date", "file"]
    widget: Literal["dropdown", "radio", "checkboxes", "input", "textarea"] | None = None
    text: str
    required: bool
    allow_unknown: bool = False
    options: list[ContractQuestionOption] = Field(default_factory=list)
    validation: ContractQuestionValidation = Field(default_factory=ContractQuestionValidation)
    upload: ContractUpload | None = None


class ContractProgress(AssessmentContractModel):
    answered_required: int
    required_total: int


class ContractAnswer(AssessmentContractModel):
    question_id: str
    field_key: str
    raw_answer: str
    normalized_value: Any
    confidence: float = Field(ge=0, le=1)
    # user, or measurement when GuideOS derived the answer from the dataset.
    origin: Literal["user", "measurement"] = "user"
    calculation_id: str | None = None


class ContractProposal(AssessmentContractModel):
    """A value derived from measurement data for the current question; the
    client shows it as the default, the participant confirms or changes it."""

    question_id: str
    field_key: str
    normalized_value: Any
    tool: str
    calculation_id: str | None = None
    confidence: str | None = None
    note: str | None = None


class ContractProposedAnswer(AssessmentContractModel):
    question_id: str
    field_key: str
    raw_answer: str
    normalized_value: Any
    confidence: float = Field(ge=0, le=1)


class ContractValidationIssue(AssessmentContractModel):
    field_key: str
    code: str
    message: str


class ContractRecommendationResult(AssessmentContractModel):
    recommendation_id: str
    priority: int
    reason_codes: list[str]
    evidence: dict[str, Any]
    title: str
    message: str
    source: Literal["rule"] = "rule"
    # Figures GuideOS filled into message from tool results (name -> value, text, tool, calculation_id).
    figures: dict[str, ResolvedFigure] = Field(default_factory=dict)


class ContractKnowledgeGeneration(AssessmentContractModel):
    """How the knowledge recommendations were produced; the passages
    themselves stay on the stored session."""

    model: str
    prompt_version: int
    generated_at: datetime
    error_code: str | None = None
    passage_count: int = 0
    # How the passages were found (lexical | hybrid, optionally "+rerank").
    retrieval_mode: str | None = None
    embedding_model: str | None = None
    rerank_model: str | None = None
    # The calculations (TOOL_RESULTS) the model saw.
    tool_calculation_ids: list[str] = Field(default_factory=list)


class ContractAssessmentResult(AssessmentContractModel):
    reason_codes: list[str]
    recommendations: list[ContractRecommendationResult]
    knowledge_recommendations: list[LLMRecommendation] = Field(default_factory=list)
    knowledge_generation: ContractKnowledgeGeneration | None = None
    excluded: list[ExcludedRecommendation] = Field(default_factory=list)


class ContractReport(AssessmentContractModel):
    format: Literal["markdown"]
    content: str
    created_at: datetime


class ContractChatMessage(AssessmentContractModel):
    message_id: str
    scope: Literal["question_help", "post_completion"]
    role: Literal["user", "assistant"]
    content: str
    question_id: str | None = None
    proposed_answer: ContractProposedAnswer | None = None
    created_at: datetime


class AssessmentContractResponse(AssessmentContractModel):
    ok: bool
    session_id: str | None = None
    status: AssessmentStatus | None = None
    assistant_message: str
    current_question: ContractQuestion | None = None
    progress: ContractProgress | None = None
    answers: dict[str, ContractAnswer] = Field(default_factory=dict)
    accepted_answer: ContractAnswer | None = None
    validation_errors: list[ContractValidationIssue] = Field(default_factory=list)
    errors: list[AssessmentContractError] = Field(default_factory=list)
    result: ContractAssessmentResult | None = None
    report: ContractReport | None = None
    chat_messages: list[ContractChatMessage] = Field(default_factory=list)
    proposal: ContractProposal | None = None
    # Definitions of the answered questions in pack order (labels, options for revisions).
    questions: list[ContractQuestion] = Field(default_factory=list)


def to_contract_response(
    response: AssessmentResponse,
    *,
    errors: list[AssessmentContractError] | None = None,
) -> AssessmentContractResponse:
    validation_errors = [
        _contract_validation_issue(issue) for issue in response.validation_errors
    ]
    response_errors = list(errors or [])
    if not response_errors:
        response_errors = [
            _error_from_validation_issue(issue)
            for issue in response.validation_errors
        ]
    return AssessmentContractResponse(
        ok=not response_errors and not validation_errors,
        session_id=response.session_id,
        status=response.status,
        assistant_message=response.assistant_message,
        current_question=_contract_question(response.current_question, response.upload_formats),
        progress=_contract_progress(response.progress),
        answers={
            field_key: _contract_answer(answer)
            for field_key, answer in response.answers.items()
        },
        accepted_answer=_contract_answer(response.accepted_answer),
        validation_errors=validation_errors,
        errors=response_errors,
        result=_contract_result(response.result),
        report=_contract_report(response.report),
        chat_messages=[
            _contract_chat_message(message) for message in response.chat_messages
        ],
        proposal=(
            ContractProposal(**response.proposal.model_dump()) if response.proposal is not None else None
        ),
        questions=[_contract_question(question) for question in response.answered_questions],
    )


def _contract_question(
    question: QuestionDefinition | None,
    upload_formats: list[str] | None = None,
) -> ContractQuestion | None:
    if question is None:
        return None
    return ContractQuestion(
        question_id=question.question_id,
        field_key=question.field_key,
        field_type=question.field_type,
        widget=effective_widget(question),
        text=question.text,
        required=question.required,
        allow_unknown=question.allow_unknown,
        options=[_contract_option(option) for option in question.options],
        validation=ContractQuestionValidation(**question.validation.model_dump()),
        upload=(
            ContractUpload(extensions=list(question.upload.extensions), max_bytes=question.upload.max_bytes,
                           formats=list(upload_formats) if upload_formats else ["auto"])
            if question.upload is not None else None
        ),
    )


def _contract_option(option: QuestionOption) -> ContractQuestionOption:
    return ContractQuestionOption(value=option.value, label=option.label)


def _contract_progress(
    progress: AssessmentProgress | None,
) -> ContractProgress | None:
    if progress is None:
        return None
    return ContractProgress(
        answered_required=progress.answered_required,
        required_total=progress.required_total,
    )


def _contract_answer(answer: AssessmentAnswer | None) -> ContractAnswer | None:
    if answer is None:
        return None
    return ContractAnswer(
        question_id=answer.question_id,
        field_key=answer.field_key,
        raw_answer=answer.raw_answer,
        normalized_value=answer.normalized_value,
        confidence=answer.confidence,
        origin=answer.origin,
        calculation_id=answer.calculation_id,
    )


def _contract_proposed_answer(
    answer: ProposedAssessmentAnswer | None,
) -> ContractProposedAnswer | None:
    if answer is None:
        return None
    return ContractProposedAnswer(
        question_id=answer.question_id,
        field_key=answer.field_key,
        raw_answer=answer.raw_answer,
        normalized_value=answer.normalized_value,
        confidence=answer.confidence,
    )


def _contract_validation_issue(
    issue: AssessmentValidationIssue,
) -> ContractValidationIssue:
    return ContractValidationIssue(
        field_key=issue.field_key,
        code=issue.code,
        message=issue.message,
    )


def _contract_result(
    result: AssessmentResult | None,
) -> ContractAssessmentResult | None:
    if result is None:
        return None
    return ContractAssessmentResult(
        reason_codes=result.reason_codes,
        recommendations=[
            _contract_recommendation(recommendation)
            for recommendation in result.recommendations
        ],
        knowledge_recommendations=list(result.knowledge_recommendations),
        knowledge_generation=_contract_generation(result.knowledge_generation),
        excluded=list(result.excluded),
    )


def _contract_generation(
    generation: KnowledgeGeneration | None,
) -> ContractKnowledgeGeneration | None:
    if generation is None:
        return None
    return ContractKnowledgeGeneration(
        model=generation.model,
        prompt_version=generation.prompt_version,
        generated_at=generation.generated_at,
        error_code=generation.error_code,
        passage_count=len(generation.passages),
        retrieval_mode=generation.retrieval_mode,
        embedding_model=generation.embedding_model,
        rerank_model=generation.rerank_model,
        tool_calculation_ids=list(generation.tool_calculation_ids),
    )


def _contract_recommendation(
    recommendation: RecommendationResult,
) -> ContractRecommendationResult:
    return ContractRecommendationResult(
        recommendation_id=recommendation.recommendation_id,
        priority=recommendation.priority,
        reason_codes=recommendation.reason_codes,
        evidence=recommendation.evidence,
        title=recommendation.title,
        message=recommendation.message,
        source=recommendation.source,
        figures=recommendation.figures,
    )


def _contract_report(report: AssessmentReport | None) -> ContractReport | None:
    if report is None:
        return None
    return ContractReport(
        format=report.format,
        content=report.content,
        created_at=report.created_at,
    )


def _contract_chat_message(message: AssessmentChatMessage) -> ContractChatMessage:
    return ContractChatMessage(
        message_id=message.message_id,
        scope=message.scope,
        role=message.role,
        content=message.content,
        question_id=message.question_id,
        proposed_answer=_contract_proposed_answer(message.proposed_answer),
        created_at=message.created_at,
    )


def _error_from_validation_issue(
    issue: AssessmentValidationIssue,
) -> AssessmentContractError:
    return AssessmentContractError(
        code=_error_code_from_validation_code(issue.code),
        message=issue.message,
        field_key=issue.field_key,
    )


def _error_code_from_validation_code(code: str) -> AssessmentErrorCode:
    if code == "uncertain":
        return "interpretation_uncertain"
    if code in {"interpretation_failed", "chat_failed"}:
        return "provider_error"
    return "validation_failed"
