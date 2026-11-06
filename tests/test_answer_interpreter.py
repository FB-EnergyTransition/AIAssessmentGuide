from guideos.assessment.interpreter import (
    AnswerInterpretationError,
    InterpretationResult,
    LLMAnswerInterpreter,
)
from guideos.domains.loader import load_domain_pack


class FakeStructuredProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result):
        self.result = result
        self.schema = None

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.schema = schema
        return self.result


def test_interpreter_validates_structured_provider_output():
    provider = FakeStructuredProvider(
        {
            "decision": "valid",
            "normalized_value": 120,
            "confidence": 0.91,
            "reason_code": None,
            "follow_up": None,
        }
    )
    question = load_domain_pack().questions[2]
    interpreter = LLMAnswerInterpreter(provider)

    result = interpreter.interpret(
        "http://fake",
        "fake",
        question,
        "ungefähr 120 Quadratmeter",
    )

    assert result.normalized_value == 120
    assert result.confidence == 0.91
    assert provider.schema["required"] == [
        "decision",
        "normalized_value",
        "confidence",
        "reason_code",
        "follow_up",
    ]


def test_interpreter_rejects_malformed_provider_output():
    provider = FakeStructuredProvider({"decision": "maybe"})
    question = load_domain_pack().questions[0]

    try:
        LLMAnswerInterpreter(provider).interpret(
            "http://fake",
            "fake",
            question,
            "Haus",
        )
    except AnswerInterpretationError as exc:
        assert "strukturiert" in str(exc)
    else:
        raise AssertionError("Malformed output was accepted")


def test_interpreter_prompt_states_decision_rules_and_value_mapping():
    provider = FakeStructuredProvider(
        {"decision": "valid", "normalized_value": "sports", "confidence": 0.9,
         "reason_code": None, "follow_up": None}
    )
    provider.messages = None
    original = provider.generate_structured

    def capture(endpoint, model, messages, schema):
        provider.messages = messages
        return original(endpoint, model, messages, schema)

    provider.generate_structured = capture
    question = load_domain_pack().questions[0]

    LLMAnswerInterpreter(provider).interpret("http://fake", "fake", question, "Sportverein")

    system = provider.messages[0]["content"]
    assert "value" in system and "label" in system
    assert "'valid'" in system and "'uncertain'" in system
    assert "'not_answered'" in system
    assert "follow_up" in system and "null" in system
    assert "Deutsch" in system


def test_interpreter_normalizes_placeholder_follow_up_to_none():
    provider = FakeStructuredProvider(
        {"decision": "uncertain", "normalized_value": None, "confidence": 0.3,
         "reason_code": None, "follow_up": "None"}
    )
    question = load_domain_pack().questions[0]

    result = LLMAnswerInterpreter(provider).interpret("http://fake", "fake", question, "Haus")

    assert result.decision == "uncertain"
    assert result.follow_up is None


def test_interpreter_prompt_states_number_word_rules():
    provider = FakeStructuredProvider(
        {"decision": "valid", "normalized_value": 1962, "confidence": 0.9,
         "reason_code": None, "follow_up": None}
    )
    provider.messages = None
    original = provider.generate_structured

    def capture(endpoint, model, messages, schema):
        provider.messages = messages
        return original(endpoint, model, messages, schema)

    provider.generate_structured = capture
    question = load_domain_pack().questions[1]

    LLMAnswerInterpreter(provider).interpret(
        "http://fake", "fake", question, "neunzehnhundertzweiundsechzig"
    )

    system = provider.messages[0]["content"]
    assert "Zahlwörter" in system
    assert "zweiundsechzig" in system and "1962" in system


def test_interpreter_accepts_boolean_and_list_values():
    question = load_domain_pack().questions[0]

    def interpret(value):
        provider = FakeStructuredProvider(
            {"decision": "valid", "normalized_value": value, "confidence": 0.9,
             "reason_code": None, "follow_up": None}
        )
        return LLMAnswerInterpreter(provider).interpret("http://fake", "fake", question, "x")

    assert interpret(True).normalized_value is True
    assert interpret(["email", "website"]).normalized_value == ["email", "website"]
    assert interpret(1).normalized_value == 1
    assert not isinstance(interpret(1).normalized_value, bool)
    assert interpret("true").normalized_value == "true"


def test_interpreter_schema_offers_boolean_and_string_list():
    schema = InterpretationResult.model_json_schema()

    assert schema["properties"]["normalized_value"]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]


def test_interpreter_prompt_states_type_rules():
    provider = FakeStructuredProvider(
        {"decision": "valid", "normalized_value": "x", "confidence": 0.9,
         "reason_code": None, "follow_up": None}
    )
    provider.messages = None
    original = provider.generate_structured

    def capture(endpoint, model, messages, schema):
        provider.messages = messages
        return original(endpoint, model, messages, schema)

    provider.generate_structured = capture

    LLMAnswerInterpreter(provider).interpret(
        "http://fake", "fake", load_domain_pack().questions[0], "x"
    )

    system = provider.messages[0]["content"]
    assert "multiselect" in system and "JSON-Liste" in system
    assert "true oder false" in system
    assert "JJJJ-MM-TT" in system
    assert "Wortlaut" in system
    assert "zum field_type passenden Wert" in system
