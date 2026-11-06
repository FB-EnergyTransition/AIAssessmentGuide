from __future__ import annotations

from typing import Any

from guideos.assessment.models import (
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
    LLMRecommendation,
)
from guideos.context.sources import cites_knowledge, describe_source, unverified_note
from guideos.domains.models import DomainPack, advisory_file_question, display_value, format_date_de
from guideos.profiles.models import Profile, RecommendationFeedback
from guideos.tools.models import DatasetRef

FEEDBACK_LABELS = {"accepted": "Vom Nutzer befürwortet", "rejected": "Vom Nutzer abgelehnt"}
OTHER_TOPIC_LABEL = "Sonstiges"
EXCLUDED_HEADING = "## Nicht berücksichtigt"

KNOWLEDGE_HEADING = "## Hinweise aus Wissensquellen (KI-formuliert)"
LLM_KNOWLEDGE_HEADING = "### Allgemeines KI-Fachwissen"


DISCLAIMER = "> Hinweis: Dieser technische Demo-Check ist keine verbindliche Beratung."


def render_markdown_report(
    pack: DomainPack,
    session: AssessmentSession,
    result: AssessmentResult,
) -> AssessmentReport:
    values = {key: answer.normalized_value for key, answer in session.answers.items()}
    notes = {
        key: MEASUREMENT_NOTE for key, answer in session.answers.items() if answer.origin == "measurement"
    }
    lines = [
        f"# {pack.name}",
        "",
        DISCLAIMER,
        "",
        f"Session: `{session.session_id}`",
        f"Domain-Version: `{pack.domain_id}/{pack.version}`",
        "",
        *_answer_lines(pack, values, notes),
        *_result_lines(pack, values, result),
        *_calculation_lines(session, _session_dataset(pack, session)),
    ]
    return AssessmentReport(content="\n".join(lines).rstrip() + "\n")


MEASUREMENT_NOTE = " (aus den Messdaten abgeleitet)"


def _session_dataset(pack: DomainPack, session: AssessmentSession) -> DatasetRef | None:
    question = advisory_file_question(pack)
    answer = session.answers.get(question.field_key) if question is not None else None
    value = answer.normalized_value if answer is not None else None
    if isinstance(value, dict) and value.get("dataset_id"):
        return DatasetRef.model_validate(value)
    return None


def render_profile_report(pack: DomainPack, profile: Profile) -> AssessmentReport:
    """The current profile of a participant in a domain: answers with the
    date of a later change, and both recommendation kinds of its result."""
    values = {key: answer.normalized_value for key, answer in profile.answers.items()}
    notes = {
        key: (
            f" (geändert am {format_date_de(answer.updated_at.date().isoformat())})"
            if answer.origin == "chat_update" else MEASUREMENT_NOTE
        )
        for key, answer in profile.answers.items()
        if answer.origin in ("chat_update", "measurement")
    }
    lines = [
        f"# {pack.name}",
        "",
        DISCLAIMER,
        "",
        f"Profil: `{profile.profile_id}`",
        f"Domain-Version: `{pack.domain_id}/{pack.version}`",
        f"Quelle: Assessment `{profile.source_session_id}`",
        f"Stand: {format_date_de(profile.updated_at.date().isoformat())}",
        "",
        *_answer_lines(pack, values, notes),
        *_result_lines(pack, values, profile.result, profile.feedback),
        *_calculation_lines(profile, profile.dataset),
    ]
    return AssessmentReport(content="\n".join(lines).rstrip() + "\n")


CALCULATIONS_HEADING = "## Berechnungen"


