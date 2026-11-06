from guideos.assessment.models import (
    AssessmentAnswer,
    AssessmentChatMessage,
    AssessmentSession,
)
from guideos.context import AdvisoryContext, UserFact, build_advisory_context
from guideos.domains.loader import load_domain_pack
from guideos.rules.engine import evaluate_assessment


def test_completed_assessment_answers_become_facts_with_provenance():
    pack = load_domain_pack()
    session = _completed_session(pack)

    context = build_advisory_context(pack, session)

    assert isinstance(context, AdvisoryContext)
    facts = {fact.field_key: fact for fact in context.facts}
    assert set(facts) == {
        "club_type",
        "founded_year",
        "members_count",
        "funding_source",
        "volunteer_situation",
        "annual_budget_eur",
    }
    funding = facts["funding_source"]
    assert isinstance(funding, UserFact)
    assert funding.question_id == "funding_source"
    assert funding.question_text == "Woher stammt der Großteil der Einnahmen?"
    assert funding.raw_answer == "vor allem Förderungen vom Land"
    assert funding.normalized_value == "public_grants"
    assert funding.confidence == 0.8


def test_unknown_answer_keeps_raw_text_and_missing_normalized_value():
    pack = load_domain_pack()
    session = _completed_session(pack)

    context = build_advisory_context(pack, session)

    consumption = next(
        fact for fact in context.facts if fact.field_key == "annual_budget_eur"
    )
    assert consumption.raw_answer == "unbekannt"
    assert consumption.normalized_value is None


def test_deterministic_result_is_included():
    pack = load_domain_pack()
    session = _completed_session(pack)

    context = build_advisory_context(pack, session)

    assert context.assessment_session_id == session.session_id
    assert "assessment_score" not in context.model_dump()
    assert context.reason_codes == [
        "GRANTS_DEPENDENT",
        "VOLUNTEERS_AT_RISK",
        "OLD_STATUTES",
        "MISSING_BUDGET",
    ]
    assert context.rule_recommendations == session.result.recommendations
    assert [item.recommendation_id for item in context.rule_recommendations] == [
        "collect-budget",
        "volunteer-recruiting",
        "funding-diversification",
        "club-consultation",
    ]


def test_domain_and_participant_metadata_are_preserved():
    pack = load_domain_pack()
    session = _completed_session(pack)
    session.participant_id = "participant-1"

    context = build_advisory_context(pack, session)

    assert context.domain_id == "verein-demo"
    assert context.domain_version == "1.0.0"
    assert context.domain_name == pack.name
    assert context.participant_id == "participant-1"


def test_session_without_result_builds_context_without_rule_results():
    pack = load_domain_pack()
    session = _session_with_answers(club_type="social")

    context = build_advisory_context(pack, session)

    assert [fact.field_key for fact in context.facts] == ["club_type"]
    assert context.participant_id is None
    assert context.reason_codes == []
    assert context.rule_recommendations == []


def test_context_contains_no_chat_history():
    pack = load_domain_pack()
    session = _completed_session(pack)
    session.chat_messages.append(
        AssessmentChatMessage(
            scope="post_completion",
            role="user",
            content="Geheime Chatnachricht",
        )
    )

    context = build_advisory_context(pack, session)

    assert not any("chat" in name or "message" in name for name in AdvisoryContext.model_fields)
    assert "Geheime Chatnachricht" not in context.model_dump_json()


def _completed_session(pack):
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=120,
        funding_source="public_grants",
        volunteer_situation="tight",
        annual_budget_eur=None,
    )
    session.answers["funding_source"] = AssessmentAnswer(
        question_id="funding_source",
        field_key="funding_source",
        raw_answer="vor allem Förderungen vom Land",
        normalized_value="public_grants",
        confidence=0.8,
    )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    return session


def _session_with_answers(**values):
    session = AssessmentSession.create(
        domain_id="verein-demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )
    for field_key, value in values.items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key,
            field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value,
            confidence=1,
        )
    return session


