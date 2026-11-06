"""Reads a chat message for a changed fact about the participant. The
model only proposes a field and a value; validation, confirmation and the
write happen deterministically in GuideOS."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from guideos.assessment.prompt import ANSWER_NORMALIZATION_RULES, ANSWER_TYPE_RULES
from guideos.chat import Message
from guideos.domains.models import DomainPack, display_value
from guideos.profiles.models import Profile
from guideos.providers.base import LLMProvider, ProviderError


class ProfileChangeProposal(BaseModel):
    """field_key null: the message states no changed fact (a question, a
    plan, a hypothesis or something no question covers).
    feedback_recommendation_id names a recommendation the user explicitly
    endorses or rejects, with feedback_stance; otherwise both are null."""

    model_config = ConfigDict(extra="forbid")

    field_key: str | None = None
    value: str | int | float | bool | list[str] | None = None
    feedback_recommendation_id: str | None = None
    feedback_stance: Literal["accepted", "rejected"] | None = None

    @field_validator("field_key", "feedback_recommendation_id", mode="before")
    @classmethod
    def placeholder_strings_are_none(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "n/a"}:
            return None
        return value


class ProfileChangeInterpretationError(Exception):
    pass


CHANGE_INTERPRETER_POLICY = (
    "Du prüfst, ob die Nachricht eines Nutzers eine geänderte Angabe zu "
    "seiner eigenen Situation enthält, die genau eine der aufgeführten Fragen "
    "(questions) neu beantwortet. Du erhältst die Fragen mit field_key, "
    "Fragetext, field_type und Optionen sowie die aktuellen Antworten "
    "(current_answers). Gib ein JSON-Objekt gemäß Schema zurück.\n"
    "Regeln:\n"
    "- Nur eine tatsächliche, gegenwärtige Änderung zählt (z. B. 'wir sind "
    "jetzt 140 Mitglieder', 'der Verein wurde 1980 gegründet'). Fragen, Überlegungen, "
    "Pläne, Wünsche und hypothetische Aussagen ('ich überlege', 'was wäre "
    "wenn', 'lohnt sich') sind keine Änderung: field_key null.\n"
    "- Passt die Aussage zu keiner Frage oder bleibt unklar, welche Frage "
    "gemeint ist: field_key null. Erfinde keine Werte.\n"
    "- value ist bei Auswahlfragen der Optionswert (value), nie das label; "
    "bei Zahlen die Zahl ohne Einheit; bei Ja/Nein-Fragen true oder false; "
    "bei Datumsfragen das ISO-Datum.\n"
    "- Nenne nur eine Änderung; bei mehreren die zuerst genannte.\n"
    "- Nimmt der Nutzer ausdrücklich zu einer der aufgeführten Empfehlungen "
    "(recommendations) Stellung, etwa 'kommt für uns nicht in Frage', "
    "'machen wir auf jeden Fall', nenne deren recommendation_id in "
    "feedback_recommendation_id und die Haltung in feedback_stance "
    "('rejected' oder 'accepted'). Eine Frage zu einer Empfehlung ist keine "
    "Stellungnahme: beide Felder null.\n"
) + ANSWER_NORMALIZATION_RULES + ANSWER_TYPE_RULES


class LLMProfileChangeInterpreter:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def interpret(
        self,
        endpoint: str,
        model: str,
        pack: DomainPack,
        profile: Profile,
        text: str,
    ) -> ProfileChangeProposal:
        questions = {question.field_key: question for question in pack.questions}
        messages: list[Message] = [
            {"role": "system", "content": CHANGE_INTERPRETER_POLICY},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "questions": [
                            {
                                "field_key": question.field_key,
                                "question_text": question.text,
                                "field_type": question.field_type,
                                "options": [
                                    option.model_dump(mode="json") for option in question.options
                                ],
                                "validation": question.validation.model_dump(
                                    mode="json", exclude_none=True
                                ),
                            }
                            for question in pack.questions
                        ],
                        "current_answers": {
                            field_key: {
                                "normalized_value": answer.normalized_value,
                                "display_value": (
                                    display_value(questions[field_key], answer.normalized_value)
                                    if field_key in questions
                                    else None
                                ),
                            }
                            for field_key, answer in profile.answers.items()
                        },
                        "recommendations": [
                            {"recommendation_id": item.recommendation_id, "title": item.title}
                            for item in (
                                *profile.result.recommendations,
                                *profile.result.knowledge_recommendations,
                            )
                        ],
                        "message": text,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            data = self.provider.generate_structured(
                endpoint, model, messages, ProfileChangeProposal.model_json_schema()
            )
            return ProfileChangeProposal.model_validate(data)
        except ProviderError:
            raise
        except ValidationError as exc:
            raise ProfileChangeInterpretationError(
                "Die Antwort des Modells war nicht korrekt strukturiert."
            ) from exc