def _calculation_lines(source: Profile | AssessmentSession, dataset: DatasetRef | None) -> list[str]:
    """Dataset and the last result per tool, so the report shows what the
    advisory computed and from which data."""
    if dataset is None and not source.tool_results:
        return []
    lines = ["", CALCULATIONS_HEADING, ""]
    if dataset is not None:
        lines.append(
            f"Messdaten: `{dataset.filename}`, importiert am "
            f"{format_date_de(dataset.imported_at.date().isoformat())} "
            f"(Datensatz `{dataset.dataset_id}`, Hash `{dataset.content_hash[:12]}`, "
            f"Server `{dataset.server_name}`)."
        )
        if dataset.summary:
            lines.append(f"{dataset.summary}")
    for result in source.tool_results:
        arguments = ", ".join(f"{key}={value}" for key, value in result.call.arguments.items()) or "ohne Argumente"
        provenance = (result.structured_content or {}).get("provenance") or {}
        lines.append(
            f"- **{result.call.name}** ({arguments}): {result.summary or 'ohne Zusammenfassung'}"
            + (f" (Berechnung `{result.calculation_id}`" if result.calculation_id else "")
            + (f", Kern {provenance.get('core_version')}" if provenance.get("core_version") else "")
            + (")" if result.calculation_id else "")
        )
        for key, label in (("assumptions", "Annahmen"), ("limitations", "Grenzen")):
            items = provenance.get(key)
            if items:
                lines.append(f"  - {label}: " + "; ".join(str(item) for item in items))
    return lines


def render_profile_summary(pack: DomainPack, profile: Profile) -> str:
    """Deterministic chat-style view of a profile: answers in pack order
    and both recommendation kinds, no model call."""
    values = {key: answer.normalized_value for key, answer in profile.answers.items()}
    lines = [
        *_answer_lines(pack, values),
        "",
        "## Empfehlungen",
        "",
        render_rule_recommendations(pack, profile.result, profile.feedback),
    ]
    if profile.result.knowledge_recommendations:
        lines.extend([
            "",
            render_llm_recommendations(
                pack, profile.result.knowledge_recommendations, feedback=profile.feedback
            ),
        ])
    return "\n".join(lines).rstrip()


def _answer_lines(
    pack: DomainPack, values: dict[str, Any], notes: dict[str, str] | None = None
) -> list[str]:
    lines = ["## Angaben", ""]
    for question in pack.questions:
        if question.field_key not in values:
            continue
        lines.append(
            f"- **{question.text}**: "
            f"{display_value(question, values[question.field_key])}"
            f"{(notes or {}).get(question.field_key, '')}"
        )
    return lines


def _feedback_line(
    feedback: dict[str, RecommendationFeedback] | None, recommendation_id: str
) -> str | None:
    item = (feedback or {}).get(recommendation_id)
    return FEEDBACK_LABELS[item.stance] if item else None


def _result_lines(
    pack: DomainPack,
    values: dict[str, Any],
    result: AssessmentResult,
    feedback: dict[str, RecommendationFeedback] | None = None,
) -> list[str]:
    lines = ["", "## Empfehlungen", ""]
    if not result.recommendations:
        lines.append(
            "Aus den Demo-Regeln wurden keine konkreten Empfehlungen abgeleitet."
        )
    for recommendation in result.recommendations:
        lines.extend(
            [
                f"### {recommendation.title}",
                "",
                recommendation.message,
                "",
                "- Gründe: "
                + ", ".join(f"`{code}`" for code in recommendation.reason_codes),
                "- Bezug: " + "; ".join(describe_evidence(pack, recommendation.evidence)),
            ]
        )
        if recommendation.source_reference:
            lines.append(f"- Quelle: {recommendation.source_reference}")
        if (note := _feedback_line(feedback, recommendation.recommendation_id)) is not None:
            lines.append(f"- Rückmeldung: {note}")
        lines.append("")
    lines.extend(_knowledge_section(pack, values, result, feedback))
    lines.extend(_excluded_section(pack, result))
    return lines


def topic_label(pack: DomainPack, topic: str | None) -> str | None:
    """Label of a knowledge recommendation's topic from the pack catalog;
    "Sonstiges" for the model's fallback "other"; None without a topic."""
    if topic is None:
        return None
    labels = {item.topic_id: item.label for item in pack.topics or []}
    return labels.get(topic, OTHER_TOPIC_LABEL)


def _excluded_section(pack: DomainPack, result: AssessmentResult) -> list[str]:
    """What GuideOS left out on purpose, so the user sees why a topic is
    missing: knowledge recommendations dropped by an exclude rule of the
    pack or because the participant rejected them earlier."""
    if not result.excluded:
        return []
    lines = [EXCLUDED_HEADING, "", "Diese Hinweise hat GuideOS absichtlich weggelassen:", ""]
    for item in result.excluded:
        if item.trigger == "rule":
            label = topic_label(pack, item.topic)
            topic = f" (Thema {label})" if label else ""
            lines.append(f"- {item.title}{topic}: ausgelassen durch Regel `{item.rule_id}`")
        else:
            lines.append(f"- {item.title}: von dir zuvor abgelehnt")
    lines.append("")
    return lines


