from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from guideos.knowledge.models import SourceRef
from guideos.tools.models import ToolResult


ConversationRole = Literal["user", "assistant"]


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MessageSection(ConversationModel):
    """One part of an assistant message and the sources it rests on."""

    text: str
    sources: list[SourceRef] = Field(default_factory=list)
    # Figures in text that occur neither in the cited sources nor in the session context.
    unverified_values: list[str] = Field(default_factory=list)


class ConversationMessage(ConversationModel):
    """content is the full text shown to the user, source lines included;
    sections carries the same statements with structured provenance."""

    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    role: ConversationRole
    content: str
    sections: list[MessageSection] = Field(default_factory=list)
    # Tool results GuideOS executed for this answer (model-chosen or dataset import).
    tool_results: list[ToolResult] = Field(default_factory=list)
    # The LLM model that produced an assistant answer; None for user messages
    # and for deterministic replies (confirmations, notices, offers).
    model: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


PendingKind = Literal["confirm_change", "confirm_feedback", "answer_question", "regenerate_offer"]


class PendingAction(ConversationModel):
    """What the next user message answers: the confirmation of a proposed
    profile change, an open follow-up question, or the offer to regenerate
    the knowledge recommendations."""

    kind: PendingKind
    field_key: str | None = None
    value: Any = None
    raw_answer: str | None = None
    recommendation_id: str | None = None
    stance: Literal["accepted", "rejected"] | None = None


class Conversation(ConversationModel):
    """One independent advisory dialogue. It references the participant's
    profile of the domain via profile_id and never owns verified user facts.
    result_session_id names the completed assessment whose result the
    "Ergebnis" chat last presented, so a newer completion appends its
    result once."""

    conversation_id: str
    participant_id: str | None = None
    domain_id: str
    title: str
    profile_id: str | None = None
    result_session_id: str | None = None
    pending: PendingAction | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        domain_id: str,
        title: str,
        participant_id: str | None = None,
        profile_id: str | None = None,
    ) -> "Conversation":
        now = datetime.now(UTC)
        return cls(
            conversation_id=f"conv_{uuid4().hex}",
            participant_id=participant_id,
            domain_id=domain_id,
            title=title,
            profile_id=profile_id,
            created_at=now,
            updated_at=now,
        )

    def add_message(
        self,
        role: ConversationRole,
        content: str,
        sections: list[MessageSection] | None = None,
        tool_results: list[ToolResult] | None = None,
        model: str | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(role=role, content=content, sections=sections or [],
                                      tool_results=tool_results or [], model=model)
        self.messages.append(message)
        self.updated_at = datetime.now(UTC)
        return message
