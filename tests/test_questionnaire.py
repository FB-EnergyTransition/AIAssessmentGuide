from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.domains.models import DomainPack
from guideos.questionnaire.engine import get_next_question, is_question_visible


def test_get_next_question_skips_invisible_required_question():
    pack = _pack("equals", "house")
    session = _session()
    _answer(session, "club_type", "social")

    assert get_next_question(pack, session) is None
    assert not is_question_visible(pack, session, pack.questions[1])


def test_equals_condition_makes_question_visible_for_matching_answer():
    pack = _pack("equals", "house")
    session = _session()
    _answer(session, "club_type", "house")

    assert get_next_question(pack, session).field_key == "funding_source"


def test_in_condition_makes_question_visible_for_list_member():
    pack = _pack("in", ["house", "duplex"])
    session = _session()
    _answer(session, "club_type", "duplex")

    assert get_next_question(pack, session).field_key == "funding_source"


def test_is_missing_condition_only_matches_missing_or_unknown_answer():
    pack = _pack("is_missing")
    session = _session()

    assert is_question_visible(pack, session, pack.questions[1])

    _answer(session, "club_type", None)

    assert get_next_question(pack, session).field_key == "funding_source"

    _answer(session, "club_type", "house")

    assert get_next_question(pack, session) is None


def test_contains_condition_matches_list_member():
    pack = _pack("contains", "duplex", first_type="multiselect")
    session = _session()
    _answer(session, "club_type", ["house", "duplex"])

    assert is_question_visible(pack, session, pack.questions[1])

    _answer(session, "club_type", ["house"])

    assert not is_question_visible(pack, session, pack.questions[1])


def test_contains_any_condition_matches_overlap():
    pack = _pack("contains_any", ["duplex", "social"], first_type="multiselect")
    session = _session()
    _answer(session, "club_type", ["house", "social"])

    assert is_question_visible(pack, session, pack.questions[1])

    _answer(session, "club_type", ["house"])

    assert not is_question_visible(pack, session, pack.questions[1])


def test_contains_on_unknown_answer_is_hidden():
    pack = _pack("contains", "duplex", first_type="multiselect")
    session = _session()
    _answer(session, "club_type", None)

    assert not is_question_visible(pack, session, pack.questions[1])


def test_equals_condition_on_boolean_answer():
    pack = _pack("equals", True, first_type="boolean")
    session = _session()
    _answer(session, "club_type", True)

    assert is_question_visible(pack, session, pack.questions[1])

    _answer(session, "club_type", False)

    assert not is_question_visible(pack, session, pack.questions[1])


def _pack(operator, value=None, first_type="select"):
    visible_if = {
        "field_key": "club_type",
        "operator": operator,
    }
    if operator != "is_missing":
        visible_if["value"] = value
    return DomainPack.model_validate(
        {
            "domain_id": "demo",
            "version": "1.0.0",
            "name": "Demo",
            "description": "Demo",
            "default_locale": "de",
            "questions": [
                {
                    "question_id": "club_type",
                    "field_key": "club_type",
                    "text": "Gebäudetyp?",
                    "field_type": first_type,
                    "required": True,
                    "options": [
                        {"value": "house", "label": "Haus"},
                        {"value": "social", "label": "Sozial- oder Hilfsverein"},
                        {"value": "duplex", "label": "Doppelhaus"},
                    ] if first_type != "boolean" else [],
                },
                {
                    "question_id": "funding_source",
                    "field_key": "funding_source",
                    "text": "Heizung?",
                    "field_type": "select",
                    "required": True,
                    "visible_if": visible_if,
                    "options": [{"value": "public_grants", "label": "Öffentliche Förderungen"}],
                },
            ],
            "rules": [],
            "recommendations": [],
        }
    )


def _session():
    return AssessmentSession.create(
        domain_id="demo",
        domain_version="1.0.0",
        domain_content_hash="a" * 64,
        locale="de",
    )


def _answer(session, field_key, value):
    session.answers[field_key] = AssessmentAnswer(
        question_id=field_key,
        field_key=field_key,
        raw_answer="unbekannt" if value is None else str(value),
        normalized_value=value,
        confidence=1,
    )
