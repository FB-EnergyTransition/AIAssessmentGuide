"""Topics and rule kinds (concept step 4): rules can exclude or prioritize
topics of knowledge recommendations; the model only assigns a topic."""

import json

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.context import build_advisory_context
from guideos.domains.catalog import pack_draft_payload
from guideos.domains.loader import DomainPackError, load_domain_pack, validate_domain_pack_data
from guideos.recommendations.llm import LLMRecommendationGenerator
from guideos.recommendations.service import generate_for_context
from guideos.rules.engine import evaluate_assessment, evaluate_rules


def _pack_data(**overrides):
    data = {
        "domain_id": "topics-demo", "version": "1.0.0", "name": "Themen-Demo",
        "description": "Test", "default_locale": "de",
        "topics": [
            {"topic_id": "heating_system", "label": "Heizsystem"},
            {"topic_id": "envelope", "label": "Gebäudehülle"},
        ],
        "questions": [
            {"question_id": "tenure", "field_key": "tenure", "text": "Eigentum oder Miete?",
             "field_type": "select", "required": True,
             "options": [{"value": "owner", "label": "Eigentum"}, {"value": "tenant", "label": "Miete"}]},
            {"question_id": "drafty", "field_key": "drafty", "text": "Zieht es?",
             "field_type": "boolean", "required": True},
        ],
        "rules": [
            {"rule_id": "tenant-no-heating", "kind": "exclude", "field_key": "tenure", "operator": "equals",
             "value": "tenant", "reason_code": "TENANT", "topics": ["heating_system"]},
            {"rule_id": "drafty-envelope-first", "kind": "prioritize", "field_key": "drafty",
             "operator": "equals", "value": True, "reason_code": "DRAFTY", "topics": ["envelope"],
             "priority": 1},
            {"rule_id": "drafty-seal", "field_key": "drafty", "operator": "equals", "value": True,
             "reason_code": "DRAFTY_SEAL", "recommendation_ids": ["seal"]},
        ],
        "recommendations": [
            {"recommendation_id": "seal", "priority": 1, "title": "Fenster abdichten", "message": "Dichten."},
        ],
    }
    data.update(overrides)
    return data


def _session(pack, **answers):
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    for field_key, value in answers.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key, raw_answer=str(value),
            normalized_value=value, confidence=1,
        )
    return session


def test_pack_with_topics_and_rule_kinds_loads_and_defaults_stay_implicit():
    pack = validate_domain_pack_data(_pack_data())

    assert [t.topic_id for t in pack.topics] == ["heating_system", "envelope"]
    kinds = {rule.rule_id: rule.kind for rule in pack.rules}
    assert kinds == {"tenant-no-heating": "exclude", "drafty-envelope-first": "prioritize", "drafty-seal": "recommend"}
    payload = pack_draft_payload(pack)
    plain_rule = next(r for r in payload["rules"] if r["rule_id"] == "drafty-seal")
    assert "kind" not in plain_rule and "topics" not in plain_rule and "priority" not in plain_rule
    exclude_rule = next(r for r in payload["rules"] if r["rule_id"] == "tenant-no-heating")
    assert exclude_rule["kind"] == "exclude" and exclude_rule["topics"] == ["heating_system"]
    # Packs without topics serialize, and therefore hash, exactly as before.
    energy = load_domain_pack()
    assert "topics" not in pack_draft_payload(energy)
    assert all("kind" not in rule for rule in pack_draft_payload(energy)["rules"])


@pytest.mark.parametrize("broken, message", [
    ({"rules": [{"rule_id": "x", "kind": "exclude", "field_key": "tenure", "operator": "equals",
                 "value": "tenant", "reason_code": "T", "topics": ["unknown"]}]}, "unbekannte Themen"),
    ({"rules": [{"rule_id": "x", "kind": "exclude", "field_key": "tenure", "operator": "equals",
                 "value": "tenant", "reason_code": "T"}]}, "mindestens ein Thema"),
    ({"rules": [{"rule_id": "x", "kind": "prioritize", "field_key": "tenure", "operator": "equals",
                 "value": "tenant", "reason_code": "T", "topics": ["envelope"]}]}, "priority"),
    ({"rules": [{"rule_id": "x", "kind": "exclude", "field_key": "tenure", "operator": "equals",
                 "value": "tenant", "reason_code": "T", "topics": ["envelope"],
                 "recommendation_ids": ["seal"]}]}, "keine recommendation_ids"),
    ({"rules": [{"rule_id": "x", "field_key": "tenure", "operator": "equals", "value": "tenant",
                 "reason_code": "T", "topics": ["envelope"]}]}, "nur für exclude und prioritize"),
    ({"topics": [{"topic_id": "a", "label": "A"}, {"topic_id": "a", "label": "B"}]}, "doppelte topic_id"),
])
def test_loader_rejects_inconsistent_topic_rules(broken, message):
    with pytest.raises(DomainPackError, match=message):
        validate_domain_pack_data(_pack_data(**broken))


