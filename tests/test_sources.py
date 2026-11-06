from datetime import UTC, datetime

from guideos.assessment.models import RecommendationResult
from guideos.context.models import AdvisoryContext, UserFact
from guideos.context.sources import (
    LLM_SOURCE,
    render_source_line,
    resolve_sources,
    unverified_values,
)
from guideos.rules.engine import RuleMetric
from guideos.knowledge.models import RetrievedPassage, SourceRef


FETCHED = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def test_cited_ids_resolve_to_documents_web_pages_rules_and_user_facts():
    sources = resolve_sources(["D1", "W1", "volunteer-recruiting", "funding_source"], _context(), _passages())

    assert [s.kind for s in sources] == ["document", "web", "rule", "user_context"]
    document, web, rule, fact = sources
    assert (document.title, document.locator, document.reference) == ("Leitfaden", "Seite 3", "doc_1")
    assert document.content_hash == "a" * 64 and document.excerpt == "Vereine können Förderungen beantragen."
    assert (web.url, web.retrieved_at) == ("https://www.example.at/vereinsfoerderung", FETCHED)
    assert (rule.title, rule.reference) == ("Ehrenamt sichern", "volunteer-recruiting")
    assert (fact.title, fact.reference) == ("Woher stammt der Großteil der Einnahmen?", "funding_source")


def test_sections_without_a_rule_or_knowledge_source_are_marked_as_llm_knowledge():
    context = _context()

    assert resolve_sources([], context, _passages()) == [LLM_SOURCE]
    assert resolve_sources(["D9", "erfunden"], context, _passages()) == [LLM_SOURCE]
    assert [s.kind for s in resolve_sources(["funding_source"], context, _passages())] == [
        "user_context",
        "llm",
    ]
    assert LLM_SOURCE == SourceRef(kind="llm", title="KI-Fachwissen")


def test_cited_ids_are_normalized_and_deduplicated():
    sources = resolve_sources([" [d1] ", "D1", "W1."], _context(), _passages())

    assert [s.reference for s in sources] == ["doc_1", "doc_2"]


def test_long_passages_are_stored_as_a_short_excerpt():
    passage = _passages()[0].model_copy(update={"text": "Wort " * 300})

    excerpt = passage.to_source().excerpt

    assert len(excerpt) <= 502 and excerpt.endswith(" …")


def test_source_line_names_every_source_in_german():
    sources = resolve_sources(["D1", "W1", "volunteer-recruiting", "funding_source"], _context(), _passages())

    assert render_source_line(sources) == (
        "_Quelle: Dokument „Leitfaden“, Seite 3"
        " | Website „Vereinsförderung“ (https://www.example.at/vereinsfoerderung, abgerufen am 30.09.2026)"
        " | GuideOS-Regel „Ehrenamt sichern“"
        " | Deine Angabe „Woher stammt der Großteil der Einnahmen?“_"
    )
    assert render_source_line([LLM_SOURCE]) == "_Quelle: KI-Fachwissen_"


def _passages():
    return [
        RetrievedPassage(
            source_id="D1", kind="document", document_id="doc_1", title="Leitfaden",
            locator="Seite 3", content_hash="a" * 64, retrieved_at=FETCHED,
            text="Vereine können Förderungen beantragen.", score=2.0,
        ),
        RetrievedPassage(
            source_id="W1", kind="web", document_id="doc_2", title="Vereinsförderung",
            url="https://www.example.at/vereinsfoerderung", content_hash="b" * 64,
            retrieved_at=FETCHED, text="Das Land fördert Vereinsprojekte.", score=1.0,
        ),
    ]


def _context():
    return AdvisoryContext(
        domain_id="verein-demo",
        domain_version="1.2.0",
        domain_name="Vereins-Check",
        facts=[
            UserFact(
                field_key="funding_source",
                question_text="Woher stammt der Großteil der Einnahmen?",
                normalized_value="public_grants",
            )
        ],
        rule_recommendations=[
            RecommendationResult(
                recommendation_id="volunteer-recruiting",
                priority=1,
                reason_codes=["VOLUNTEERS_AT_RISK"],
                evidence={"volunteer_situation": "tight"},
                title="Ehrenamt sichern",
                message="Plane die Nachbesetzung.",
            )
        ],
    )


def test_uncited_amounts_are_verified_by_the_users_own_answers_and_rule_metrics():
    context = AdvisoryContext(
        domain_id="verein-demo",
        domain_version="1.2.0",
        domain_name="Vereins-Check",
        facts=[
            UserFact(
                field_key="annual_costs",
                question_text="Wie hoch sind deine jährlichen Kosten in Euro?",
                normalized_value=1200,
                display_value="1200",
            )
        ],
        rule_metrics=[
            RuleMetric(
                rule_id="high-costs", reason_code="HIGH_COSTS", field_key="annual_costs",
                value=1200, operator="gte", threshold=1000, matched=True,
            )
        ],
    )
    text = "Mit deinen Kosten von 1.200 Euro liegst du über 1000 €, nicht bei 500 €."

    assert unverified_values(text, [], context, _passages()) == ["500"]
    # A bare number in a retrieved passage still does not verify a price.
    assert unverified_values("Das kostet 3 €.", [], context, [
        RetrievedPassage(
            source_id="D1", kind="document", document_id="doc_1", title="Leitfaden",
            locator="Seite 3", content_hash="a" * 64, retrieved_at=FETCHED,
            text="Es gibt 3 Varianten.", score=2.0,
        )
    ]) == ["3"]
