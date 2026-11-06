import json
from datetime import UTC, datetime

import pytest

from guideos.assessment.contract import to_contract_response
from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.context import build_advisory_context
from guideos.domains.loader import load_domain_pack
from guideos.knowledge.models import RetrievedPassage, SourceRef
from guideos.providers.base import ProviderError
from guideos.recommendations.llm import (
    LLMRecommendation,
    LLMRecommendationBatch,
    LLMRecommendationError,
    LLMRecommendationGenerator,
)
from guideos.reports.renderer import render_llm_recommendations
from guideos.rules.engine import evaluate_assessment


class FakeStructuredProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.schema = None
        self.messages = None

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.schema = schema
        self.messages = messages
        if self.error:
            raise self.error
        return self.result


VALID_ITEM = {
    "recommendation_id": "hydraulic-balancing",
    "title": "Hydraulischen Abgleich prüfen",
    "message": "Ein hydraulischer Abgleich senkt bei Gasheizungen oft den Verbrauch.",
    "rationale": "Gasheizung in einem Gebäude von 1980 mit teilweiser Dämmung.",
    "evidence_fields": ["funding_source", "founded_year"],
    "confidence": 0.7,
}


def test_generator_returns_validated_llm_recommendations_with_provenance():
    provider = FakeStructuredProvider({"recommendations": [VALID_ITEM]})
    context = _context()

    recommendations = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", context
    )

    assert len(recommendations) == 1
    item = recommendations[0]
    assert isinstance(item, LLMRecommendation)
    assert item.source == "llm"
    assert item.recommendation_id == "hydraulic-balancing"
    assert item.evidence_fields == ["funding_source", "founded_year"]
    assert item.confidence == 0.7
    assert provider.schema == LLMRecommendationBatch.model_json_schema()
    system = provider.messages[0]["content"]
    assert "zusätzlich" in system and "Erfinde keine" in system
    payload = json.loads(provider.messages[1]["content"])
    assert {f["field_key"] for f in payload["verified_user_context"]["facts"]} >= {"funding_source"}
    assert [r["recommendation_id"] for r in payload["rule_results"]["recommendations"]] == [
        "collect-budget",
        "volunteer-recruiting",
        "funding-diversification",
        "club-consultation",
    ]


def test_generator_drops_rule_rewrites_and_unknown_evidence_fields():
    provider = FakeStructuredProvider(
        {
            "recommendations": [
                {**VALID_ITEM, "recommendation_id": "volunteer-recruiting"},
                {**VALID_ITEM, "evidence_fields": ["funding_source", "erfunden"]},
                {**VALID_ITEM, "recommendation_id": "hydraulic-balancing"},
            ]
        }
    )

    recommendations = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", _context()
    )

    assert [r.recommendation_id for r in recommendations] == ["hydraulic-balancing"]
    assert recommendations[0].evidence_fields == ["funding_source"]


def test_generator_fails_safely_on_malformed_or_failing_provider():
    with pytest.raises(LLMRecommendationError):
        LLMRecommendationGenerator(
            FakeStructuredProvider({"recommendations": [{"title": "nur Titel"}]})
        ).generate("http://fake", "fake", _context())
    with pytest.raises(LLMRecommendationError):
        LLMRecommendationGenerator(
            FakeStructuredProvider({"recommendations": [{**VALID_ITEM, "confidence": 1.5}]})
        ).generate("http://fake", "fake", _context())
    with pytest.raises(ProviderError):
        LLMRecommendationGenerator(
            FakeStructuredProvider(error=ProviderError("weg"))
        ).generate("http://fake", "fake", _context())


def test_llm_generation_leaves_deterministic_result_untouched(tmp_path):
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(tmp_path / "assessments.json")
    )
    response = orchestrator.start()
    for question_id, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        orchestrator.submit(response.session_id, question_id, value)
    completed = orchestrator.complete(response.session_id)
    session = orchestrator.repository.get(response.session_id)
    before = session.result.model_dump()

    LLMRecommendationGenerator(
        FakeStructuredProvider({"recommendations": [VALID_ITEM]})
    ).generate("http://fake", "fake", build_advisory_context(orchestrator.pack, session))

    assert orchestrator.repository.get(response.session_id).result.model_dump() == before
    assert all(r.source == "rule" for r in completed.result.recommendations)
    contract = to_contract_response(completed)
    assert {r.source for r in contract.result.recommendations} == {"rule"}


