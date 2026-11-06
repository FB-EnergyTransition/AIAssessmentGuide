from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.domains.loader import load_domain_pack
from guideos.recommendations.engine import build_recommendations
from guideos.reports.renderer import render_markdown_report, render_rule_recommendations
from guideos.rules.engine import evaluate_assessment, evaluate_rules


def test_rule_engine_returns_rule_matches_without_score():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="critical",
        annual_budget_eur=18000,
    )

    evaluation = evaluate_rules(pack, session)

    assert "score" not in evaluation.model_dump()
    assert all("score" not in match.model_dump() for match in evaluation.matches)
    assert evaluation.reason_codes == [
        "GRANTS_DEPENDENT",
        "VOLUNTEERS_AT_RISK",
        "OLD_STATUTES",
        "HIGH_BUDGET_PER_MEMBER",
    ]
    assert [match.rule_id for match in evaluation.matches] == [
        "grants-dependent",
        "volunteers-at-risk",
        "old-statutes",
        "high-budget-per-member",
    ]
    assert evaluation.matches[0].field_key == "funding_source"
    assert evaluation.matches[0].recommendation_ids == ["funding-diversification"]
    assert evaluation.matches[0].evidence_value == "public_grants"


def test_recommendation_engine_builds_prioritized_recommendations_with_evidence():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="critical",
        annual_budget_eur=18000,
    )

    recommendations = build_recommendations(pack, evaluate_rules(pack, session))

    assert [
        recommendation.recommendation_id
        for recommendation in recommendations
    ] == [
        "volunteer-recruiting",
        "funding-diversification",
        "club-consultation",
    ]
    assert recommendations[0].reason_codes == ["VOLUNTEERS_AT_RISK"]
    assert recommendations[0].evidence == {"volunteer_situation": "critical"}
    assert recommendations[1].title == "Einnahmen breiter aufstellen"
    assert recommendations[2].reason_codes == [
        "OLD_STATUTES",
        "HIGH_BUDGET_PER_MEMBER",
    ]
    assert recommendations[2].evidence == {
        "founded_year": 1980,
        "annual_budget_eur": 18000,
    }


def test_rule_engine_recommends_collecting_missing_consumption():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="social",
        founded_year=2010,
        members_count=80,
        funding_source="events",
        volunteer_situation="sufficient",
        annual_budget_eur=None,
    )

    result = evaluate_assessment(pack, session)

    assert result.reason_codes == ["MISSING_BUDGET"]
    assert result.recommendations[0].recommendation_id == "collect-budget"


def test_evaluate_assessment_keeps_public_result_shape():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="critical",
        annual_budget_eur=18000,
    )

    result = evaluate_assessment(pack, session)

    assert set(result.model_dump()) == {
        "reason_codes",
        "recommendations",
        "knowledge_recommendations",
        "knowledge_generation",
        "excluded",
    }
    # Rule evaluation never produces knowledge recommendations itself.
    assert result.knowledge_recommendations == []
    assert result.knowledge_generation is None
    assert result.excluded == []
    assert result.reason_codes == [
        "GRANTS_DEPENDENT",
        "VOLUNTEERS_AT_RISK",
        "OLD_STATUTES",
        "HIGH_BUDGET_PER_MEMBER",
    ]
    assert [
        recommendation.recommendation_id
        for recommendation in result.recommendations
    ] == [
        "volunteer-recruiting",
        "funding-diversification",
        "club-consultation",
    ]


def test_markdown_report_contains_answers_results_and_disclaimer():
    pack = load_domain_pack()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="tight",
        annual_budget_eur=None,
    )
    result = evaluate_assessment(pack, session)

    report = render_markdown_report(pack, session, result)

    assert "# Vereins-Check Demo" in report.content
    assert "Score" not in report.content
    assert "Einnahmen breiter aufstellen" in report.content
    assert "keine verbindliche Beratung" in report.content
    assert session.session_id in report.content


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


