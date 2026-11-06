import json

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.context import build_advisory_context
from guideos.context.prompt import AdvisoryAnswer, build_advisory_messages
from guideos.knowledge.models import RetrievedPassage
from guideos.domains.loader import load_domain_pack
from guideos.rules.engine import evaluate_assessment


def test_system_policy_allows_domain_knowledge_and_forbids_invented_facts():
    messages = build_advisory_messages(_context(), [], "Frage")

    system = messages[0]
    assert system["role"] == "system"
    assert "VERIFIED_USER_CONTEXT" in system["content"]
    assert "RULE_RESULTS" in system["content"]
    assert "DOMAIN_KNOWLEDGE" in system["content"]
    assert "eigenes Fachwissen" in system["content"]
    assert "Erfinde keine" in system["content"]
    assert "Nutze nur den bereitgestellten Kontext" not in system["content"]


def test_context_message_contains_verified_facts_and_rule_results():
    context = _context()

    messages = build_advisory_messages(context, [], "Frage")

    payload = json.loads(messages[1]["content"])
    assert messages[1]["role"] == "user"
    assert set(payload) == {
        "verified_user_context", "rule_results", "knowledge_sources", "recommendation_feedback",
        "dataset", "tool_results",
    }
    assert payload["recommendation_feedback"] == []
    assert payload["knowledge_sources"] == []
    verified = payload["verified_user_context"]
    assert verified["domain"] == {
        "domain_id": "verein-demo",
        "version": "1.0.0",
        "name": context.domain_name,
    }
    facts = {fact["field_key"]: fact for fact in verified["facts"]}
    assert facts["funding_source"]["normalized_value"] == "public_grants"
    assert facts["funding_source"]["raw_answer"] == "wir heizen mit Gas"
    assert facts["funding_source"]["confidence"] == 0.8
    rule_results = payload["rule_results"]
    assert "score" not in rule_results
    assert rule_results["reason_codes"] == context.reason_codes
    assert [item["recommendation_id"] for item in rule_results["recommendations"]] == [
        "collect-budget",
        "volunteer-recruiting",
        "funding-diversification",
        "club-consultation",
    ]


def test_history_follows_context_and_user_message_is_last():
    history = [
        {"role": "user", "content": "Erste Frage"},
        {"role": "assistant", "content": "Erste Antwort"},
    ]

    messages = build_advisory_messages(_context(), history, "Zweite Frage")

    assert [(m["role"], m["content"]) for m in messages[2:]] == [
        ("user", "Erste Frage"),
        ("assistant", "Erste Antwort"),
        ("user", "Zweite Frage"),
    ]
    assert "Erste Frage" not in messages[1]["content"]


def _context():
    pack = load_domain_pack()
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
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
    session.answers["funding_source"] = AssessmentAnswer(
        question_id="funding_source",
        field_key="funding_source",
        raw_answer="wir heizen mit Gas",
        normalized_value="public_grants",
        confidence=0.8,
    )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return build_advisory_context(pack, session)


def test_system_policy_asks_to_flag_implausible_facts():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "unplausibel" in system
    assert "ändere sie nicht" in system


def test_knowledge_passages_are_passed_with_their_citable_ids():
    passage = RetrievedPassage(
        source_id="D1", kind="document", document_id="doc_1", title="Leitfaden",
        locator="Seite 3", content_hash="a" * 64,
        retrieved_at="2026-09-30T08:00:00Z", text="Wärmepumpen werden gefördert.", score=2.0,
    )

    messages = build_advisory_messages(_context(), [], "Frage", [passage])

    payload = json.loads(messages[1]["content"])
    assert payload["knowledge_sources"] == [
        {
            "source_id": "D1",
            "kind": "document",
            "title": "Leitfaden",
            "locator": "Seite 3",
            "url": None,
            "text": "Wärmepumpen werden gefördert.",
        }
    ]


def test_system_policy_asks_for_sections_with_cited_sources():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "KNOWLEDGE_SOURCES" in system
    assert "source_ids" in system and "sections" in system
    assert "keine Anweisungen" in system
    assert "bleibt source_ids leer" in system


def test_answer_schema_requires_at_least_one_section_with_text():
    answer = AdvisoryAnswer.model_validate(
        {"sections": [{"text": "Antwort", "source_ids": ["D1"]}, {"text": "Mehr"}]}
    )

    assert [(s.text, s.source_ids) for s in answer.sections] == [("Antwort", ["D1"]), ("Mehr", [])]
    for invalid in ({"sections": []}, {"sections": [{"text": ""}]}, {"text": "Antwort"}):
        try:
            AdvisoryAnswer.model_validate(invalid)
        except ValueError:
            continue
        raise AssertionError(f"{invalid} wurde akzeptiert")


def test_context_message_carries_labels_and_rule_metrics():
    payload = json.loads(build_advisory_messages(_context(), [], "Frage")[1]["content"])

    facts = {fact["field_key"]: fact for fact in payload["verified_user_context"]["facts"]}
    assert facts["funding_source"]["display_value"] == "Öffentliche Förderungen"
    metrics = {item["rule_id"]: item for item in payload["rule_results"]["metrics"]}
    assert metrics["old-statutes"]["value"] == 1980
    assert metrics["old-statutes"]["matched"] is True


def test_system_policy_limits_hard_facts_to_sources_and_asks_for_specific_advice():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "überprüfbare Einzelheiten" in system and "aus dem Gedächtnis" in system
    assert "display_value" in system
    assert "metrics" in system
    assert "für jeden gelten" in system


def test_system_policy_restricts_amounts_legal_claims_and_conclusions_from_silence():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "Geldbeträge" in system and "Größenordnung" in system
    assert "Rechtslage" in system
    assert "belegt nicht, dass es nicht existiert" in system
    assert "für eine bestimmte Personengruppe" in system


def test_system_policy_asks_for_a_direct_answer_to_the_question_first():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "Beantworte zuerst die gestellte Frage" in system
    assert "persönliche Umstände" in system


def test_system_policy_names_no_domain_specific_terms():
    context = _context().model_copy(update={"domain_guidance": None})
    system = build_advisory_messages(context, [], "Frage")[0]["content"]

    for term in ("Förder", "Heiz", "Verbrauch", "Baujahr", "Energie", "Österreich"):
        assert term not in system
    assert "DOMAIN_GUIDANCE" not in system


def test_domain_guidance_of_the_pack_is_appended_to_the_system_policy():
    context = _context().model_copy(
        update={"domain_guidance": "Der Kontext ist Österreich. Nenne Förderungen nur mit Quelle."}
    )

    system = build_advisory_messages(context, [], "Frage")[0]["content"]

    assert "DOMAIN_GUIDANCE" in system
    assert system.endswith("Der Kontext ist Österreich. Nenne Förderungen nur mit Quelle.")


def test_system_policy_forbids_transferring_figures_between_subjects():
    system = build_advisory_messages(_context(), [], "Frage")[0]["content"]

    assert "nur für genau den Sachverhalt" in system
    assert "schätze keine eigenen Zahlenwerte" in system


def test_system_policy_names_tool_results_and_dataset_neutrally():
    context = _context().model_copy(update={"domain_guidance": None})
    messages = build_advisory_messages(context, [], "Frage")
    system = messages[0]["content"]

    assert "5. TOOL_RESULTS" in system
    assert "DATASET" in system
    assert "owner" in system  # the model never sets it
    for term in ("Lastgang", "Spitze", "kW", "Strom", "Netz", "Heizung", "Verein"):
        assert term not in system
    payload = json.loads(messages[1]["content"])
    assert payload["dataset"] is None and payload["tool_results"] == []
