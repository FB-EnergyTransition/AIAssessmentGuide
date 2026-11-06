import pytest

from guideos.domains.loader import load_domain_pack
from guideos.validation.engine import AnswerValidationError, validate_answer


@pytest.fixture
def questions():
    return {
        question.field_key: question for question in load_domain_pack().questions
    }


def test_validate_number_normalizes_numeric_string(questions):
    assert validate_answer(questions["members_count"], "120,5") == 120.5


def test_validate_integer_rejects_boolean(questions):
    with pytest.raises(AnswerValidationError) as exc:
        validate_answer(questions["founded_year"], True)

    assert exc.value.code == "invalid_type"


def test_validate_range_rejects_out_of_bounds(questions):
    with pytest.raises(AnswerValidationError) as exc:
        validate_answer(questions["founded_year"], 1700)

    assert exc.value.code == "below_minimum"


def test_validate_unknown_only_when_allowed(questions):
    assert validate_answer(questions["founded_year"], None) is None

    with pytest.raises(AnswerValidationError) as exc:
        validate_answer(questions["members_count"], None)

    assert exc.value.code == "required"


def _question(field_type, *, options=None, required=True, allow_unknown=False, **validation):
    from guideos.domains.models import QuestionDefinition

    return QuestionDefinition.model_validate(
        {
            "question_id": "q",
            "field_key": "q",
            "text": "Frage?",
            "field_type": field_type,
            "required": required,
            "allow_unknown": allow_unknown,
            "options": [{"value": value, "label": label} for value, label in (options or [])],
            "validation": validation,
        }
    )


def _code(question, value):
    with pytest.raises(AnswerValidationError) as exc:
        validate_answer(question, value)
    return exc.value.code


CHANNELS = [("a", "A"), ("b", "B"), ("c", "C")]


def test_validate_multiselect_normalizes_lists_and_comma_strings():
    question = _question("multiselect", options=CHANNELS)

    assert validate_answer(question, ["b", "a", "a"]) == ["a", "b"]
    assert validate_answer(question, "a, c") == ["a", "c"]
    assert validate_answer(question, "a") == ["a"]


def test_validate_multiselect_rejects_wrong_types_and_options():
    question = _question("multiselect", options=CHANNELS)

    assert _code(question, ["x"]) == "invalid_option"
    assert _code(question, 42) == "invalid_type"
    assert _code(question, ["a", 1]) == "invalid_type"


def test_validate_multiselect_treats_empty_list_as_missing():
    assert _code(_question("multiselect", options=CHANNELS), []) == "required"
    assert validate_answer(_question("multiselect", options=CHANNELS, required=False), []) is None


def test_validate_multiselect_checks_counts():
    assert _code(_question("multiselect", options=CHANNELS, min_count=2), ["a"]) == "too_few_options"
    assert (
        _code(_question("multiselect", options=CHANNELS, max_count=1), ["a", "b"])
        == "too_many_options"
    )


def test_validate_text_strips_and_checks_type():
    question = _question("text")

    assert validate_answer(question, "  Müller GmbH  ") == "Müller GmbH"
    assert _code(question, "   ") == "required"
    assert validate_answer(_question("text", required=False), "   ") is None
    assert _code(question, 123) == "invalid_type"


def test_validate_text_checks_length_and_pattern():
    assert _code(_question("text", min_length=3), "ab") == "too_short"
    assert _code(_question("text", max_length=5), "abcdef") == "too_long"
    url = _question("text", pattern="^https?://\\S+$")
    assert _code(url, "example") == "pattern_mismatch"
    assert validate_answer(url, "https://x.de") == "https://x.de"


def test_validate_boolean_accepts_bool_and_german_words():
    question = _question("boolean")

    assert validate_answer(question, True) is True
    assert validate_answer(question, "nein") is False
    assert validate_answer(question, "Ja") is True
    assert validate_answer(question, "yes") is True
    assert validate_answer(question, "true") is True
    assert _code(question, "vielleicht") == "invalid_type"
    assert _code(question, 1) == "invalid_type"


def test_validate_date_normalizes_to_iso():
    import datetime

    question = _question("date")

    assert validate_answer(question, "2024-03-12") == "2024-03-12"
    assert validate_answer(question, "12.03.2024") == "2024-03-12"
    assert validate_answer(question, datetime.date(2024, 3, 12)) == "2024-03-12"
    assert _code(question, "2024-02-30") == "invalid_type"
    assert _code(question, "März 2024") == "invalid_type"


def test_validate_date_checks_bounds():
    assert _code(_question("date", min_date="2000-01-01"), "1999-12-31") == "before_minimum"
    assert _code(_question("date", max_date="2000-01-01"), "2000-01-02") == "after_maximum"


def test_validate_select_treats_empty_list_as_missing():
    assert _code(_question("select", options=CHANNELS), []) == "required"
    assert validate_answer(_question("select", options=CHANNELS, required=False), []) is None


def _file_question(**upload):
    from guideos.domains.models import QuestionDefinition

    return QuestionDefinition.model_validate({
        "question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
        "field_type": "file", "required": False, "upload": upload,
    })


def test_validate_file_normalizes_the_upload_input():
    question = _file_question()

    normalized = validate_answer(
        question, {"filename": " Messdaten.CSV ", "content": "ts;kwh\n2026-01-01 00:00;0,1\n"}
    )

    assert normalized == {
        "filename": "Messdaten.CSV",
        "content": "ts;kwh\n2026-01-01 00:00;0,1\n",
        "format_hint": "auto",
    }
    assert validate_answer(question, {"filename": "a.txt", "content": "x", "format_hint": "at"})[
        "format_hint"
    ] == "at"
    assert validate_answer(question, None) is None


def test_validate_file_keeps_an_imported_reference():
    question = _file_question()
    reference = {"dataset_id": "ds_1", "filename": "a.csv", "summary": "12 Monate"}

    assert validate_answer(question, reference) == reference


def test_validate_file_rejects_wrong_shapes_and_limits():
    question = _file_question(extensions=["csv"], max_bytes=10)

    assert _code(question, "a.csv") == "invalid_type"
    assert _code(question, {"filename": "a.csv"}) == "invalid_type"
    assert _code(question, {"filename": "", "content": "x"}) == "invalid_type"
    assert _code(question, {"filename": "a.csv", "content": "  \n"}) == "file_empty"
    assert _code(question, {"filename": "a.txt", "content": "x"}) == "file_extension"
    assert _code(question, {"filename": "a.csv", "content": "x" * 11}) == "file_too_large"
    assert _code(question, {"filename": "a.csv", "content": "äöü" * 4}) == "file_too_large"
    assert _code(question, {"filename": "a.csv", "content": "x", "format_hint": 1}) == "invalid_type"
    required = _file_question()
    required = required.model_copy(update={"required": True})
    assert _code(required, None) == "required"