def _knowledge_section(
    pack: DomainPack,
    values: dict[str, Any],
    result: AssessmentResult,
    feedback: dict[str, RecommendationFeedback] | None = None,
) -> list[str]:
    """Knowledge recommendations after the rule recommendations: cited ones
    first, the model's own knowledge under its own heading, and a note when
    the generation failed. Nothing when none were generated."""
    generation = result.knowledge_generation
    if generation is None:
        return []
    lines = [KNOWLEDGE_HEADING, ""]
    if generation.error_code is not None:
        lines.append(
            "Die Hinweise aus Wissensquellen konnten nicht erzeugt werden "
            f"(Code `{generation.error_code}`)."
        )
        return lines
    lines.append(
        "> Hinweis: Diese Hinweise hat ein KI-Modell aus den Angaben und den "
        "Wissensquellen formuliert; sie sind keine GuideOS-Regeln."
    )
    lines.append("")
    cited = [item for item in result.knowledge_recommendations if cites_knowledge(item.sources)]
    uncited = [item for item in result.knowledge_recommendations if not cites_knowledge(item.sources)]
    if not cited and not uncited:
        lines.append("Das Modell hat keine zusätzlichen Hinweise geliefert.")
    for item in cited:
        lines.extend(_knowledge_item(pack, values, item, feedback))
    if uncited:
        lines.extend([LLM_KNOWLEDGE_HEADING, ""])
        for item in uncited:
            lines.extend(_knowledge_item(pack, values, item, feedback))
    lines.append(
        f"Modell: {generation.model} · Prompt-Version {generation.prompt_version} · "
        f"Stand {generation.generated_at.date().isoformat()}"
    )
    return lines


def _knowledge_item(
    pack: DomainPack,
    values: dict[str, Any],
    item: LLMRecommendation,
    feedback: dict[str, RecommendationFeedback] | None = None,
) -> list[str]:
    lines = [f"### {item.title}", "", item.message, "", item.rationale, ""]
    if (label := topic_label(pack, item.topic)) is not None:
        lines.append(f"- Thema: {label}")
    if item.evidence_fields:
        evidence = {
            field_key: values[field_key]
            for field_key in item.evidence_fields
            if field_key in values
        }
        lines.append("- Bezug: " + "; ".join(describe_evidence(pack, evidence)))
    cited = [source for source in item.sources if source.kind != "llm"]
    lines.append(
        "- Quelle: " + (" | ".join(describe_source(s) for s in cited) if cited else "KI-Fachwissen")
    )
    if item.unverified_values:
        lines.append(f"- Ungeprüft: {unverified_note(item.unverified_values, bool(cited))}")
    if (note := _feedback_line(feedback, item.recommendation_id)) is not None:
        lines.append(f"- Rückmeldung: {note}")
    lines.append("")
    return lines


def describe_evidence(pack: DomainPack, evidence: dict[str, object]) -> list[str]:
    """Human-readable answer references for a recommendation, in rule-match order."""
    questions = {question.field_key: question for question in pack.questions}
    described = []
    for field_key, value in evidence.items():
        question = questions.get(field_key)
        if isinstance(value, dict) and "metric" in value and "tool" in value:
            label = value.get("label") or value["metric"]
            described.append(f"{label}: {_metric_text(value.get('value'))} (Berechnung {value['tool']})")
            continue
        if question is None:
            described.append(f"{field_key} ({value})")
            continue
        described.append(f"{question.text} ({display_value(question, value)})")
    return described


def _metric_text(value: object) -> str:
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, float):
        return f"{value:g}".replace(".", ",")
    return str(value)