def _context():
    pack = load_domain_pack()
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash=pack.content_hash,
        locale="de",
    )
    for field_key, value in {
        "club_type": "sports",
        "founded_year": 1980,
        "members_count": 120,
        "funding_source": "public_grants",
        "volunteer_situation": "tight",
        "annual_budget_eur": None,
    }.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key,
            field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value,
            confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return build_advisory_context(pack, session)


def test_generator_prompt_requires_evidence_to_support_rationale():
    provider = FakeStructuredProvider({"recommendations": []})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    system = provider.messages[0]["content"]
    assert "evidence_fields" in system and "rationale" in system
    assert "tatsächlich" in system
    assert "Heizverbrauch" not in system


def test_generator_prompt_asks_to_omit_a_topic_instead_of_a_hint_on_missing_sources():
    provider = FakeStructuredProvider({"recommendations": []})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    system = provider.messages[0]["content"]
    assert "lass das Thema weg" in system
    assert "keine Empfehlung, deren Inhalt nur" in system


PASSAGE = RetrievedPassage(
    source_id="D1", kind="document", document_id="doc_1", title="Leitfaden Heizung",
    locator="Seite 3", content_hash="a" * 64, retrieved_at="2026-09-30T08:00:00Z",
    text="Ein hydraulischer Abgleich senkt den Verbrauch.", score=2.0,
)


def test_generator_passes_knowledge_passages_and_resolves_cited_sources():
    provider = FakeStructuredProvider(
        {
            "recommendations": [
                {**VALID_ITEM, "source_ids": ["D1", "D7"]},
                {**VALID_ITEM, "recommendation_id": "smart-thermostats", "source_ids": []},
            ]
        }
    )

    grounded, own = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", _context(), [PASSAGE]
    )

    payload = json.loads(provider.messages[1]["content"])
    assert payload["knowledge_sources"][0]["source_id"] == "D1"
    assert "Abgleich" in payload["knowledge_sources"][0]["text"]
    assert "KNOWLEDGE_SOURCES" in provider.messages[0]["content"]
    assert grounded.source == "llm" and grounded.source_ids == ["D1"]
    assert [(s.kind, s.title, s.locator) for s in grounded.sources] == [
        ("document", "Leitfaden Heizung", "Seite 3")
    ]
    assert own.source_ids == [] and [s.kind for s in own.sources] == ["llm"]


def test_generator_marks_recommendations_without_knowledge_base_as_llm_knowledge():
    provider = FakeStructuredProvider({"recommendations": [{**VALID_ITEM, "source_ids": ["D1"]}]})

    [item] = LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    assert json.loads(provider.messages[1]["content"])["knowledge_sources"] == []
    assert item.source_ids == [] and [s.kind for s in item.sources] == ["llm"]


def test_rendered_llm_recommendations_name_the_cited_document():
    provider = FakeStructuredProvider(
        {
            "recommendations": [
                {**VALID_ITEM, "source_ids": ["D1"]},
                {**VALID_ITEM, "recommendation_id": "smart-thermostats"},
            ]
        }
    )
    items = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", _context(), [PASSAGE]
    )

    text = render_llm_recommendations(load_domain_pack(), items)

    assert (
        "_Quelle: Dokument „Leitfaden Heizung“, Seite 3 (KI-formuliert, keine GuideOS-Regel)"
        " | Bezug: Woher stammt der Großteil der Einnahmen?" in text
    )
    assert "_Quelle: KI-Fachwissen, keine GuideOS-Regel | Bezug:" in text


def test_generator_passes_labels_and_rule_metrics():
    provider = FakeStructuredProvider({"recommendations": []})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    payload = json.loads(provider.messages[1]["content"])
    facts = {fact["field_key"]: fact for fact in payload["verified_user_context"]["facts"]}
    assert facts["funding_source"]["display_value"] == "Öffentliche Förderungen"
    assert [item["rule_id"] for item in payload["rule_results"]["metrics"]] == ["old-statutes"]


def test_generator_drops_recommendations_without_a_supporting_fact():
    provider = FakeStructuredProvider(
        {
            "recommendations": [
                {**VALID_ITEM, "recommendation_id": "general-tip", "evidence_fields": []},
                {**VALID_ITEM, "recommendation_id": "invented", "evidence_fields": ["erfunden"]},
                VALID_ITEM,
            ]
        }
    )

    recommendations = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", _context()
    )

    assert [r.recommendation_id for r in recommendations] == ["hydraulic-balancing"]