def test_recommendation_result_carries_source_reference_from_pack():
    pack = _pack_with_source_reference()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="tight",
        annual_budget_eur=None,
    )

    result = evaluate_assessment(pack, session)

    by_id = {item.recommendation_id: item for item in result.recommendations}
    assert by_id["funding-diversification"].source_reference == "Demo-Quelle: Vereinsfinanzen"
    assert by_id["volunteer-recruiting"].source_reference is None
    assert result.model_dump()["recommendations"][0].keys() >= {"source_reference"}


def test_markdown_report_lists_evidence_and_source_per_recommendation():
    pack = _pack_with_source_reference()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="tight",
        annual_budget_eur=None,
    )
    result = evaluate_assessment(pack, session)

    report = render_markdown_report(pack, session, result)

    funding = report.content.split("### Einnahmen breiter aufstellen")[1]
    funding = funding.split("###")[0]
    assert "Gründe: `GRANTS_DEPENDENT`" in funding
    assert "Bezug: Woher stammt der Großteil der Einnahmen? (Öffentliche Förderungen)" in funding
    assert "Quelle: Demo-Quelle: Vereinsfinanzen" in funding
    volunteers = report.content.split("### Ehrenamt sichern")[1].split("###")[0]
    assert "Bezug: Wie schätzt du die Lage bei den Ehrenamtlichen ein? (Knapp)" in volunteers
    assert "Quelle:" not in volunteers


def _pack_with_source_reference():
    pack = load_domain_pack()
    recommendations = [
        item.model_copy(update={"source_reference": "Demo-Quelle: Vereinsfinanzen"})
        if item.recommendation_id == "funding-diversification"
        else item
        for item in pack.recommendations
    ]
    return pack.model_copy(update={"recommendations": recommendations})


def test_rule_recommendations_text_shows_evidence_and_source_per_recommendation():
    pack = _pack_with_source_reference()
    session = _session_with_answers(
        club_type="sports",
        founded_year=1980,
        members_count=100,
        funding_source="public_grants",
        volunteer_situation="tight",
        annual_budget_eur=None,
    )
    result = evaluate_assessment(pack, session)

    text = render_rule_recommendations(pack, result)

    assert text.startswith("**Finanzdaten erfassen**")
    assert "75/100" not in text
    funding = text.split("**Einnahmen breiter aufstellen**")[1].split("**Vereinsberatung")[0]
    assert "Quelle: GuideOS-Regel" in funding
    assert "Gründe: GRANTS_DEPENDENT" in funding
    assert "Bezug: Woher stammt der Großteil der Einnahmen? (Öffentliche Förderungen)" in funding
    assert "Fachquelle: Demo-Quelle: Vereinsfinanzen" in funding
    volunteers = text.split("**Ehrenamt sichern**")[1].split("**Einnahmen breiter")[0]
    assert "Bezug: Wie schätzt du die Lage bei den Ehrenamtlichen ein? (Knapp)" in volunteers
    assert "Fachquelle" not in volunteers


def _typed_pack():
    from guideos.domains.loader import validate_domain_pack_data

    return validate_domain_pack_data(
        {
            "domain_id": "typed",
            "version": "1.0.0",
            "name": "Typen-Demo",
            "description": "Demo",
            "default_locale": "de",
            "questions": [
                {
                    "question_id": "channels",
                    "field_key": "channels",
                    "text": "Kanäle?",
                    "field_type": "multiselect",
                    "required": True,
                    "options": [
                        {"value": "email", "label": "E-Mail"},
                        {"value": "website", "label": "Website"},
                        {"value": "social", "label": "Soziale Medien"},
                    ],
                },
                {
                    "question_id": "has_website",
                    "field_key": "has_website",
                    "text": "Website vorhanden?",
                    "field_type": "boolean",
                    "required": True,
                },
                {
                    "question_id": "founded_on",
                    "field_key": "founded_on",
                    "text": "Gründung?",
                    "field_type": "date",
                    "required": True,
                },
                {
                    "question_id": "employees",
                    "field_key": "employees",
                    "text": "Beschäftigte?",
                    "field_type": "integer",
                    "required": True,
                },
                {
                    "question_id": "summary",
                    "field_key": "summary",
                    "text": "Kurzbeschreibung?",
                    "field_type": "text",
                    "required": False,
                },
            ],
            "rules": [
                {
                    "rule_id": "social",
                    "field_key": "channels",
                    "operator": "contains",
                    "value": "social",
                    "reason_code": "SOCIAL",
                    "recommendation_ids": ["online"],
                },
                {
                    "rule_id": "online",
                    "field_key": "channels",
                    "operator": "contains_any",
                    "value": ["website", "social"],
                    "reason_code": "ONLINE",
                    "recommendation_ids": ["online"],
                },
                {
                    "rule_id": "no-website",
                    "field_key": "has_website",
                    "operator": "equals",
                    "value": False,
                    "reason_code": "NO_WEBSITE",
                },
                {
                    "rule_id": "established",
                    "field_key": "founded_on",
                    "operator": "lt",
                    "value": "2000-01-01",
                    "reason_code": "ESTABLISHED",
                },
                {
                    "rule_id": "large",
                    "field_key": "employees",
                    "operator": "gte",
                    "value": 50,
                    "reason_code": "LARGE",
                },
            ],
            "recommendations": [
                {
                    "recommendation_id": "online",
                    "priority": 1,
                    "title": "Online-Präsenz ausbauen",
                    "message": "Text",
                }
            ],
        }
    )


