"""Topics visible to the user: each knowledge recommendation names its topic
in chat and report, and the report lists what GuideOS left out and why."""

from guideos.assessment.models import ExcludedRecommendation, KnowledgeGeneration, LLMRecommendation
from guideos.domains.loader import load_domain_pack, validate_domain_pack_data
from guideos.reports.renderer import render_llm_recommendations, render_markdown_report
from guideos.rules.engine import evaluate_assessment

from tests.test_topics_and_rule_kinds import _pack_data, _session


def _item(recommendation_id, topic, title):
    return LLMRecommendation(
        recommendation_id=recommendation_id, title=title, message="Tu es.", rationale="Weil.",
        evidence_fields=["drafty"], topic=topic,
    )


def test_chat_names_the_topic_label_of_each_knowledge_recommendation():
    pack = validate_domain_pack_data(_pack_data())
    text = render_llm_recommendations(pack, [_item("insulate", "envelope", "Dämmen"),
                                             _item("led", "other", "LED")])
    assert "Thema: Gebäudehülle" in text
    assert "Thema: Sonstiges" in text


def test_chat_omits_the_topic_when_the_pack_has_none():
    text = render_llm_recommendations(load_domain_pack(), [_item("led", None, "LED")])
    assert "Thema" not in text


def test_report_names_topics_and_lists_what_was_left_out():
    pack = validate_domain_pack_data(_pack_data())
    session = _session(pack, tenure="tenant", drafty=True)
    result = evaluate_assessment(pack, session)
    result = result.model_copy(update={
        "knowledge_recommendations": [_item("insulate", "envelope", "Dämmen")],
        "knowledge_generation": KnowledgeGeneration(model="fake", prompt_version=1),
        "excluded": [
            ExcludedRecommendation(recommendation_id="new-boiler", title="Heizung tauschen", trigger="rule",
                                   rule_id="tenant-no-heating", topic="heating_system"),
            ExcludedRecommendation(recommendation_id="consult", title="Energieberatung", trigger="user_feedback"),
        ],
    })
    content = render_markdown_report(pack, session, result).content
    assert "- Thema: Gebäudehülle" in content
    assert "## Nicht berücksichtigt" in content
    assert "- Heizung tauschen (Thema Heizsystem): ausgelassen durch Regel `tenant-no-heating`" in content
    assert "- Energieberatung: von dir zuvor abgelehnt" in content
    assert content.index("## Nicht berücksichtigt") > content.index("- Thema: Gebäudehülle")


def test_report_has_no_left_out_section_without_exclusions():
    pack = validate_domain_pack_data(_pack_data())
    session = _session(pack, tenure="owner", drafty=False)
    content = render_markdown_report(pack, session, evaluate_assessment(pack, session)).content
    assert "Nicht berücksichtigt" not in content