def test_generator_prompt_limits_hard_facts_to_sources_and_generic_tips():
    provider = FakeStructuredProvider({"recommendations": []})
    context = _context().model_copy(update={"domain_guidance": None})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", context)

    system = provider.messages[0]["content"]
    assert "überprüfbare Einzelheiten" in system and "aus dem Gedächtnis" in system
    assert "für jeden gelten" in system
    assert "display_value" in system and "metrics" in system
    assert "widerspricht" in system
    for term in ("Förder", "Heiz", "Verbrauch", "Energie", "Österreich"):
        assert term not in system
    assert "DOMAIN_GUIDANCE" not in system


def test_generator_appends_the_domain_guidance_of_the_pack():
    provider = FakeStructuredProvider({"recommendations": []})
    context = _context().model_copy(update={"domain_guidance": "Der Kontext ist Österreich."})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", context)

    system = provider.messages[0]["content"]
    assert "DOMAIN_GUIDANCE" in system and system.endswith("Der Kontext ist Österreich.")


def test_requested_schema_requires_evidence_fields():
    item_schema = LLMRecommendationBatch.model_json_schema()["$defs"]["GeneratedRecommendation"]

    assert "evidence_fields" in item_schema["required"]


def test_generator_marks_numbers_missing_from_the_cited_passage():
    provider = FakeStructuredProvider(
        {
            "recommendations": [
                {
                    **VALID_ITEM,
                    "message": "Der Abgleich senkt den Verbrauch um bis zu 15 %.",
                    "rationale": "Gasheizung in einem Gebäude von 1980.",
                    "source_ids": ["D1"],
                },
                {
                    **VALID_ITEM,
                    "recommendation_id": "own-knowledge",
                    "message": "Das spart etwa 15 %.",
                    "source_ids": [],
                },
                {
                    **VALID_ITEM,
                    "recommendation_id": "own-price",
                    "message": "Ein Messgerät kostet etwa 25 Euro.",
                    "source_ids": [],
                },
            ]
        }
    )

    cited, own, priced = LLMRecommendationGenerator(provider).generate(
        "http://fake", "fake", _context(), [PASSAGE]
    )

    assert cited.unverified_values == ["15"]
    assert own.unverified_values == []
    assert priced.unverified_values == ["25"]
    assert (
        "_Quelle: KI-Fachwissen, keine GuideOS-Regel"
        " | Ungeprüft: Geldbetrag 25 ist durch keine Quelle belegt"
    ) in render_llm_recommendations(load_domain_pack(), [priced])
    text = render_llm_recommendations(load_domain_pack(), [cited, own])
    assert (
        "(KI-formuliert, keine GuideOS-Regel)"
        " | Ungeprüft: Zahlenangabe 15 steht nicht in der zitierten Quelle"
    ) in text
    assert text.count("Ungeprüft") == 1


def test_report_note_for_an_item_with_a_user_context_source_names_the_amount_check():
    item = LLMRecommendation(
        **{**VALID_ITEM, "message": "Ein Messgerät kostet etwa 25 Euro."},
        sources=[SourceRef(kind="user_context", title="Heizungsart", reference="funding_source")],
        unverified_values=["25"],
    )

    text = render_llm_recommendations(load_domain_pack(), [item])

    assert "Ungeprüft: Geldbetrag 25 ist durch keine Quelle belegt" in text
    assert "zitierten Quelle" not in text


def test_internal_references_are_stripped_from_user_facing_text():
    from guideos.context.verification import strip_internal_references

    keys = ["volunteer_situation", "funding_source", "founded_year"]
    assert strip_internal_references(
        "Bei teilweiser Dämmung (volunteer_situation = partial) und einer Wärmepumpe "
        "(funding_source = heat_pump) lohnt ein Konzept.", keys, ["D1", "D3"]
    ) == "Bei teilweiser Dämmung und einer Wärmepumpe lohnt ein Konzept."
    assert strip_internal_references(
        "Gut gedämmte Rohre amortisieren sich schnell (vgl. D3).", keys, ["D3"]
    ) == "Gut gedämmte Rohre amortisieren sich schnell."
    assert strip_internal_references("Das Baujahr 1980 [founded_year] zählt.", keys, []) == (
        "Das Baujahr 1980 zählt."
    )
    assert strip_internal_references("Rohre dämmen [D1, W2].", keys, ["D1", "W2"]) == "Rohre dämmen."
    # Plain words and unknown keys stay untouched.
    assert strip_internal_references("Dämmung (teilweise) prüfen.", keys, []) == "Dämmung (teilweise) prüfen."


