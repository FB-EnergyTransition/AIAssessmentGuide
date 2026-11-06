from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from guideos.assessment.models import (
    AssessmentResult,
    AssessmentSession,
)
from guideos.domains.models import DomainPack, RuleDefinition
from guideos.recommendations.engine import build_recommendations
from guideos.tools.pointers import compare, numeric, resolve_pointer


class _HasNormalizedValue(Protocol):
    normalized_value: Any


class AnswerSource(Protocol):
    """Anything that carries answers by field key, such as a session or a
    profile."""

    @property
    def answers(self) -> Mapping[str, _HasNormalizedValue]: ...


class RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleMatch(RuleModel):
    rule_id: str
    field_key: str
    reason_code: str
    recommendation_ids: list[str]
    evidence_value: Any


class TopicExclusion(RuleModel):
    topic_id: str
    rule_id: str


class TopicPriority(RuleModel):
    topic_id: str
    priority: int
    rule_id: str


class RuleEvaluation(RuleModel):
    reason_codes: list[str]
    matches: list[RuleMatch]
    # Matched exclude/prioritize rules for the knowledge recommendations.
    excluded_topics: list[TopicExclusion] = []
    topic_priorities: list[TopicPriority] = []


class RuleMetric(RuleModel):
    rule_id: str
    reason_code: str
    field_key: str
    divisor_field_key: str | None = None
    value: Any
    operator: str
    threshold: Any
    matched: bool
    # Metric rules: the tool result the value comes from.
    tool: str | None = None
    pointer: str | None = None
    label: str | None = None
    calculation_id: str | None = None


class MetricEvidence(RuleModel):
    """The evidence of a metric rule match: which figure of which calculation
    was compared (kept as the evidence value of the recommendation)."""

    metric: str
    label: str | None = None
    tool: str
    value: Any
    calculation_id: str | None = None


def evaluate_assessment(
    pack: DomainPack,
    session: AssessmentSession,
) -> AssessmentResult:
    evaluation = evaluate_rules(pack, session)
    recommendations = build_recommendations(pack, evaluation, tool_outputs(session))
    return AssessmentResult(
        reason_codes=evaluation.reason_codes,
        recommendations=recommendations,
    )


def evaluate_rules(
    pack: DomainPack,
    session: AssessmentSession,
) -> RuleEvaluation:
    values = {
        field_key: answer.normalized_value
        for field_key, answer in session.answers.items()
    }
    outputs = tool_outputs(session)
    matched_rules = [
        rule for rule in pack.rules if _matches(rule, values, outputs)
    ]
    reason_codes = [rule.reason_code for rule in matched_rules]
    excluded_topics = [
        TopicExclusion(topic_id=topic, rule_id=rule.rule_id)
        for rule in matched_rules
        if rule.kind == "exclude"
        for topic in rule.topics
    ]
    topic_priorities = [
        TopicPriority(topic_id=topic, priority=rule.priority or 1, rule_id=rule.rule_id)
        for rule in matched_rules
        if rule.kind == "prioritize"
        for topic in rule.topics
    ]
    matched_rules = [rule for rule in matched_rules if rule.kind == "recommend"]
    matches = [
        RuleMatch(
            rule_id=rule.rule_id,
            field_key=rule.field_key,
            reason_code=rule.reason_code,
            recommendation_ids=rule.recommendation_ids,
            evidence_value=(
                _metric_evidence(rule, outputs).model_dump(mode="json")
                if rule.metric is not None else values.get(rule.field_key)
            ),
        )
        for rule in matched_rules
    ]
    return RuleEvaluation(
        reason_codes=reason_codes,
        matches=matches,
        excluded_topics=excluded_topics,
        topic_priorities=topic_priorities,
    )


