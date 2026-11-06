"""Recommendation texts with the participant's own figures: a pack declares
figures (tool, JSON pointer, decimals, map) on a recommendation and a text with
{name} placeholders; GuideOS fills them deterministically from the last tool
result of the session or profile and keeps the generic text when a figure is
missing. The model never writes these texts."""

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.domains.loader import validate_domain_pack_data
from guideos.profiles.models import Profile
from guideos.rules.engine import evaluate_assessment
from guideos.tools.models import ToolCall, ToolResult
from guideos.tools.pointers import format_figure

TIERS_OUTPUT = {
    "totals": {"threshold_kw": "10", "months_total": 12, "months_complete": 12, "months_above": 9,
               "kw_months_above_threshold": "19.888", "max_above_kw": "3.516"},
    "provenance": {"calculation_id": "calc-tiers-1"},
}
CHARACTERISTICS_OUTPUT = {
    "characteristics": {"charging": {"detected": True, "confidence": "high", "power_kw": "10.8",
                                     "peaks_in_sessions": 7}},
    "provenance": {"calculation_id": "calc-char-1"},
}


def _result(name, output):
    return ToolResult(call=ToolCall(name=name, arguments={"dataset_id": "ds_1"}), server_name="demo",
                      structured_content=output, summary=f"{name} fertig.",
                      calculation_id=output["provenance"]["calculation_id"])


def _pack(recommendations=None):
    return validate_domain_pack_data({
        "domain_id": "figures-demo", "version": "1.0.0", "name": "Kennzahlen", "description": "Test",
        "default_locale": "de",
        "questions": [
            {"question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
             "field_type": "file", "required": False, "upload": {}},
            {"question_id": "electric_vehicle", "field_key": "electric_vehicle", "text": "E-Auto?",
             "field_type": "boolean", "required": True},
        ],
        "rules": [
            {"rule_id": "peaks", "field_key": "dataset_file", "operator": "gte", "value": 1,
             "metric": {"tool": "analyze_tiers", "pointer": "/totals/months_above",
                        "label": "Monate über der Schwelle"},
             "reason_code": "PEAKS", "recommendation_ids": ["reduce-peaks"]},
            {"rule_id": "ev", "field_key": "electric_vehicle", "operator": "equals", "value": True,
             "reason_code": "EV", "recommendation_ids": ["ev-charging"]},
        ],
        "recommendations": recommendations or [
            {"recommendation_id": "reduce-peaks", "priority": 1, "title": "Spitzen senken",
             "message": "In mindestens einem Monat liegt die Spitze über der Schwelle.",
             "figures": {
                 "months_above": {"tool": "analyze_tiers", "pointer": "/totals/months_above",
                                  "decimals": 0},
                 "months_total": {"tool": "analyze_tiers", "pointer": "/totals/months_total"},
                 "max_above_kw": {"tool": "analyze_tiers", "pointer": "/totals/max_above_kw",
                                  "decimals": 1},
             },
             "message_with_figures": ("In {months_above} von {months_total} Monaten liegt die Spitze über "
                                      "der Schwelle, im höchsten Monat um {max_above_kw} kW.")},
            {"recommendation_id": "ev-charging", "priority": 2, "title": "Laden planen",
             "message": "Lade außerhalb der Abendstunden.",
             "figures": {
                 "peaks": {"tool": "analyze_characteristics",
                           "pointer": "/characteristics/charging/peaks_in_sessions"},
                 "confidence": {"tool": "analyze_characteristics",
                                "pointer": "/characteristics/charging/confidence",
                                "map": {"high": "hoch", "medium": "mittel", "low": "niedrig"}},
             },
             "message_with_figures": "{peaks} Monatsspitzen fallen in Ladevorgänge (Konfidenz {confidence})."},
        ],
        "tools": {"enabled": True,
                  "after_import": [{"tool": "analyze_tiers"}, {"tool": "analyze_characteristics"}]},
    })


def _session(pack, tool_results):
    session = AssessmentSession.create(pack.domain_id, pack.version, pack.content_hash, "de")
    session.answers["dataset_file"] = AssessmentAnswer(
        question_id="dataset_file", field_key="dataset_file", raw_answer="profil.csv",
        normalized_value={"dataset_id": "ds_1", "filename": "profil.csv", "summary": "Import.",
                          "field_key": "dataset_file"}, confidence=1)
    session.answers["electric_vehicle"] = AssessmentAnswer(
        question_id="electric_vehicle", field_key="electric_vehicle", raw_answer="ja", normalized_value=True,
        confidence=1)
    session.tool_results = list(tool_results)
    return session