def test_generator_strips_field_keys_and_passage_ids_from_recommendations():
    provider = FakeStructuredProvider({"recommendations": [{
        **VALID_ITEM,
        "title": "Sanierungskonzept erarbeiten (volunteer_situation = partial)",
        "message": "Dämmung, Fenster und Heizsystem koordinieren (vgl. D1).",
        "rationale": "Bei teilweiser Dämmung (volunteer_situation = partial) und Gasheizung (funding_source = gas) sinnvoll.",
        "source_ids": ["D1"],
    }]})
    context = _context()
    passage = RetrievedPassage(
        source_id="D1", kind="document", document_id="doc", title="Leitfaden", locator="Seite 2",
        url=None, content_hash="a" * 64, retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
        text="Ein Sanierungskonzept koordiniert Dämmung, Fenster und Heizsystem.", score=1.0,
    )

    [item] = LLMRecommendationGenerator(provider).generate("http://fake", "fake", context, [passage])

    assert item.title == "Sanierungskonzept erarbeiten"
    assert item.message == "Dämmung, Fenster und Heizsystem koordinieren."
    assert item.rationale == "Bei teilweiser Dämmung und Gasheizung sinnvoll."
    assert [s.kind for s in item.sources] == ["document"]


def test_prompts_forbid_field_keys_raw_values_and_ids_in_text():
    from guideos.context.prompt import GROUNDING_RULES
    from guideos.recommendations.llm import PROMPT_VERSION

    assert "field_key" in GROUNDING_RULES and "source_id" in GROUNDING_RULES
    assert "display_value" in GROUNDING_RULES
    assert PROMPT_VERSION >= 2


def test_generator_logs_why_it_dropped_recommendations(caplog):
    import logging

    provider = FakeStructuredProvider({"recommendations": [
        VALID_ITEM,
        {**VALID_ITEM, "recommendation_id": "volunteer-recruiting"},  # rewrites a rule recommendation
        {**VALID_ITEM, "recommendation_id": "unsupported", "evidence_fields": ["unknown_field"]},
        {**VALID_ITEM, "recommendation_id": "hollow", "message": "(funding_source = gas)"},
        {**VALID_ITEM, "recommendation_id": "hydraulic-balancing"},  # duplicate id
    ]})

    with caplog.at_level(logging.INFO, logger="guideos"):
        items = LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    assert [item.recommendation_id for item in items] == ["hydraulic-balancing"]
    messages = [r.getMessage() for r in caplog.records if r.name == "guideos.recommendations.llm"]
    assert any(
        "llm_recommendations_checked received=5 accepted=1 rule_rewrite=1 no_evidence=1 "
        "empty_text=1 duplicate=1" in m
        for m in messages
    )
    assert not any("Hydraulischen" in m for m in messages)


def test_generator_maps_evidence_given_as_question_text_back_to_field_keys():
    provider = FakeStructuredProvider({"recommendations": [{
        **VALID_ITEM,
        "evidence_fields": ["Woher stammt der Großteil der Einnahmen?", "founded_year", "Dämmzustand"],
    }]})

    [item] = LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())

    # Question text resolves to its field key; an unknown label is dropped, not fatal.
    assert item.evidence_fields == ["funding_source", "founded_year"]


def test_grounding_rules_keep_identifiers_in_schema_fields():
    from guideos.context.prompt import GROUNDING_RULES

    rule = next(line for line in GROUNDING_RULES.split("\n") if "keine internen Bezeichner" in line)
    assert "evidence_fields" in rule and "source_ids" in rule
    assert "nur im Fließtext" in rule or "Fließtext" in rule


def test_bare_passage_ids_in_prose_are_removed():
    from guideos.context.verification import strip_internal_references

    assert strip_internal_references(
        "Der Hinweis in Quelle D2 empfiehlt die Dämmung; laut D3 und W1 lohnt sich das.", [], ["D2"]
    ) == "Der Hinweis in Quelle empfiehlt die Dämmung; laut lohnt sich das."
    assert strip_internal_references("Die Norm EN 12831 gilt.", [], []) == "Die Norm EN 12831 gilt."
    assert strip_internal_references(
        "Die Berechnung (T1) zeigt 9 Monate; laut T2 lohnt sich das.", [], []
    ) == "Die Berechnung zeigt 9 Monate; laut lohnt sich das."