def evaluate_rule_metrics(
    pack: DomainPack,
    source: AssessmentSession | AnswerSource,
) -> list[RuleMetric]:
    """The compared value and threshold of every lt/gte rule that can be
    computed from the answers of a session or a profile, whether the rule
    matched or not."""
    values = {
        field_key: answer.normalized_value
        for field_key, answer in source.answers.items()
    }
    outputs = tool_outputs(source)
    metrics = []
    for rule in pack.rules:
        if rule.operator not in ("lt", "gte"):
            continue
        if rule.metric is not None:
            found = _metric_value(rule, outputs)
            if found is None:
                continue
            value, calculation_id = found
            metrics.append(
                RuleMetric(
                    rule_id=rule.rule_id,
                    reason_code=rule.reason_code,
                    field_key=rule.field_key,
                    value=value,
                    operator=rule.operator,
                    threshold=rule.value,
                    matched=_matches(rule, values, outputs),
                    tool=rule.metric.tool,
                    pointer=rule.metric.pointer,
                    label=rule.metric.label,
                    calculation_id=calculation_id,
                )
            )
            continue
        value = _rule_value(rule, values)
        if not _comparable(value, rule.value):
            continue
        metrics.append(
            RuleMetric(
                rule_id=rule.rule_id,
                reason_code=rule.reason_code,
                field_key=rule.field_key,
                divisor_field_key=rule.divisor_field_key,
                value=round(value, 1) if isinstance(value, float) else value,
                operator=rule.operator,
                threshold=rule.value,
                matched=_matches(rule, values, outputs),
            )
        )
    return metrics


def tool_outputs(source: Any) -> dict[str, tuple[dict[str, Any], str | None]]:
    """The structured output and calculation id of the last result per tool
    on a session or profile (empty for sources without tool results)."""
    outputs: dict[str, tuple[dict[str, Any], str | None]] = {}
    for result in getattr(source, "tool_results", None) or []:
        outputs[result.call.name] = (result.structured_content or {}, result.calculation_id)
    return outputs


def _metric_value(rule: RuleDefinition, outputs) -> tuple[Any, str | None] | None:
    """The figure a metric rule compares, as a number when it is one, with the
    calculation id; None when the tool result or the figure is missing."""
    assert rule.metric is not None
    if rule.metric.tool not in outputs:
        return None
    structured, calculation_id = outputs[rule.metric.tool]
    raw = resolve_pointer(structured, rule.metric.pointer)
    if raw is None:
        return None
    number = numeric(raw)
    return (number if number is not None else raw), calculation_id


def _metric_evidence(rule: RuleDefinition, outputs) -> MetricEvidence:
    assert rule.metric is not None
    found = _metric_value(rule, outputs)
    value, calculation_id = found if found is not None else (None, None)
    return MetricEvidence(metric=rule.metric.pointer, label=rule.metric.label, tool=rule.metric.tool,
                          value=value, calculation_id=calculation_id)


def _rule_value(rule: RuleDefinition, values: dict[str, Any]) -> Any:
    """The value a rule compares: the answer or its ratio to the divisor
    field; None when the ratio cannot be computed."""
    value = values.get(rule.field_key)
    if rule.divisor_field_key:
        divisor = values.get(rule.divisor_field_key)
        if not _is_number(value) or not _is_number(divisor) or divisor <= 0:
            return None
        return value / divisor
    return value


def _matches(rule: RuleDefinition, values: dict[str, Any], outputs=None) -> bool:
    if rule.metric is not None:
        found = _metric_value(rule, outputs or {})
        return found is not None and compare(found[0], rule.operator, rule.value)
    value = values.get(rule.field_key)
    if rule.operator == "is_missing":
        return rule.field_key not in values or value is None
    if rule.divisor_field_key:
        value = _rule_value(rule, values)
        if value is None:
            return False
    if rule.operator == "equals":
        return value == rule.value
    if rule.operator == "in":
        return value in rule.value
    if rule.operator == "contains":
        return isinstance(value, list) and rule.value in value
    if rule.operator == "contains_any":
        return isinstance(value, list) and any(item in value for item in rule.value)
    if not _comparable(value, rule.value):
        return False
    if rule.operator == "lt":
        return value < rule.value
    if rule.operator == "gte":
        return value >= rule.value
    return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _comparable(value: Any, expected: Any) -> bool:
    """Ordered comparison only between two numbers or two strings (ISO dates)."""
    if _is_number(value) and _is_number(expected):
        return True
    return isinstance(value, str) and isinstance(expected, str)
