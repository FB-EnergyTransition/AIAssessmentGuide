from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from guideos.domains.models import QuestionDefinition
from guideos.knowledge.models import RetrievedPassage, SourceRef
from guideos.tools.models import ToolResult


AssessmentStatus = Literal["in_progress", "review", "completed"]
# user: the participant answered; measurement: GuideOS derived the answer
# from the dataset of the advisory file question (pack block tools.derive).
AssessmentAnswerOrigin = Literal["user", "measurement"]
MEASUREMENT_RAW_ANSWER = "Aus den Messdaten abgeleitet"
AssessmentChatScope = Literal["question_help", "post_completion"]
AssessmentChatRole = Literal["user", "assistant"]


class AssessmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AssessmentAnswer(AssessmentModel):
    question_id: str
    field_key: str
    raw_answer: str
    normalized_value: Any
    confidence: float = Field(ge=0, le=1)
    origin: AssessmentAnswerOrigin = "user"
    # The calculation the answer was derived from (origin measurement).
    calculation_id: str | None = None


class DerivedProposal(AssessmentModel):
    """A value GuideOS derived from measurement data for a question the
    participant still answers (pack derivation mode propose): adapters show
    it as the default with the confidence the tool computed."""

    question_id: str
    field_key: str
    normalized_value: Any
    tool: str
    calculation_id: str | None = None
    confidence: str | None = None
    note: str | None = None


class AssessmentEvent(AssessmentModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResolvedFigure(AssessmentModel):
    """A figure GuideOS filled into a recommendation text: the raw tool output
    value, the text shown, and the calculation it comes from."""

    value: Any
    text: str
    tool: str
    calculation_id: str | None = None


class RecommendationResult(AssessmentModel):
    recommendation_id: str
    priority: int
    reason_codes: list[str]
    evidence: dict[str, Any]
    title: str
    message: str
    source: Literal["rule"] = "rule"
    source_reference: str | None = None
    # Set when message is the pack's message_with_figures with resolved placeholders.
    figures: dict[str, ResolvedFigure] = Field(default_factory=dict)


class GeneratedRecommendation(BaseModel):
    """Shape requested from the model for a knowledge recommendation.
    Provenance is added by GuideOS, never by the model."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    # Required, so that schema-constrained output always names the supporting answers.
    evidence_fields: list[str]
    source_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    # A topic_id of the pack's topic catalog, or "other"; null when the pack has no topics.
    topic: str | None = None


class LLMRecommendation(GeneratedRecommendation):
    """A knowledge recommendation as checked by GuideOS. source stays "llm":
    the model wrote it, it is never a rule. sources names the knowledge-base
    passages it rests on, or the model's own knowledge."""

    source: Literal["llm"] = "llm"
    sources: list[SourceRef] = Field(default_factory=list)
    unverified_values: list[str] = Field(default_factory=list)
    # Set by a matched prioritize rule of the pack; prioritized items come first.
    priority: int | None = None


class KnowledgeGeneration(AssessmentModel):
    """How the knowledge recommendations of a result were produced: the
    model, the prompt version and every passage the model saw, so the run
    stays traceable after documents change; error_code when it failed."""

    model: str
    prompt_version: int = Field(ge=1)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    passages: list[RetrievedPassage] = Field(default_factory=list)
    error_code: str | None = None
    # How the passages were found: lexical | hybrid, each optionally "+rerank".
    retrieval_mode: str | None = None
    embedding_model: str | None = None
    rerank_model: str | None = None
    # The calculations (TOOL_RESULTS) the model saw, for the same traceability as the passages.
    tool_calculation_ids: list[str] = Field(default_factory=list)


class ExcludedRecommendation(AssessmentModel):
    """A generated knowledge recommendation GuideOS dropped, with the
    reason: user_feedback (the participant rejected it earlier) or rule
    (an exclude rule of the pack matched its topic)."""

    recommendation_id: str
    title: str
    trigger: Literal["user_feedback", "rule"]
    rule_id: str | None = None
    topic: str | None = None


class AssessmentResult(AssessmentModel):
    reason_codes: list[str]
    recommendations: list[RecommendationResult]
    knowledge_recommendations: list[LLMRecommendation] = Field(default_factory=list)
    knowledge_generation: KnowledgeGeneration | None = None
    excluded: list[ExcludedRecommendation] = Field(default_factory=list)


class AssessmentReport(AssessmentModel):
    format: Literal["markdown"] = "markdown"
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssessmentProgress(AssessmentModel):
    answered_required: int
    required_total: int


class AssessmentValidationIssue(AssessmentModel):
    field_key: str
    code: str
    message: str


class ProposedAssessmentAnswer(AssessmentModel):
    question_id: str
    field_key: str
    raw_answer: str
    normalized_value: Any
    confidence: float = Field(ge=0, le=1)


class AssessmentChatMessage(AssessmentModel):
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    scope: AssessmentChatScope
    role: AssessmentChatRole
    content: str
    question_id: str | None = None
    proposed_answer: ProposedAssessmentAnswer | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssessmentResponse(AssessmentModel):
    session_id: str
    status: AssessmentStatus
    assistant_message: str
    current_question: QuestionDefinition | None
    progress: AssessmentProgress
    answers: dict[str, AssessmentAnswer]
    accepted_answer: AssessmentAnswer | None = None
    validation_errors: list[AssessmentValidationIssue] = Field(default_factory=list)
    result: AssessmentResult | None = None
    report: AssessmentReport | None = None
    chat_messages: list[AssessmentChatMessage] = Field(default_factory=list)
    # The measurement-derived proposal for the current question, if any.
    proposal: DerivedProposal | None = None
    # The definitions of the answered questions in pack order, so a client
    # without the pack can show labels and revise answers.
    answered_questions: list[QuestionDefinition] = Field(default_factory=list)
    # Format hints the import tool of a current file question accepts
    # ("auto" first); empty for other questions.
    upload_formats: list[str] = Field(default_factory=list)


class AssessmentSession(AssessmentModel):
    session_id: str
    domain_id: str
    domain_version: str
    domain_content_hash: str
    locale: str
    participant_id: str | None = None
    revision: int = Field(default=0, ge=0)
    status: AssessmentStatus = "in_progress"
    current_question_id: str | None = None
    answers: dict[str, AssessmentAnswer] = Field(default_factory=dict)
    chat_messages: list[AssessmentChatMessage] = Field(default_factory=list)
    events: list[AssessmentEvent] = Field(default_factory=list)
    result: AssessmentResult | None = None
    report: AssessmentReport | None = None
    # The first analysis of the advisory dataset (pack block tools.after_import),
    # run right after the import, and the proposals derived from it.
    tool_results: list[ToolResult] = Field(default_factory=list)
    proposals: dict[str, DerivedProposal] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        domain_id: str,
        domain_version: str,
        domain_content_hash: str,
        locale: str,
    ) -> "AssessmentSession":
        return cls(
            session_id=f"sess_{uuid4().hex}",
            domain_id=domain_id,
            domain_version=domain_version,
            domain_content_hash=domain_content_hash,
            locale=locale,
            events=[AssessmentEvent(event_type="session_started")],
        )