def test_message_with_figures_is_resolved_from_the_last_tool_result():
    pack = _pack()
    session = _session(pack, [_result("analyze_tiers", TIERS_OUTPUT),
                              _result("analyze_characteristics", CHARACTERISTICS_OUTPUT)])

    result = evaluate_assessment(pack, session)

    peaks, ev = result.recommendations
    assert peaks.message == ("In 9 von 12 Monaten liegt die Spitze über der Schwelle, im höchsten Monat um "
                             "3,5 kW.")
    assert peaks.title == "Spitzen senken"
    assert peaks.figures["months_above"].model_dump() == {
        "value": 9, "text": "9", "tool": "analyze_tiers", "calculation_id": "calc-tiers-1"}
    assert peaks.figures["max_above_kw"].text == "3,5" and peaks.figures["max_above_kw"].value == "3.516"
    assert ev.message == "7 Monatsspitzen fallen in Ladevorgänge (Konfidenz hoch)."
    assert ev.figures["confidence"].calculation_id == "calc-char-1"
    # The evidence of the metric rule is untouched.
    assert peaks.evidence["dataset_file"]["value"] == 9


def test_generic_message_is_kept_when_a_figure_is_missing():
    pack = _pack()
    # No tool results at all: the metric rule does not match, the answer rule does.
    without = evaluate_assessment(pack, _session(pack, []))
    assert [item.recommendation_id for item in without.recommendations] == ["ev-charging"]
    assert without.recommendations[0].message == "Lade außerhalb der Abendstunden."
    assert without.recommendations[0].figures == {}

    # The tiers tool ran, the characteristics tool failed: only the EV text stays generic.
    partial = evaluate_assessment(pack, _session(pack, [_result("analyze_tiers", TIERS_OUTPUT)]))
    peaks, ev = partial.recommendations
    assert peaks.message.startswith("In 9 von 12 Monaten")
    assert ev.message == "Lade außerhalb der Abendstunden." and ev.figures == {}

    # A pointer without a value (null) falls back as well.
    incomplete = {**CHARACTERISTICS_OUTPUT,
                  "characteristics": {"charging": {"confidence": "high", "peaks_in_sessions": None}}}
    sparse = evaluate_assessment(pack, _session(pack, [_result("analyze_characteristics", incomplete)]))
    assert sparse.recommendations[0].message == "Lade außerhalb der Abendstunden."


def test_format_figure_uses_german_notation_and_maps_values():
    assert format_figure("19.888", decimals=1) == "19,9"
    assert format_figure("3.516", decimals=1) == "3,5"
    assert format_figure(5249.524, decimals=0) == "5250"  # no grouping: a dot would end a teaser sentence
    assert format_figure("1234567.5", decimals=2) == "1234567,50"
    assert format_figure(12, decimals=None) == "12"
    assert format_figure("12.0", decimals=None) == "12"
    assert format_figure("-42.5", decimals=2) == "-42,50"
    assert format_figure(True, decimals=None) == "Ja"
    assert format_figure("high", decimals=None, mapping={"high": "hoch"}) == "hoch"
    assert format_figure("high", decimals=None) == "high"
    assert format_figure(None, decimals=0) is None
    assert format_figure("high", decimals=None, mapping={"low": "niedrig"}) is None


def test_profile_reevaluation_keeps_the_figures():
    pack = _pack()
    session = _session(pack, [_result("analyze_tiers", TIERS_OUTPUT),
                              _result("analyze_characteristics", CHARACTERISTICS_OUTPUT)])
    session.result = evaluate_assessment(pack, session)
    session.status = "completed"
    profile = Profile.from_session(session)
    profile.tool_results = list(session.tool_results)

    recomputed = evaluate_assessment(pack, profile)

    assert [item.message for item in recomputed.recommendations] == [
        item.message for item in session.result.recommendations]
    assert recomputed.recommendations[0].figures["months_above"].calculation_id == "calc-tiers-1"


def test_result_chat_cites_the_calculation_behind_resolved_figures():
    from guideos.conversations.service import _rule_sections

    pack = _pack()
    session = _session(pack, [_result("analyze_tiers", TIERS_OUTPUT),
                              _result("analyze_characteristics", CHARACTERISTICS_OUTPUT)])
    result = evaluate_assessment(pack, session)

    peaks, ev = _rule_sections(pack, result)

    # The metric rule already cites its calculation; the figures of the same calculation add no duplicate.
    tool_sources = [source for source in peaks.sources if source.kind == "tool"]
    assert [source.reference for source in tool_sources] == ["calc-tiers-1"]
    # An answer rule whose text quotes figures cites that calculation with the quoted values.
    assert [source.kind for source in ev.sources] == ["rule", "user_context", "tool"]
    assert ev.sources[2].reference == "calc-char-1" and ev.sources[2].title == "analyze_characteristics"
    assert ev.sources[2].excerpt == "peaks: 7, confidence: hoch"
    assert ev.text.startswith("Laden planen: 7 Monatsspitzen fallen in Ladevorgänge")


def test_contract_carries_the_resolved_figures():
    from guideos.assessment.contract import _contract_recommendation

    pack = _pack()
    session = _session(pack, [_result("analyze_tiers", TIERS_OUTPUT)])
    peaks, ev = evaluate_assessment(pack, session).recommendations

    contract = _contract_recommendation(peaks).model_dump(mode="json")
    assert contract["message"].startswith("In 9 von 12 Monaten")
    assert contract["figures"]["max_above_kw"] == {
        "value": "3.516", "text": "3,5", "tool": "analyze_tiers", "calculation_id": "calc-tiers-1"}
    assert _contract_recommendation(ev).model_dump(mode="json")["figures"] == {}
