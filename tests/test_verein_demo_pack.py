"""Bundled demo pack verein-demo 1.0.0: the loader accepts it, every rule
fires for at least one persona, the result order follows the pack, and the
texts are German with real umlauts."""

import json
import re

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.domains.loader import load_domain_pack
from guideos.recommendations.engine import build_recommendations
from guideos.rules.engine import evaluate_rules
from tests.pack_fixtures import DEMO_PACK_PATH, EXPECTED, PERSONAS

PACK_PATH = DEMO_PACK_PATH


def _session(answers):
    session = AssessmentSession.create(domain_id="verein-demo", domain_version="1.0.0",
                                       domain_content_hash="a" * 64, locale="de")
    for field_key, value in answers:
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value), normalized_value=value, confidence=1)
    return session


def test_pack_loads_with_six_questions_and_every_rule_referenced():
    pack = load_domain_pack(PACK_PATH)
    assert pack.domain_id == "verein-demo" and pack.version == "1.0.0"
    assert [q.field_key for q in pack.questions] == [
        "club_type", "founded_year", "members_count", "funding_source", "volunteer_situation", "annual_budget_eur"]
    assert [q.field_type for q in pack.questions] == ["select", "integer", "number", "select", "select", "number"]
    assert [q.required for q in pack.questions] == [True, True, True, True, True, False]
    assert [q.allow_unknown for q in pack.questions] == [False, True, False, False, True, True]
    assert pack.tools is None and pack.topics is None and pack.result_chat is None
    assert "Österreich" in pack.advisory_guidance
    referenced = {rid for rule in pack.rules for rid in rule.recommendation_ids}
    assert referenced == {r.recommendation_id for r in pack.recommendations}
    assert all(rule.source_reference and rule.status == "approved" for rule in pack.rules)


@pytest.mark.parametrize("persona", list(PERSONAS))
def test_rules_and_recommendations_per_persona(persona):
    pack = load_domain_pack(PACK_PATH)
    evaluation = evaluate_rules(pack, _session(PERSONAS[persona]))
    reason_codes, recommendation_ids = EXPECTED[persona]
    assert evaluation.reason_codes == reason_codes
    assert [r.recommendation_id for r in build_recommendations(pack, evaluation)] == recommendation_ids


def test_every_rule_fires_for_some_persona():
    pack = load_domain_pack(PACK_PATH)
    fired = {code for codes, _ in EXPECTED.values() for code in codes}
    assert fired == {rule.reason_code for rule in pack.rules}


def test_assessment_runs_through_the_orchestrator(tmp_path):
    orchestrator = AssessmentOrchestrator(load_domain_pack(PACK_PATH), LocalAssessmentRepository(tmp_path / "a.json"))
    response = orchestrator.start()
    session_id = response.session_id
    for question_id, value in PERSONAS["sportverein_gefoerdert"]:
        assert response.current_question.question_id == question_id
        response = orchestrator.submit(session_id, question_id, value)
    assert response.status == "review"
    result = orchestrator.complete(session_id)
    assert result.status == "completed"
    assert result.result.reason_codes == EXPECTED["sportverein_gefoerdert"][0]
    assert "Vereins-Check" in result.report.content


def test_texts_are_german_with_real_umlauts_and_free_of_energy_terms():
    text = PACK_PATH.read_text(encoding="utf-8")
    assert re.search(r"[äöüÄÖÜß]", text)
    assert not re.search(r"(?i)oesterreich|\bfuer\b|\bueber\b|gruend|zaehl", text)
    assert not re.search(r"(?i)energie|kwh|heiz|strom|netz(?!werk)|lastprofil|tarif", text)
    data = json.loads(text)
    assert all(key.isascii() for question in data["questions"] for key in (question["field_key"], question["question_id"]))
