from __future__ import annotations

from typing import TYPE_CHECKING, Any

from guideos.assessment.models import RecommendationResult, ResolvedFigure
from guideos.domains.models import PLACEHOLDER, DomainPack, RecommendationDefinition
from guideos.tools.pointers import format_figure, resolve_pointer

if TYPE_CHECKING:
    from guideos.rules.engine import RuleEvaluation, RuleMatch

ToolOutputs = dict[str, tuple[dict[str, Any], str | None]]


def build_recommendations(
    pack: DomainPack,
    evaluation: RuleEvaluation,
    outputs: ToolOutputs | None = None,
) -> list[RecommendationResult]:
    """The catalog entries the matched rules name, in priority order. outputs
    (the last structured output and calculation id per tool, see
    rules.engine.tool_outputs) fills the figures of entries that declare them;
    without every figure the generic message stands."""
    recommendation_matches: dict[str, list[RuleMatch]] = {}
    for match in evaluation.matches:
        for recommendation_id in match.recommendation_ids:
            recommendation_matches.setdefault(recommendation_id, []).append(match)

    definitions = {
        item.recommendation_id: item for item in pack.recommendations
    }
    recommendations = []
    for recommendation_id, matches in recommendation_matches.items():
        definition = definitions[recommendation_id]
        message, figures = resolve_figures(definition, outputs or {})
        recommendations.append(
            RecommendationResult(
                recommendation_id=recommendation_id,
                priority=definition.priority,
                reason_codes=[match.reason_code for match in matches],
                evidence={
                    match.field_key: match.evidence_value for match in matches
                },
                title=definition.title,
                message=message,
                source_reference=definition.source_reference,
                figures=figures,
            )
        )
    recommendations.sort(key=lambda item: item.priority)
    return recommendations


def resolve_figures(
    definition: RecommendationDefinition, outputs: ToolOutputs
) -> tuple[str, dict[str, ResolvedFigure]]:
    """The text with every placeholder filled from the tool outputs, or the
    generic message (and no figures) when any figure is missing."""
    if definition.figures is None or definition.message_with_figures is None:
        return definition.message, {}
    figures: dict[str, ResolvedFigure] = {}
    for name, source in definition.figures.items():
        if source.tool not in outputs:
            return definition.message, {}
        structured, calculation_id = outputs[source.tool]
        value = resolve_pointer(structured, source.pointer)
        text = format_figure(value, source.decimals, source.map)
        if text is None:
            return definition.message, {}
        figures[name] = ResolvedFigure(value=value, text=text, tool=source.tool, calculation_id=calculation_id)
    message = PLACEHOLDER.sub(lambda match: figures[match.group(1)].text, definition.message_with_figures)
    return message, figures