def test_rule_engine_matches_list_boolean_and_date_rules():
    pack = _typed_pack()
    session = _session_with_answers(
        channels=["email", "social"],
        has_website=False,
        founded_on="1998-05-01",
        employees=12,
    )

    evaluation = evaluate_rules(pack, session)

    assert evaluation.reason_codes == ["SOCIAL", "ONLINE", "NO_WEBSITE", "ESTABLISHED"]
    assert evaluation.matches[0].evidence_value == ["email", "social"]


def test_rule_engine_ignores_incomparable_values_without_error():
    pack = _typed_pack()
    session = _session_with_answers(
        channels="email",
        has_website="nein",
        founded_on=1998,
        employees=["50"],
    )

    evaluation = evaluate_rules(pack, session)

    assert evaluation.reason_codes == []


def test_display_value_renders_lists_booleans_and_dates():
    from guideos.domains.models import display_value

    pack = _typed_pack()
    by_key = {question.field_key: question for question in pack.questions}

    assert display_value(by_key["channels"], ["email", "website"]) == "E-Mail, Website"
    assert display_value(by_key["channels"], ["email", "x"]) == "E-Mail, x"
    assert display_value(by_key["has_website"], True) == "Ja"
    assert display_value(by_key["has_website"], False) == "Nein"
    assert display_value(by_key["founded_on"], "2024-03-12") == "12.03.2024"
    assert display_value(by_key["founded_on"], None) == "Unbekannt"
    assert display_value(by_key["employees"], 12) == "12"


def test_markdown_report_renders_lists_booleans_and_dates():
    pack = _typed_pack()
    session = _session_with_answers(
        channels=["email", "website"],
        has_website=True,
        founded_on="2024-03-12",
        employees=3,
        summary="Wir backen Brot.",
    )
    result = evaluate_assessment(pack, session)

    report = render_markdown_report(pack, session, result)

    assert "E-Mail, Website" in report.content
    assert "| Website vorhanden? | Ja |" in report.content or "Ja" in report.content
    assert "12.03.2024" in report.content
    assert "Wir backen Brot." in report.content
    assert "Bezug: Kanäle? (E-Mail, Website)" in report.content


def test_display_value_renders_file_answers():
    from guideos.domains.models import QuestionDefinition, display_value

    question = QuestionDefinition.model_validate({
        "question_id": "dataset_file", "field_key": "dataset_file", "text": "Kassabuch?",
        "field_type": "file", "required": False, "upload": {},
    })

    assert display_value(question, None) == "Unbekannt"
    assert display_value(question, {"dataset_id": "ds_1", "filename": "a.csv", "summary": ""}) == "a.csv"
    assert display_value(
        question, {"dataset_id": "ds_1", "filename": "a.csv", "summary": "12 Monate, 35.040 Werte"}
    ) == "a.csv (12 Monate, 35.040 Werte)"
    assert display_value(question, {"filename": "neu.csv", "content": "x"}) == "neu.csv"
