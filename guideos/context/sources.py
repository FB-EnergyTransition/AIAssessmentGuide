import re
from collections.abc import Sequence

from guideos.context.models import AdvisoryContext
from guideos.context.verification import (
    context_texts,
    tool_result_texts,
    unverified_amounts,
    unverified_numbers,
)
from guideos.domains.models import format_date_de
from guideos.knowledge.models import RetrievedPassage, SourceRef


LLM_SOURCE = SourceRef(kind="llm", title="KI-Fachwissen")
# Kinds that back a statement with something other than the model's own knowledge.
GROUNDING_KINDS = {"rule", "document", "web", "tool"}
_TOOL_ID = re.compile(r"^T(\d+)$", re.IGNORECASE)
TOOL_EXCERPT_LENGTH = 500


def tool_source(result) -> SourceRef:
    excerpt = result.summary
    if len(excerpt) > TOOL_EXCERPT_LENGTH:
        excerpt = excerpt[:TOOL_EXCERPT_LENGTH].rstrip() + " …"
    return SourceRef(kind="tool", title=result.call.name, reference=result.calculation_id,
                     excerpt=excerpt or None)


def tool_result_for(source_id: str, context: AdvisoryContext):
    """The tool result a T<n> id names in the context, None for other ids."""
    return _tool_result(source_id.strip(" []().,;"), context)


def _tool_result(source_id: str, context: AdvisoryContext):
    match = _TOOL_ID.match(source_id)
    if match is None:
        return None
    index = int(match.group(1)) - 1
    return context.tool_results[index] if 0 <= index < len(context.tool_results) else None


def resolve_sources(
    source_ids: Sequence[str],
    context: AdvisoryContext,
    passages: Sequence[RetrievedPassage],
) -> list[SourceRef]:
    """Turn the ids a model cited into provenance records. Unknown ids are
    dropped; whatever is not backed by a rule, document or web page is marked
    as the model's own knowledge."""
    by_passage = {passage.source_id: passage for passage in passages}
    rules = {item.recommendation_id: item for item in context.rule_recommendations}
    facts = {fact.field_key: fact for fact in context.facts}
    sources: list[SourceRef] = []
    seen = set()
    for raw_id in source_ids:
        source_id = raw_id.strip(" []().,;")
        if source_id.upper() in by_passage:
            source_id = source_id.upper()
        tool_result = _tool_result(source_id, context)
        if tool_result is not None:
            source_id = source_id.upper()
        if source_id in seen:
            continue
        seen.add(source_id)
        if tool_result is not None:
            sources.append(tool_source(tool_result))
        elif source_id in by_passage:
            sources.append(by_passage[source_id].to_source())
        elif source_id in rules:
            sources.append(
                SourceRef(kind="rule", title=rules[source_id].title, reference=source_id)
            )
        elif source_id in facts:
            sources.append(
                SourceRef(
                    kind="user_context",
                    title=facts[source_id].question_text or source_id,
                    reference=source_id,
                )
            )
    if not any(source.kind in GROUNDING_KINDS for source in sources):
        sources.append(LLM_SOURCE)
    return sources


def unverified_values(
    text: str,
    source_ids: Sequence[str],
    context: AdvisoryContext,
    passages: Sequence[RetrievedPassage],
    extra_texts: Sequence[str] = (),
) -> list[str]:
    """Figures in a statement that cites a document or web page but that occur
    neither in the cited passages, nor in the session context, nor in
    extra_texts (the user's own messages; never the model's earlier answers).
    A statement without such a citation is labelled as the model's own
    knowledge; there only money amounts are reported, when no retrieved
    passage or user message states them as an amount and the session context
    does not contain the figure."""
    cited_ids = {raw_id.strip(" []().,;").upper() for raw_id in source_ids}
    cited = [passage.text for passage in passages if passage.source_id in cited_ids]
    cited_tools = [result for raw_id in source_ids
                   for result in [_tool_result(raw_id.strip(" []().,;"), context)] if result is not None]
    cited.extend(tool_result_texts(cited_tools))
    facts = context_texts(context)  # includes every tool result of the context
    if not cited:
        return unverified_amounts(
            text, [*(passage.text for passage in passages), *extra_texts], facts
        )
    return unverified_numbers(text, [*cited, *facts, *extra_texts])


def cites_knowledge(sources: Sequence[SourceRef]) -> bool:
    return any(source.kind in ("document", "web", "tool") for source in sources)


def unverified_note(values: Sequence[str], cited: bool = True) -> str:
    single = len(values) == 1
    if cited:
        noun, verb = ("Zahlenangabe", "steht") if single else ("Zahlenangaben", "stehen")
        return f"{noun} {', '.join(values)} {verb} nicht in der zitierten Quelle"
    noun, verb = ("Geldbetrag", "ist") if single else ("Geldbeträge", "sind")
    return f"{noun} {', '.join(values)} {verb} durch keine Quelle belegt"


def render_unverified_line(values: Sequence[str], cited: bool = True) -> str:
    article = "Der" if len(values) == 1 and not cited else "Die"
    return f"_Ungeprüft: {article} {unverified_note(values, cited)}._"


def describe_source(source: SourceRef) -> str:
    if source.kind == "llm":
        return source.title
    if source.kind == "rule":
        return f"GuideOS-Regel „{source.title}“"
    if source.kind == "user_context":
        return f"Deine Angabe „{source.title}“"
    if source.kind == "tool":
        return f"Berechnung „{source.title}“"
    label = "Dokument" if source.kind == "document" else "Website"
    text = f"{label} „{source.title}“"
    if source.locator:
        text += f", {source.locator}"
    if source.kind == "web" and source.url:
        fetched = (
            f", abgerufen am {format_date_de(source.retrieved_at.date().isoformat())}"
            if source.retrieved_at
            else ""
        )
        text += f" ({source.url}{fetched})"
    return text


def render_source_line(sources: Sequence[SourceRef]) -> str:
    return "_Quelle: " + " | ".join(describe_source(source) for source in sources) + "_"
