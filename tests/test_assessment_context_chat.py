import json

import pytest

from guideos.assessment.context_chat import (
    AssessmentChatError,
    LLMAssessmentChatAssistant,
    QuestionHelpResult,
)
from guideos.assessment.models import AssessmentChatMessage, AssessmentSession
from guideos.domains.loader import load_domain_pack


class FakeStructuredProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, result):
        self.result = result
        self.schema = None
        self.messages = None

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.schema = schema
        self.messages = messages
        return self.result


def test_question_help_result_schema_types_normalized_value_for_ollama():
    schema = QuestionHelpResult.model_json_schema()

    normalized_value = schema["properties"]["normalized_value"]

    assert normalized_value["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]
    assert "normalized_value" in schema["required"]


def test_respond_to_question_includes_question_history_and_answer_context():
    pack = load_domain_pack()
    session = _session(pack)
    session.chat_messages = [
        _message("user", "Was ist gemeint?", "club_type"),
        _message("assistant", "Der Gebäudetyp.", "club_type"),
        _message("user", "Andere Frage", "founded_year"),
    ]
    provider = FakeStructuredProvider(
        {
            "message": "Alles klar, Einfamilienhaus.",
            "proposes_answer": True,
            "normalized_value": "sports",
            "confidence": 0.9,
        }
    )
    assistant = LLMAssessmentChatAssistant(provider)

    result = assistant.respond_to_question(
        "http://fake",
        "fake",
        pack,
        session,
        pack.questions[0],
        "Ist ein Reihenhaus ein Einfamilienhaus?",
    )

    assert isinstance(result, QuestionHelpResult)
    assert result.normalized_value == "sports"
    assert provider.schema == QuestionHelpResult.model_json_schema()
    assert provider.messages[0]["role"] == "system"
    context = json.loads(provider.messages[1]["content"])
    assert provider.messages[1]["role"] == "user"
    assert set(context) >= {"domain", "question", "answers"}
    assert context["question"]["question_id"] == "club_type"
    assert [(m["role"], m["content"]) for m in provider.messages[2:]] == [
        ("user", "Was ist gemeint?"),
        ("assistant", "Der Gebäudetyp."),
        ("user", "Ist ein Reihenhaus ein Einfamilienhaus?"),
    ]


def test_respond_to_question_rejects_malformed_provider_output():
    pack = load_domain_pack()
    assistant = LLMAssessmentChatAssistant(FakeStructuredProvider({"message": "x"}))

    with pytest.raises(AssessmentChatError, match="strukturiert"):
        assistant.respond_to_question(
            "http://fake",
            "fake",
            pack,
            _session(pack),
            pack.questions[0],
            "Haus",
        )


def _session(pack):
    return AssessmentSession.create(
        domain_id=pack.domain_id,
        domain_version=pack.version,
        domain_content_hash=pack.content_hash,
        locale=pack.default_locale,
    )


def _message(role, content, question_id):
    return AssessmentChatMessage(
        scope="question_help",
        role=role,
        content=content,
        question_id=question_id,
    )


def test_question_help_prompt_keeps_explanations_factual_and_option_bound():
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Erklärung", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    LLMAssessmentChatAssistant(provider).ask_question_help(
        "http://fake", "fake", pack, _session(pack), pack.questions[0], "Was bedeutet Einfamilienhaus?"
    )

    system = provider.messages[0]["content"]
    assert "Optionen" in system
    assert "Erfinde keine" in system
    assert "kurz" in system


def test_question_help_prompt_states_number_word_rules():
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Erklärung", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    LLMAssessmentChatAssistant(provider).ask_question_help(
        "http://fake", "fake", pack, _session(pack), pack.questions[1], "Das Haus ist von neunzehnhundertzweiundsechzig."
    )

    system = provider.messages[0]["content"]
    assert "Zahlwörter" in system
    assert "zweiundsechzig" in system and "1962" in system


def test_respond_to_question_prompt_states_number_word_rules():
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Alles klar.", "proposes_answer": True, "normalized_value": 1962, "confidence": 0.9}
    )

    LLMAssessmentChatAssistant(provider).respond_to_question(
        "http://fake", "fake", pack, _session(pack), pack.questions[1], "neunzehnhundertzweiundsechzig"
    )

    system = provider.messages[0]["content"]
    assert "value" in system and "label" in system
    assert "Zahlwörter" in system
    assert "zweiundsechzig" in system and "1962" in system


def test_question_help_prompt_demands_a_direct_answer_from_pack_facts():
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Erklärung", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    LLMAssessmentChatAssistant(provider).ask_question_help(
        "http://fake", "fake", pack, _session(pack), pack.questions[0], "Zählt ein Reihenhaus als Einfamilienhaus?"
    )

    system = provider.messages[0]["content"]
    assert "help_text" in system
    assert "zuerst" in system.lower()
    assert "wiederhole" in system.lower()
    assert "Beispiel" in system
    assert "hypothetischen" in system
    assert "allow_unknown" in system


def test_respond_to_question_prompt_demands_a_direct_answer_on_follow_ups():
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Erklärung", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    LLMAssessmentChatAssistant(provider).respond_to_question(
        "http://fake", "fake", pack, _session(pack), pack.questions[0], "Zählt ein Reihenhaus als Einfamilienhaus?"
    )

    system = provider.messages[0]["content"]
    assert "help_text" in system
    assert "zuerst" in system.lower()
    assert "wiederhole" in system.lower()
    assert "hypothetischen" in system
    assert "allow_unknown" in system


def test_question_chat_results_keep_boolean_and_list_values():
    pack = load_domain_pack()

    def respond(value):
        provider = FakeStructuredProvider(
            {"message": "Ok.", "proposes_answer": True, "normalized_value": value, "confidence": 0.9}
        )
        return LLMAssessmentChatAssistant(provider).respond_to_question(
            "http://fake", "fake", pack, _session(pack), pack.questions[0], "x"
        )

    assert respond(False).normalized_value is False
    assert respond(["a", "b"]).normalized_value == ["a", "b"]
    assert respond(1).normalized_value == 1 and not isinstance(respond(1).normalized_value, bool)


@pytest.mark.parametrize("method", ["ask_question_help", "respond_to_question"])
def test_chat_prompts_state_type_rules(method):
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Ok.", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    getattr(LLMAssessmentChatAssistant(provider), method)(
        "http://fake", "fake", pack, _session(pack), pack.questions[0], "x"
    )

    system = provider.messages[0]["content"]
    assert "multiselect" in system and "JSON-Liste" in system
    assert "true oder false" in system
    assert "JJJJ-MM-TT" in system
    assert "Wortlaut" in system


@pytest.mark.parametrize("method", ["ask_question_help", "respond_to_question"])
def test_chat_prompts_tell_the_model_to_enumerate_options_on_request(method):
    pack = load_domain_pack()
    provider = FakeStructuredProvider(
        {"message": "Ok.", "proposes_answer": False, "normalized_value": None, "confidence": 0.0}
    )

    getattr(LLMAssessmentChatAssistant(provider), method)(
        "http://fake", "fake", pack, _session(pack), pack.questions[0], "x"
    )

    system = provider.messages[0]["content"]
    assert "zähle alle Optionen" in system
