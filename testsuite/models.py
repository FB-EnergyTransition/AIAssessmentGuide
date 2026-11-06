from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Assertion(Model):
    path: str
    op: Literal["eq", "exists", "contains", "not_contains", "between", "min_length"]
    expected: Any = None

    @model_validator(mode="after")
    def validate_assertion(self):
        if self.path and (not self.path.startswith("/") or re.search(r"~(?![01])", self.path)):
            raise ValueError("Assertion benötigt einen JSON Pointer.")
        if self.op != "exists" and "expected" not in self.model_fields_set:
            raise ValueError("expected fehlt.")
        if self.op == "exists" and "expected" in self.model_fields_set and type(self.expected) is not bool:
            raise ValueError("exists erwartet einen booleschen Wert.")
        if self.op == "not_contains" and not isinstance(self.expected, (str, dict)):
            raise ValueError("not_contains erwartet eine Zeichenfolge oder ein Teilobjekt.")
        if self.op == "min_length" and (type(self.expected) is not int or self.expected < 0):
            raise ValueError("min_length erwartet eine ganze Zahl ab 0.")
        if self.op == "between":
            values = self.expected
            if (not isinstance(values, list) or len(values) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                    or values[0] > values[1]):
                raise ValueError("between erwartet [Minimum, Maximum].")
        return self


Action = Literal[
    "start_assessment", "get_session", "submit_answer", "submit_file_answer", "submit_text_answer",
    "revise_answer", "complete", "question_help",
    "accept_proposed_answer", "discard_proposed_answer", "get_report", "get_profile",
    "profile_recommendations", "update_profile_fact", "recommendation_feedback",
    "llm_recommendations", "create_conversation", "get_conversation",
    "list_conversations", "send_conversation_message", "delete_conversation",
    "upload_dataset", "get_dataset", "delete_dataset",
]

# Payload vocabulary is deliberately limited to the existing public REST API.
PAYLOAD_FIELDS = {
    "start_assessment": ({"locale"}, set()),
    "get_session": (set(), set()),
    "submit_answer": ({"question_id", "value", "raw_answer", "confidence"}, {"question_id", "value"}),
    # A file question: fixture = file name under testsuite/fixtures/datasets/,
    # sent as the answer value {filename, content, format_hint} of submit_answer.
    "submit_file_answer": ({"question_id", "fixture", "format_hint"}, {"question_id", "fixture"}),
    "submit_text_answer": ({"question_id", "text"}, {"question_id", "text"}),
    "revise_answer": ({"question_id", "value", "raw_answer", "confidence"}, {"question_id", "value"}),
    # knowledge_recommendations: true lets the API generate the first knowledge
    # recommendations at completion with its default model (an LLM step).
    "complete": ({"knowledge_recommendations"}, set()),
    "question_help": ({"question_id", "text"}, {"question_id", "text"}),
    "accept_proposed_answer": ({"message_id"}, {"message_id"}),
    "discard_proposed_answer": ({"message_id"}, {"message_id"}),
    "get_report": (set(), set()),
    "get_profile": (set(), set()),
    "profile_recommendations": (set(), set()),
    "update_profile_fact": ({"field_key", "value", "raw_answer"}, {"field_key", "value"}),
    "recommendation_feedback": ({"recommendation_id", "stance"}, {"recommendation_id", "stance"}),
    "llm_recommendations": (set(), set()),
    # Conversation steps use the case's latest conversation unless conversation_id is referenced.
    "create_conversation": ({"title"}, set()),
    "get_conversation": ({"conversation_id"}, set()),
    "list_conversations": (set(), set()),
    "send_conversation_message": ({"conversation_id", "text"}, {"text"}),
    "delete_conversation": ({"conversation_id"}, set()),
    # Measurement data for the case's profile: fixture = file name under
    # testsuite/fixtures/datasets/, sent as text to the pack's dataset tool.
    "upload_dataset": ({"fixture", "format_hint"}, {"fixture"}),
    "get_dataset": (set(), set()),
    "delete_dataset": (set(), set()),
}
# Both return the generated recommendations; the review view reads the last one.
RECOMMENDATION_ACTIONS = {"llm_recommendations", "profile_recommendations"}
LLM_ACTIONS = {"submit_text_answer", "question_help",
               "send_conversation_message", "llm_recommendations", "profile_recommendations"}


class Step(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    action: Action
    payload: dict[str, Any] = Field(default_factory=dict)
    # Destination payload field -> earlier step ID followed by #/json/pointer.
    references: dict[str, str] = Field(default_factory=dict)
    expected_http: int = Field(default=200, ge=100, le=599)
    expected_ok: bool | None = True
    assertions: list[Assertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self):
        allowed, required = PAYLOAD_FIELDS[self.action]
        keys = self.payload.keys() | self.references.keys()
        if not required <= keys or not keys <= allowed or self.payload.keys() & self.references.keys():
            raise ValueError(f"Ungültige Payload-Felder für {self.action}.")
        if self.action == "complete" and type(self.payload.get("knowledge_recommendations", False)) is not bool:
            raise ValueError("knowledge_recommendations muss true oder false sein.")
        return self

    @property
    def uses_llm(self) -> bool:
        return self.action in LLM_ACTIONS or (
            self.action == "complete" and self.payload.get("knowledge_recommendations") is True)


class Review(Model):
    """Angaben für die fachliche Bewertung im Beratungstest; ohne Einfluss auf Ausführung und Urteil."""
    persona: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    persona_label: str = Field(min_length=1)
    kind: Literal["advisory", "edge", "llm_recommendations"]
    check: str = Field(min_length=1)


class Scenario(Model):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    version: str = Field(min_length=1)
    domain_id: str = Field(min_length=1)
    domain_version: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    data_classification: Literal["synthetic"] = "synthetic"
    persist_raw: bool = False
    steps: list[Step] = Field(min_length=1)
    assertions: list[Assertion] = Field(default_factory=list)
    review: Review | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version muss eine ganze Zahl sein.")
        return value

    @model_validator(mode="after")
    def validate_steps(self):
        try:
            json.dumps(self.model_dump(), allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("Szenarien dürfen nur endliche JSON-Werte enthalten.") from exc
        if self.steps[0].action != "start_assessment":
            raise ValueError("Das Szenario muss mit start_assessment beginnen.")
        seen = set()
        for index, step in enumerate(self.steps):
            if step.id in seen:
                raise ValueError("Doppelte Schritt-ID.")
            if index and step.action == "start_assessment":
                raise ValueError("Pro Szenario ist genau eine neue Session erlaubt.")
            for reference in step.references.values():
                source, separator, pointer = reference.partition("#")
                if not separator or source not in seen:
                    raise ValueError("Referenzen müssen auf frühere Schritte zeigen.")
                Assertion(path=pointer, op="exists")
            seen.add(step.id)
        if self.review:
            if not self.persist_raw:
                raise ValueError("review benötigt persist_raw, sonst fehlen die Antworten für die Bewertung.")
            reviewed = (RECOMMENDATION_ACTIONS if self.review.kind == "llm_recommendations"
                        else {"send_conversation_message"})
            if all(step.action not in reviewed for step in self.steps):
                raise ValueError(
                    f"review.kind {self.review.kind} benötigt einen Schritt {' oder '.join(sorted(reviewed))}."
                )
        return self

    @property
    def requires_llm(self) -> bool:
        return any(step.uses_llm for step in self.steps)
