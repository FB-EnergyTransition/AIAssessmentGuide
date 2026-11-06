import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from guideos.chat import Message
from guideos.domains.models import QuestionDefinition
from guideos.assessment.prompt import ANSWER_NORMALIZATION_RULES, ANSWER_TYPE_RULES
from guideos.providers.base import LLMProvider, ProviderError


class InterpretationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["valid", "uncertain", "invalid", "not_answered"]
    normalized_value: str | int | float | bool | list[str] | None
    confidence: float = Field(ge=0, le=1)
    reason_code: str | None
    follow_up: str | None

    @field_validator("reason_code", "follow_up", mode="before")
    @classmethod
    def placeholder_strings_are_none(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "n/a"}:
            return None
        return value


class AnswerInterpretationError(Exception):
    pass


INTERPRETER_POLICY = (
    "Du interpretierst ausschließlich die Antwort eines Nutzers auf die "
    "angegebene Frage und gibst ein JSON-Objekt gemäß Schema zurück.\n"
    "Entscheidungsregeln:\n"
    "- 'valid': Die Antwort nennt genau eine passende Option, einen "
    "brauchbaren Zahlenwert oder einen zum field_type passenden Wert (siehe "
    "Regeln je field_type). Bei Auswahlfragen ist normalized_value der "
    "Optionswert (value), nie das label; ordne Synonyme und Umschreibungen "
    "der passenden Option zu (z. B. 'Ich wohne in einem Einfamilienhaus' -> "
    "value der Option mit label 'Einfamilienhaus'). Bei Zahlenfragen ist "
    "normalized_value die Zahl ohne Einheit; 'ca.' oder 'ungefähr' sind "
    "kein Grund für Unsicherheit.\n"
    "- 'not_answered': Der Nutzer sagt, dass er den Wert nicht kennt "
    "(normalized_value null).\n"
    "- 'uncertain': Nur wenn die Antwort zu mehreren Optionen passt oder "
    "keinen Bezug zur Frage hat; formuliere dann in follow_up eine kurze "
    "deutsche Rückfrage.\n"
    "- 'invalid': Der genannte Wert verletzt die angegebenen Grenzen.\n"
    "follow_up ist null, wenn keine Rückfrage nötig ist; schreibe nie "
    "'None' als Text. Erfinde keine Werte. Antworte auf Deutsch.\n"
) + ANSWER_NORMALIZATION_RULES + ANSWER_TYPE_RULES


class LLMAnswerInterpreter:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def interpret(
        self,
        endpoint: str,
        model: str,
        question: QuestionDefinition,
        text: str,
    ) -> InterpretationResult:
        messages: list[Message] = [
            {"role": "system", "content": INTERPRETER_POLICY},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question.model_dump(mode="json"),
                        "answer": text,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            data = self.provider.generate_structured(
                endpoint,
                model,
                messages,
                InterpretationResult.model_json_schema(),
            )
            return InterpretationResult.model_validate(data)
        except ProviderError:
            raise
        except ValidationError as exc:
            raise AnswerInterpretationError(
                "Die LLM-Antwort war nicht korrekt strukturiert."
            ) from exc