def test_rules_engine_reports_excluded_and_prioritized_topics():
    pack = validate_domain_pack_data(_pack_data())

    evaluation = evaluate_rules(pack, _session(pack, tenure="tenant", drafty=True))

    assert evaluation.reason_codes == ["TENANT", "DRAFTY", "DRAFTY_SEAL"]
    assert [(e.topic_id, e.rule_id) for e in evaluation.excluded_topics] == [("heating_system", "tenant-no-heating")]
    assert [(p.topic_id, p.priority, p.rule_id) for p in evaluation.topic_priorities] == [
        ("envelope", 1, "drafty-envelope-first")
    ]
    assert [m.rule_id for m in evaluation.matches] == ["drafty-seal"]
    result = evaluate_assessment(pack, _session(pack, tenure="owner", drafty=False))
    assert result.reason_codes == [] and result.recommendations == []


def test_advisory_context_carries_topic_rules_and_catalog():
    pack = validate_domain_pack_data(_pack_data())
    session = _session(pack, tenure="tenant", drafty=True)
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)

    context = build_advisory_context(pack, session)

    assert [t.topic_id for t in context.topics] == ["heating_system", "envelope"]
    assert context.excluded_topics == ["heating_system"]
    assert {p.topic_id: p.priority for p in context.topic_priorities} == {"envelope": 1}


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result):
        self.result = result
        self.schemas = []
        self.messages = []

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.schemas.append(schema)
        self.messages.append(messages)
        return self.result


def _item(rid, topic, title="Titel"):
    return {"recommendation_id": rid, "title": title, "message": "Text.", "rationale": "Grund.",
            "evidence_fields": ["tenure"], "topic": topic}


def test_generator_asks_for_a_topic_and_normalizes_unknown_ones():
    pack = validate_domain_pack_data(_pack_data())
    session = _session(pack, tenure="tenant", drafty=True)
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    context = build_advisory_context(pack, session)
    provider = FakeProvider({"recommendations": [_item("a", "envelope"), _item("b", "garden"), _item("c", None)]})

    items = LLMRecommendationGenerator(provider).generate("http://fake", "fake", context)

    assert "topic" in provider.schemas[0]["$defs"]["GeneratedRecommendation"]["properties"]
    payload = json.loads(provider.messages[0][1]["content"])
    assert [t["topic_id"] for t in payload["topics"]] == ["heating_system", "envelope"]
    assert payload["excluded_topics"] == ["heating_system"]
    assert "TOPICS" in provider.messages[0][0]["content"] and "EXCLUDED_TOPICS" in provider.messages[0][0]["content"]
    assert [(i.recommendation_id, i.topic) for i in items] == [("a", "envelope"), ("b", "other"), ("c", "other")]


def test_generation_excludes_and_prioritizes_by_topic():
    pack = validate_domain_pack_data(_pack_data())
    session = _session(pack, tenure="tenant", drafty=True)
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    context = build_advisory_context(pack, session)
    provider = FakeProvider({"recommendations": [
        _item("new-boiler", "heating_system", "Heizung tauschen"),
        _item("led", "other", "LED"),
        _item("insulate", "envelope", "Dämmen"),
    ]})

    recommendations, generation, excluded = generate_for_context(
        pack, context, "sess", provider, "http://fake", "fake"
    )

    assert [(r.recommendation_id, r.priority) for r in recommendations] == [("insulate", 1), ("led", None)]
    assert [(e.recommendation_id, e.trigger, e.rule_id, e.topic) for e in excluded] == [
        ("new-boiler", "rule", "tenant-no-heating", "heating_system")
    ]
    assert generation.model == "fake"


def test_packs_without_topics_keep_every_recommendation():
    pack = load_domain_pack()
    session = _session(pack, club_type="sports", founded_year=1980, members_count=120,
                       funding_source="public_grants", volunteer_situation="tight", annual_budget_eur=None)
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    context = build_advisory_context(pack, session)
    provider = FakeProvider({"recommendations": [
        {"recommendation_id": "x", "title": "T", "message": "M", "rationale": "R",
         "evidence_fields": ["funding_source"], "topic": "anything"},
    ]})

    recommendations, _, excluded = generate_for_context(pack, context, "sess", provider, "http://fake", "fake")

    assert [r.recommendation_id for r in recommendations] == ["x"]
    assert recommendations[0].topic is None and excluded == []
    assert json.loads(provider.messages[0][1]["content"])["topics"] == []