def test_prompt_names_the_prose_forms_of_source_ids_as_forbidden():
    from guideos.context.prompt import GROUNDING_RULES

    assert "Quelle D2" in GROUNDING_RULES and "laut D3" in GROUNDING_RULES


def _tool_context():
    from datetime import UTC, datetime

    from guideos.tools.models import DatasetRef, ToolCall, ToolResult

    result = ToolResult(call=ToolCall(name="analyze_tiers", arguments={"dataset_id": "ds_1"}),
                        server_name="demo",
                        structured_content={"totals": {"months_above": 9, "max_above_kw": "3.516"},
                                            "provenance": {"calculation_id": "calc-tiers-1"}},
                        summary="Leistungsstufen: 9 Monate über der Schwelle.", calculation_id="calc-tiers-1")
    dataset = DatasetRef(dataset_id="ds_1", filename="Kassabuch.csv", server_name="demo",
                         tool_name="import_dataset", imported_at=datetime(2026, 10, 20, tzinfo=UTC),
                         summary="Import fertig.")
    return _context().model_copy(update={"tool_results": [result], "dataset": dataset, "domain_guidance": None})


def test_generator_passes_tool_results_and_dataset_and_names_them_neutrally():
    provider = FakeStructuredProvider({"recommendations": [VALID_ITEM]})

    LLMRecommendationGenerator(provider).generate("http://fake", "fake", _tool_context())

    system = provider.messages[0]["content"]
    assert "5. TOOL_RESULTS" in system and "DATASET" in system and "source_ids" in system
    assert "assumptions" in system and "limitations" in system
    for term in ("Förder", "Heiz", "Verbrauch", "Energie", "Österreich", "Kassabuch", "Verein", "Spitze", "kW", "Strom", "Netz"):
        assert term not in system, term
    payload = json.loads(provider.messages[1]["content"])
    assert payload["dataset"]["dataset_id"] == "ds_1" and payload["dataset"]["filename"] == "Kassabuch.csv"
    assert payload["tool_results"] == [{
        "source_id": "T1", "tool": "analyze_tiers", "arguments": {"dataset_id": "ds_1"},
        "summary": "Leistungsstufen: 9 Monate über der Schwelle.",
        "result": {"totals": {"months_above": 9, "max_above_kw": "3.516"},
                   "provenance": {"calculation_id": "calc-tiers-1"}},
    }]
    # Without a dataset the payload says so explicitly.
    LLMRecommendationGenerator(provider).generate("http://fake", "fake", _context())
    payload = json.loads(provider.messages[1]["content"])
    assert payload["dataset"] is None and payload["tool_results"] == []


def test_generator_resolves_tool_source_ids_and_verifies_figures_against_tool_outputs():
    provider = FakeStructuredProvider({"recommendations": [
        {**VALID_ITEM, "recommendation_id": "stagger-loads",
         "message": "In 9 Monaten lag die Spitze darüber; staffle große Geräte.",
         "source_ids": ["T1"]},
        {**VALID_ITEM, "recommendation_id": "invented",
         "message": "In 11 Monaten lag die Spitze darüber (vgl. T1).", "source_ids": ["t1", "T7", "D1"]},
        {**VALID_ITEM, "recommendation_id": "own", "message": "Das kostet rund 80 Euro im Jahr.", "source_ids": []},
    ]})

    grounded, invented, own = LLMRecommendationGenerator(provider).generate("http://fake", "fake", _tool_context())

    assert grounded.source_ids == ["T1"]
    assert [(s.kind, s.title, s.reference) for s in grounded.sources] == [
        ("tool", "analyze_tiers", "calc-tiers-1")]
    assert grounded.unverified_values == []
    assert invented.source_ids == ["T1"] and invented.unverified_values == ["11"]
    assert invented.message == "In 11 Monaten lag die Spitze darüber."
    assert own.source_ids == [] and [s.kind for s in own.sources] == ["llm"]
    assert own.unverified_values == ["80"]


def test_prompt_version_is_5():
    from guideos.recommendations.llm import PROMPT_VERSION

    assert PROMPT_VERSION == 5