def test_facts_carry_the_label_shown_to_the_user():
    pack = load_domain_pack()

    context = build_advisory_context(pack, _completed_session(pack))

    facts = {fact.field_key: fact for fact in context.facts}
    assert facts["funding_source"].display_value == "Öffentliche Förderungen"
    assert facts["founded_year"].display_value == "1980"
    assert facts["annual_budget_eur"].display_value == "Unbekannt"


def test_threshold_rules_become_metrics_with_computed_value_and_threshold():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="sports",
        founded_year=2015,
        members_count=140,
        funding_source="membership_fees",
        volunteer_situation="sufficient",
        annual_budget_eur=28000,
    )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)

    context = build_advisory_context(pack, session)

    metrics = {metric.rule_id: metric for metric in context.rule_metrics}
    assert set(metrics) == {"old-statutes", "high-budget-per-member"}
    budget = metrics["high-budget-per-member"]
    assert budget.field_key == "annual_budget_eur"
    assert budget.divisor_field_key == "members_count"
    assert budget.value == 200
    assert (budget.operator, budget.threshold, budget.matched) == ("gte", 150, True)
    founded = metrics["old-statutes"]
    assert founded.divisor_field_key is None
    assert (founded.value, founded.threshold, founded.matched) == (2015, 2002, False)


def test_session_without_result_has_no_metrics():
    pack = load_domain_pack()

    context = build_advisory_context(pack, _session_with_answers(founded_year=1980))

    assert context.rule_metrics == []


def test_domain_guidance_comes_from_the_pack():
    pack = load_domain_pack().model_copy(update={"advisory_guidance": None})
    guided = pack.model_copy(update={"advisory_guidance": "Der Kontext ist Österreich."})

    assert build_advisory_context(pack, _completed_session(pack)).domain_guidance is None
    assert (
        build_advisory_context(guided, _completed_session(guided)).domain_guidance
        == "Der Kontext ist Österreich."
    )


def test_tool_results_and_dataset_of_session_and_profile_enter_the_context():
    from datetime import UTC, datetime

    from guideos.profiles.models import Profile
    from guideos.tools.models import DatasetRef, ToolCall, ToolResult
    from tests.pack_fixtures import tools_pack

    pack = tools_pack()
    result = ToolResult(call=ToolCall(name="analyze_tiers", arguments={"dataset_id": "ds_1"}),
                        server_name="demo", structured_content={"totals": {"months_above": 9}},
                        summary="Leistungsstufen.", calculation_id="calc-tiers-1")
    dataset = {"dataset_id": "ds_1", "content_hash": "c" * 64, "filename": "Kassabuch.csv",
               "imported_at": datetime(2026, 10, 20, 12, tzinfo=UTC).isoformat(), "server_name": "demo",
               "tool_name": "import_dataset", "summary": "Import.", "details": {}, "field_key": "dataset_file"}
    session = AssessmentSession.create(pack.domain_id, pack.version, pack.content_hash, "de")
    session.answers["dataset_file"] = AssessmentAnswer(
        question_id="dataset_file", field_key="dataset_file", raw_answer="Kassabuch.csv",
        normalized_value=dataset, confidence=1)
    session.tool_results = [result]

    context = build_advisory_context(pack, session)
    assert context.tool_results == [result]
    # The advisory file answer is the session's dataset (the profile gets it at completion).
    assert context.dataset is not None and context.dataset.dataset_id == "ds_1"
    assert context.dataset.filename == "Kassabuch.csv"
    # The fact keeps only id, name and summary.
    fact = next(item for item in context.facts if item.field_key == "dataset_file")
    assert set(fact.normalized_value) == {"dataset_id", "filename", "summary"}

    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    profile = Profile.from_session(session)
    profile.tool_results = [result]
    profile.dataset = DatasetRef.model_validate(dataset)
    profile_context = build_advisory_context(pack, profile)
    assert profile_context.tool_results == [result] and profile_context.dataset == profile.dataset

    # Without a file answer there is no dataset; a skipped file answer neither.
    assert build_advisory_context(pack, _session_with_answers(members_count=120)).dataset is None
    skipped = _session_with_answers(dataset_file=None)
    assert build_advisory_context(pack, skipped).dataset is None and skipped.tool_results == []
