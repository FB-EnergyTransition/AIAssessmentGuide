"""The profile of one participant in one domain: the verified answers and
the result of their latest completed assessment, kept under a stable id so
advisory conversations can continue across assessments (concept step 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from guideos.assessment.models import AssessmentEvent, AssessmentResult, AssessmentSession
from guideos.tools.models import DatasetRef, ToolResult

AnswerOrigin = Literal["assessment", "chat_update", "measurement"]
class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


FeedbackStance = Literal["accepted", "rejected"]
FeedbackOrigin = Literal["button", "chat"]


class RecommendationFeedback(ProfileModel):
    """The participant's stance on one recommendation. kind and title are
    kept because knowledge recommendations get new ids on regeneration."""

    recommendation_id: str
    kind: Literal["rule", "llm"]
    title: str
    stance: FeedbackStance
    origin: FeedbackOrigin
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProfileAnswer(ProfileModel):
    """An answer in the same shape as on the session, plus where it came
    from, so the questionnaire, validation and rules engines run on a
    profile unchanged."""

    question_id: str
    field_key: str
    raw_answer: str
    normalized_value: Any
    confidence: float = Field(ge=0, le=1)
    origin: AnswerOrigin = "assessment"
    # The calculation a measurement-derived answer rests on.
    calculation_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Profile(ProfileModel):
    profile_id: str
    participant_id: str | None = None
    domain_id: str
    domain_version: str
    domain_content_hash: str
    source_session_id: str
    revision: int = Field(default=0, ge=0)
    answers: dict[str, ProfileAnswer] = Field(default_factory=dict)
    result: AssessmentResult
    # True after an answer changed since the knowledge recommendations were generated.
    knowledge_stale: bool = False
    feedback: dict[str, RecommendationFeedback] = Field(default_factory=dict)
    # The participant's dataset on a tool server and the last result per tool
    # (concept step 5b); both survive a newer assessment of the same domain.
    dataset: DatasetRef | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    events: list[AssessmentEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_session(cls, session: AssessmentSession) -> "Profile":
        """A new profile from a completed session."""
        _require_completed(session)
        now = datetime.now(UTC)
        return cls(
            profile_id=f"prof_{uuid4().hex}",
            participant_id=session.participant_id,
            domain_id=session.domain_id,
            domain_version=session.domain_version,
            domain_content_hash=session.domain_content_hash,
            source_session_id=session.session_id,
            answers=_answers_from(session, now),
            result=session.result,
            events=[
                AssessmentEvent(
                    event_type="created_from_session",
                    payload={"session_id": session.session_id},
                )
            ],
            created_at=now,
            updated_at=now,
        )

    def replace_with(self, session: AssessmentSession) -> "Profile":
        """Replace answers and result with a newer completed assessment of
        the same participant and domain. The profile id and revision stay,
        so repositories and conversations keep pointing at it."""
        _require_completed(session)
        if session.domain_id != self.domain_id:
            raise ValueError("Das Profil gehört zu einer anderen Domäne.")
        if session.participant_id != self.participant_id:
            raise ValueError("Das Profil gehört zu einem anderen Teilnehmer.")
        now = datetime.now(UTC)
        previous = self.source_session_id
        self.domain_version = session.domain_version
        self.domain_content_hash = session.domain_content_hash
        self.source_session_id = session.session_id
        self.answers = _answers_from(session, now)
        self.result = session.result
        self.knowledge_stale = False
        self.events = [
            *self.events,
            AssessmentEvent(
                event_type="replaced_by_session",
                payload={"previous_session_id": previous, "session_id": session.session_id},
            ),
        ]
        self.updated_at = now
        return self


def _require_completed(session: AssessmentSession) -> None:
    if session.status != "completed" or session.result is None:
        raise ValueError("Ein Profil entsteht nur aus einem abgeschlossenen Assessment.")


def _answers_from(session: AssessmentSession, now: datetime) -> dict[str, ProfileAnswer]:
    return {
        field_key: ProfileAnswer(
            question_id=answer.question_id,
            field_key=answer.field_key,
            raw_answer=answer.raw_answer,
            normalized_value=answer.normalized_value,
            confidence=answer.confidence,
            origin="measurement" if answer.origin == "measurement" else "assessment",
            calculation_id=answer.calculation_id,
            updated_at=now,
        )
        for field_key, answer in session.answers.items()
    }
