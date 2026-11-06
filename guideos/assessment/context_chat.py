import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from guideos.assessment.models import AssessmentChatMessage, AssessmentSession
from guideos.chat import Message
from guideos.domains.models import DomainPack, QuestionDefinition
from guideos.assessment.prompt import ANSWER_NORMALIZATION_RULES, ANSWER_TYPE_RULES
from guideos.providers.base import LLMProvider, ProviderError


class QuestionHelpResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    proposes_answer: bool
    normalized_value: str | int | float | bool | list[str] | None
    confidence: float = Field(ge=0, le=1)


class AssessmentChatError(Exception):
    pass


class LLMAssessmentChatAssistant:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def ask_question_help(
        self,
        endpoint: str,
        model: str,
        pack: DomainPack,
        session: AssessmentSession,
        question: QuestionDefinition,
        text: str,
    ) -> QuestionHelpResult:
        messages: list[Message] = [
            {
                "role": "system",
                "content": (
                    "Du hilfst in einem geführten Assessment. Beantworte "
                    "zuerst die konkrete Rückfrage des Nutzers in ein bis drei "
                    "Sätzen, kurz, sachlich richtig und ohne Spekulation. "
                    "Nutze dafür ausschließlich den help_text der Frage und "
                    "ihre Optionen (label); grenze die Optionen voneinander ab, "
                    "wenn der Nutzer danach fragt. Wiederhole nicht den "
                    "Fragetext, schreibe keine Einleitung oder Metakommentare "
                    "und antworte nie nur mit 'ja' oder 'nein', sondern "
                    "begründe aus dem help_text. Fragt der Nutzer, welche "
                    "Antworten oder Optionen es gibt, zähle alle Optionen (label) "
                    "der Frage auf. Geben help_text und Optionen "
                    "keine Grundlage für die Rückfrage, sage das offen und "
                    "nenne, was die Frage erfasst. Erfinde keine Definitionen "
                    "und keine Nutzerangaben. Aussagen zum Ablauf, etwa ob eine "
                    "Frage übersprungen oder als unbekannt gespeichert werden "
                    "kann, leitest du nur aus required und allow_unknown der "
                    "Frage ab. Wenn der Nutzer eine konkrete Angabe zu seiner "
                    "eigenen Situation macht, darfst du einen normalisierten "
                    "Antwortvorschlag (value) liefern; bei allgemeinen oder "
                    "hypothetischen Fragen bleibt der Vorschlag leer. Speichere "
                    "nie selbst eine Antwort. Antworte auf Deutsch. Gib "
                    "ausschließlich JSON gemäß Schema zurück.\n"
                    "Beispiel für den Aufbau von message: erster Satz ist die "
                    "direkte Antwort auf die Rückfrage, danach ein bis zwei "
                    "Sätze Begründung aus help_text oder Optionen; kein "
                    "Fragetext, keine Einleitung.\n"
                ) + ANSWER_NORMALIZATION_RULES + ANSWER_TYPE_RULES,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "domain": {
                            "domain_id": pack.domain_id,
                            "version": pack.version,
                            "name": pack.name,
                            "description": pack.description,
                        },
                        "question": question.model_dump(mode="json"),
                        "answers": _answered_context(pack, session),
                        "user_message": text,
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
                QuestionHelpResult.model_json_schema(),
            )
            return QuestionHelpResult.model_validate(data)
        except ProviderError:
            raise
        except ValidationError as exc:
            raise AssessmentChatError(
                "Die LLM-Antwort war nicht korrekt strukturiert."
            ) from exc

    def respond_to_question(
        self,
        endpoint: str,
        model: str,
        pack: DomainPack,
        session: AssessmentSession,
        question: QuestionDefinition,
        text: str,
    ) -> QuestionHelpResult:
        messages: list[Message] = [
            {
                "role": "system",
                "content": (
                    "Du führst ein geführtes Assessment als Chat. Die aktuelle "
                    "Frage ist im JSON definiert: Optionen mit value und label, "
                    "Zahlengrenzen und allow_unknown. Beantwortet die Nachricht "
                    "des Nutzers die Frage, setze proposes_answer=true und "
                    "normalized_value auf den passenden Optionswert (value, nicht "
                    "label), auf die Zahl bzw. auf den zum field_type passenden "
                    "Wert (siehe Regeln je field_type); ordne Synonyme dem passenden "
                    "Optionswert zu. Kennt der Nutzer den Wert nicht, setze "
                    "proposes_answer=true und normalized_value=null. Bei "
                    "Rückfragen oder mehrdeutigen Angaben setze "
                    "proposes_answer=false und beantworte zuerst die konkrete "
                    "Rückfrage in ein bis drei Sätzen aus dem help_text und "
                    "den Optionen der Frage; wiederhole nicht den Fragetext, "
                    "schreibe keine Einleitung und antworte nie nur mit 'ja' "
                    "oder 'nein'. Fragt der Nutzer, welche Antworten oder Optionen "
                    "es gibt, zähle alle Optionen (label) der Frage auf. Geben "
                    "help_text und Optionen keine Grundlage, "
                    "sage das offen und frage nach. Aussagen zum Ablauf, etwa ob "
                    "eine Frage übersprungen oder als unbekannt gespeichert "
                    "werden kann, leitest du nur aus required und allow_unknown "
                    "ab; bei allgemeinen oder hypothetischen Fragen setze "
                    "proposes_answer=false. "
                    "Formuliere message immer kurz auf Deutsch. Erfinde keine "
                    "Werte. Speichere nie selbst eine Antwort. Gib "
                    "ausschließlich JSON gemäß Schema zurück.\n"
                ) + ANSWER_NORMALIZATION_RULES + ANSWER_TYPE_RULES,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "domain": {
                            "domain_id": pack.domain_id,
                            "version": pack.version,
                            "name": pack.name,
                            "description": pack.description,
                        },
                        "question": question.model_dump(mode="json"),
                        "answers": _answered_context(pack, session),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        messages.extend(
            _question_history_messages(session.chat_messages, question.question_id)
        )
        messages.append({"role": "user", "content": text})
        try:
            data = self.provider.generate_structured(
                endpoint,
                model,
                messages,
                QuestionHelpResult.model_json_schema(),
            )
            return QuestionHelpResult.model_validate(data)
        except ProviderError:
            raise
        except ValidationError as exc:
            raise AssessmentChatError(
                "Die LLM-Antwort war nicht korrekt strukturiert."
            ) from exc


def _question_history_messages(
    messages: list[AssessmentChatMessage],
    question_id: str,
) -> list[Message]:
    return [
        {"role": message.role, "content": message.content}
        for message in messages
        if message.scope == "question_help" and message.question_id == question_id
    ]


def _answered_context(
    pack: DomainPack,
    session: AssessmentSession,
) -> list[dict[str, Any]]:
    questions = {question.field_key: question for question in pack.questions}
    context = []
    for field_key, answer in session.answers.items():
        question = questions.get(field_key)
        context.append(
            {
                "field_key": field_key,
                "question_id": answer.question_id,
                "question_text": question.text if question else None,
                "normalized_value": answer.normalized_value,
                "raw_answer": answer.raw_answer,
                "confidence": answer.confidence,
            }
        )
    return context
