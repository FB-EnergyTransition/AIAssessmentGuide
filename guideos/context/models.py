from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from guideos.assessment.models import RecommendationResult
from guideos.rules.engine import RuleMetric
from guideos.tools.models import DatasetRef, ToolResult


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserFact(ContextModel):
    field_key: str
    question_id: str | None = None
    question_text: str | None = None
    normalized_value: Any
    display_value: str | None = None
    raw_answer: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class TopicRef(ContextModel):
    topic_id: str
    label: str


class TopicPriorityRef(ContextModel):
    topic_id: str
    priority: int
    rule_id: str


class UserFeedback(ContextModel):
    recommendation_id: str
    title: str
    stance: str


class AdvisoryContext(ContextModel):
    domain_id: str
    domain_version: str
    domain_name: str
    domain_guidance: str | None = None
    participant_id: str | None = None
    facts: list[UserFact] = Field(default_factory=list)
    assessment_session_id: str | None = None
    profile_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    rule_recommendations: list[RecommendationResult] = Field(default_factory=list)
    rule_metrics: list[RuleMetric] = Field(default_factory=list)
    feedback: list[UserFeedback] = Field(default_factory=list)
    topics: list[TopicRef] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    # topic_id -> rule_id of the exclude rule that matched first
    excluding_rules: dict[str, str] = Field(default_factory=dict)
    topic_priorities: list[TopicPriorityRef] = Field(default_factory=list)
    # The participant's dataset on a tool server and the tool results the model
    # may cite as T1, T2 … (position = number).
    dataset: DatasetRef | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