def render_rule_recommendations(
    pack: DomainPack,
    result: AssessmentResult,
    feedback: dict[str, RecommendationFeedback] | None = None,
    compact: bool = False,
) -> str:
    """Chat message with the rule-based recommendations of a completed
    assessment: reason codes, answer references and the pack's source, if any.
    Compact (pack block result_chat): one line per item with title and text,
    the details stay in the report."""
    lines = []
    if not result.recommendations:
        lines.append("Aus den Regeln ergeben sich keine Empfehlungen.")
    if compact and result.recommendations:
        lines.append(f"**Empfehlungen aus deinen Angaben** ({len(result.recommendations)})")
        lines.append("")
        for recommendation in result.recommendations:
            line = f"- **{recommendation.title}** – {recommendation.message}"
            if (note := _feedback_line(feedback, recommendation.recommendation_id)) is not None:
                line += f" _(Rückmeldung: {note})_"
            lines.append(line)
        lines.append("")
        lines.append("_Details und Quellen stehen im Bericht; frag mich zu einer Empfehlung._")
        return "\n".join(lines)
    for recommendation in result.recommendations:
        lines.append(f"**{recommendation.title}**  ")
        lines.append(f"{recommendation.message}  ")
        lines.append(
            "_Quelle: GuideOS-Regel | Gründe: "
            + ", ".join(recommendation.reason_codes)
            + " | Bezug: "
            + "; ".join(describe_evidence(pack, recommendation.evidence))
            + "_"
        )
        if recommendation.source_reference:
            lines.append(f"_Fachquelle: {recommendation.source_reference}_")
        if (note := _feedback_line(feedback, recommendation.recommendation_id)) is not None:
            lines.append(f"_Rückmeldung: {note}_")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_llm_recommendations(
    pack: DomainPack,
    items: list[LLMRecommendation],
    heading: str = "Zusätzliche KI-Empfehlungen",
    feedback: dict[str, RecommendationFeedback] | None = None,
    compact: bool = False,
) -> str:
    """Chat message for LLM recommendations, clearly labelled as model
    output rather than GuideOS rules, with the knowledge-base passages they
    cite. Compact (pack block result_chat): one line per item with title and
    text; unverified figures stay marked, rationale and sources are in the
    report."""
    questions = {question.field_key: question.text for question in pack.questions}
    if compact and items:
        lines = [f"**{heading}** ({len(items)})", ""]
        for item in items:
            line = f"- **{item.title}** – {item.message}"
            if item.unverified_values:
                line += (f" _(Ungeprüft: "
                         f"{unverified_note(item.unverified_values, cites_knowledge(item.sources))})_")
            if (note := _feedback_line(feedback, item.recommendation_id)) is not None:
                line += f" _(Rückmeldung: {note})_"
            lines.append(line)
        lines.append("")
        lines.append("_KI-formuliert, keine GuideOS-Regel. Begründungen und Quellen stehen im "
                     "Bericht; frag mich zu einer Empfehlung._")
        return "\n".join(lines)
    lines = [f"**{heading}**", ""]
    if not items:
        lines.append("Das Modell hat keine zusätzlichen Empfehlungen geliefert.")
    for item in items:
        lines.append(f"**{item.title}**  ")
        lines.append(f"{item.message}  ")
        lines.append(f"{item.rationale}  ")
        cited = [source for source in item.sources if source.kind != "llm"]
        if cited:
            details = (
                "_Quelle: "
                + " | ".join(describe_source(source) for source in cited)
                + " (KI-formuliert, keine GuideOS-Regel)"
            )
        else:
            details = "_Quelle: KI-Fachwissen, keine GuideOS-Regel"
        if item.unverified_values:
            details += (
                f" | Ungeprüft: {unverified_note(item.unverified_values, cites_knowledge(item.sources))}"
            )
        if item.evidence_fields:
            details += " | Bezug: " + "; ".join(
                questions.get(field_key, field_key) for field_key in item.evidence_fields
            )
        if (label := topic_label(pack, item.topic)) is not None:
            details += f" | Thema: {label}"
        if item.confidence is not None:
            details += f" | Konfidenz: {item.confidence:.0%}"
        if (note := _feedback_line(feedback, item.recommendation_id)) is not None:
            details += f" | Rückmeldung: {note}"
        lines.append(details + "_")
        lines.append("")
    return "\n".join(lines).rstrip()
