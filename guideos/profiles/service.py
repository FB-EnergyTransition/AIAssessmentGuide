"""Changing single answers of a profile after the assessment (concept
step 3): deterministic validation and visibility, open follow-up questions,
immediate rule recomputation. Knowledge recommendations are never
regenerated here; they stay and are marked stale."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from guideos.assessment.models import AssessmentEvent
from guideos.domains.catalog import DomainCatalogError, load_domain_pack_version
from guideos.domains.loader import DomainPackError
from guideos.domains.models import DomainPack, QuestionDefinition
from guideos.profiles.models import (
    FeedbackOrigin,
    FeedbackStance,
    Profile,
    ProfileAnswer,
    RecommendationFeedback,
)
from guideos.profiles.repository import ProfileRepository
from guideos.questionnaire.engine import is_question_visible
from guideos.rules.engine import evaluate_assessment
from guideos.validation.engine import AnswerValidationError, validate_answer

from guideos.tools.models import DatasetRef

if TYPE_CHECKING:
    from guideos.assessment.models import AssessmentSession
    from guideos.assessment.repository import AssessmentRepository
    from guideos.conversations.repository import ConversationRepository

logger = logging.getLogger(__name__)


class ProfileDeletion(BaseModel):
    """What the participant's deletion of a domain profile removed."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    conversation_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)


def delete_profile_data(
    profiles: ProfileRepository,
    conversations: ConversationRepository,
    assessments: AssessmentRepository,
    domain_id: str,
    *,
    tools=None,
    load_pack=load_domain_pack_version,
) -> ProfileDeletion:
    """Delete the participant's profile of a domain together with its
    conversations and the participant's assessment sessions of that domain
    (decision 2026-10-12: "Profil löschen" removes everything the domain
    holds about the participant). The repositories are already scoped to
    the participant. LookupError without a profile; nothing is deleted then."""
    profile = profiles.get(domain_id)
    deleted_ids: set[str] = set()
    if tools is not None and profile.dataset is not None:
        delete_dataset_on_server(profile, tools, load_pack)
        deleted_ids.add(profile.dataset.dataset_id)
    conversation_ids = [
        conversation.conversation_id
        for conversation in conversations.list()
        if conversation.profile_id == profile.profile_id
    ]
    for conversation_id in conversation_ids:
        conversations.delete(conversation_id)
    sessions = [session for session in assessments.list() if session.domain_id == domain_id]
    if tools is not None:
        # Datasets uploaded in file questions of these sessions (also of
        # unfinished assessments), each once.
        delete_session_datasets_on_server(sessions, tools, load_pack, skip_ids=deleted_ids)
    session_ids = [session.session_id for session in sessions]
    for session_id in session_ids:
        assessments.delete(session_id)
    profiles.delete(domain_id)
    logger.info(
        "profile_deleted domain_id=%s profile_id=%s conversations=%d sessions=%d",
        domain_id, profile.profile_id, len(conversation_ids), len(session_ids),
    )
    return ProfileDeletion(
        profile_id=profile.profile_id, conversation_ids=conversation_ids, session_ids=session_ids
    )

def delete_dataset_on_server(profile, tools, load_pack=load_domain_pack_version) -> bool:
    """Best effort: call the pack's delete tool for the profile's dataset.
    A failure is logged with the dataset id so that it can be deleted later;
    the deletion in GuideOS goes on."""
    from guideos.tools.models import ToolError

    dataset = profile.dataset
    if dataset is None:
        return False
    try:
        pack = load_pack(profile.domain_id, profile.domain_version)
    except (DomainCatalogError, DomainPackError) as exc:
        logger.warning("dataset_delete_failed profile_id=%s dataset_id=%s reason=%s",
                       profile.profile_id, dataset.dataset_id, exc)
        return False
    if pack.tools is None:
        return False
    try:
        tools.call(profile.domain_id, profile.participant_id or "local", pack.tools.delete_tool,
                   {"dataset_id": dataset.dataset_id})
    except ToolError as exc:
        logger.warning("dataset_delete_failed profile_id=%s dataset_id=%s code=%s",
                       profile.profile_id, dataset.dataset_id, exc.code)
        return False
    logger.info("dataset_deleted profile_id=%s dataset_id=%s", profile.profile_id, dataset.dataset_id)
    return True


def delete_datasets_on_server(profiles, tools, load_pack=load_domain_pack_version) -> int:
    """delete_dataset_on_server for every profile with a dataset; returns how
    many deletions the servers confirmed."""
    return sum(1 for profile in profiles if profile.dataset is not None
               and delete_dataset_on_server(profile, tools, load_pack))


def dataset_refs(session: AssessmentSession) -> list[DatasetRef]:
    """The datasets the file answers of a session reference (imported ones
    only; a skipped file question has no value)."""
    refs = []
    for answer in session.answers.values():
        value = answer.normalized_value
        if not isinstance(value, dict) or not value.get("dataset_id"):
            continue
        try:
            refs.append(DatasetRef.model_validate(value))
        except ValueError:
            continue
    return refs


def delete_session_datasets_on_server(
    sessions, tools, load_pack=load_domain_pack_version, *, skip_ids=(),
) -> list[str]:
    """Best effort: call the pack's delete tool for every dataset the file
    answers of these sessions reference, each dataset id once and never one
    in skip_ids (already deleted, or still used by a kept profile). Returns
    the ids a deletion was attempted for."""
    from guideos.tools.datasets import DatasetImporter

    importer = DatasetImporter(tools)
    attempted: list[str] = []
    seen = set(skip_ids)
    for session in sessions:
        refs = [ref for ref in dataset_refs(session) if ref.dataset_id not in seen]
        if not refs:
            continue
        try:
            pack = load_pack(session.domain_id, session.domain_version)
        except (DomainCatalogError, DomainPackError) as exc:
            logger.warning("dataset_delete_failed session_id=%s reason=%s", session.session_id, exc)
            continue
        if pack.tools is None:
            continue
        for ref in refs:
            seen.add(ref.dataset_id)
            attempted.append(ref.dataset_id)
            importer.delete(session.domain_id, session.participant_id, pack.tools.delete_tool, ref.dataset_id)
    return attempted


class SessionDeletion(BaseModel):
    """What the deletion of one assessment session removed."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    profile_changed: bool = False


def delete_session_data(
    assessments: AssessmentRepository,
    profiles: ProfileRepository | None,
    session_id: str,
    *,
    tools=None,
    load_pack=load_domain_pack_version,
) -> SessionDeletion:
    """Delete one assessment session together with the datasets its file
    answers reference (decision 2026-10-20: also when the participant's
    profile still uses them). The profile then forgets those datasets: the
    affected answers become unknown, the dataset for the advisory and the
    tool results derived from it are dropped. LookupError without the
    session."""
    session = assessments.get(session_id)
    refs = dataset_refs(session)
    deleted = [ref.dataset_id for ref in refs]
    if tools is not None and refs:
        delete_session_datasets_on_server([session], tools, load_pack)
    profile_changed = False
    if profiles is not None and refs:
        try:
            profile = profiles.get(session.domain_id, session.participant_id)
        except LookupError:
            profile = None
        if profile is not None:
            profile_changed = _forget_datasets(profile, session_id, deleted)
            if profile_changed:
                profiles.save(profile)
    assessments.delete(session_id)
    logger.info("session_deleted session_id=%s datasets=%d profile_changed=%s",
                session_id, len(deleted), profile_changed)
    return SessionDeletion(session_id=session_id, dataset_ids=deleted, profile_changed=profile_changed)


def _forget_datasets(profile: Profile, session_id: str, dataset_ids: list[str]) -> bool:
    ids = set(dataset_ids)
    changed = False
    if profile.dataset is not None and profile.dataset.dataset_id in ids:
        profile.dataset = None
        profile.tool_results = []
        changed = True
    now = datetime.now(UTC)
    for answer in profile.answers.values():
        value = answer.normalized_value
        if isinstance(value, dict) and value.get("dataset_id") in ids:
            answer.normalized_value = None
            answer.raw_answer = "unbekannt"
            answer.origin = "chat_update"
            answer.updated_at = now
            changed = True
    if changed:
        profile.events = [
            *profile.events,
            AssessmentEvent(event_type="dataset_deleted",
                            payload={"session_id": session_id, "dataset_ids": dataset_ids}),
        ]
        profile.updated_at = now
    return changed


ProfileServiceErrorCode = Literal[
    "profile_not_found",
    "domain_version_unavailable",
    "invalid_question",
    "invalid_recommendation",
    "validation_failed",
]


class ProfileServiceError(Exception):
    def __init__(
        self,
        message: str,
        code: ProfileServiceErrorCode,
        validation_code: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.validation_code = validation_code


class ProfileUpdate(BaseModel):
    """Outcome of one changed answer: the saved profile, what changed, which
    required questions became open, and whether the rule result changed."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    profile: Profile
    changed_field: str
    previous_value: Any = None
    open_questions: list[QuestionDefinition] = Field(default_factory=list)
    result_changed: bool


class ProfileService:
    def __init__(
        self,
        profiles: ProfileRepository,
        load_pack: Callable[[str, str], DomainPack] = load_domain_pack_version,
    ):
        self.profiles = profiles
        self.load_pack = load_pack

    def resolve(self, domain_id: str, participant_id: str | None) -> tuple[Profile, DomainPack]:
        """The profile and the pack version it is bound to."""
        try:
            profile = self.profiles.get(domain_id, participant_id)
        except LookupError as exc:
            raise ProfileServiceError(str(exc), code="profile_not_found") from exc
        try:
            pack = self.load_pack(profile.domain_id, profile.domain_version)
        except (DomainCatalogError, DomainPackError) as exc:
            raise ProfileServiceError(
                "Die Domain-Version des Profils ist nicht mehr verfügbar.",
                code="domain_version_unavailable",
            ) from exc
        if pack.content_hash != profile.domain_content_hash:
            raise ProfileServiceError(
                "Die Domain-Version des Profils ist nicht mehr verfügbar.",
                code="domain_version_unavailable",
            )
        return profile, pack

    def open_questions(self, domain_id: str, participant_id: str | None) -> list[QuestionDefinition]:
        profile, pack = self.resolve(domain_id, participant_id)
        return _open_questions(pack, profile)

    def check_fact(
        self,
        domain_id: str,
        participant_id: str | None,
        field_key: str,
        value: Any,
    ) -> tuple[Profile, DomainPack, QuestionDefinition, Any]:
        """Validate a changed answer without storing it: the profile, its
        pack, the question and the normalized value."""
        profile, pack = self.resolve(domain_id, participant_id)
        question = next((q for q in pack.questions if q.field_key == field_key), None)
        if question is None or not is_question_visible(pack, profile, question):
            raise ProfileServiceError(
                "Diese Angabe gibt es im Profil nicht oder sie ist derzeit nicht relevant.",
                code="invalid_question",
            )
        try:
            normalized = validate_answer(question, _by_label(question, value))
        except AnswerValidationError as exc:
            raise ProfileServiceError(
                str(exc), code="validation_failed", validation_code=exc.code
            ) from exc
        return profile, pack, question, normalized

    def update_fact(
        self,
        domain_id: str,
        participant_id: str | None,
        field_key: str,
        value: Any,
        raw_answer: str | None = None,
    ) -> ProfileUpdate:
        """Validate and store one changed answer, drop answers of questions
        that are no longer visible, recompute the rule result and report the
        required questions that are still open. A rejected value leaves the
        profile untouched."""
        profile, pack, question, normalized = self.check_fact(
            domain_id, participant_id, field_key, value
        )
        now = datetime.now(UTC)
        previous = profile.answers.get(field_key)
        profile.answers[field_key] = ProfileAnswer(
            question_id=question.question_id,
            field_key=field_key,
            raw_answer=raw_answer if raw_answer is not None else _raw(value),
            normalized_value=normalized,
            confidence=1.0,
            origin="chat_update",
            updated_at=now,
        )
        _remove_invisible_answers(pack, profile)
        events = [
            AssessmentEvent(
                event_type="fact_updated", payload={"field_key": field_key, "origin": "chat_update"}
            )
        ]
        result = evaluate_assessment(pack, profile)
        result_changed = (
            result.reason_codes != profile.result.reason_codes
            or [r.recommendation_id for r in result.recommendations]
            != [r.recommendation_id for r in profile.result.recommendations]
        )
        result.knowledge_recommendations = profile.result.knowledge_recommendations
        result.knowledge_generation = profile.result.knowledge_generation
        profile.result = result
        if profile.result.knowledge_generation is not None:
            profile.knowledge_stale = True
        events.append(AssessmentEvent(event_type="result_recomputed", payload={"trigger": "fact_updated"}))
        profile.events = [*profile.events, *events]
        profile.updated_at = now
        self.profiles.save(profile)
        open_questions = _open_questions(pack, profile)
        logger.info(
            "profile_fact_updated profile_id=%s field_key=%s result_changed=%s open_questions=%d",
            profile.profile_id,
            field_key,
            result_changed,
            len(open_questions),
        )
        return ProfileUpdate(
            profile=profile,
            changed_field=field_key,
            previous_value=previous.normalized_value if previous else None,
            open_questions=open_questions,
            result_changed=result_changed,
        )

    def set_feedback(
        self,
        domain_id: str,
        participant_id: str | None,
        recommendation_id: str,
        stance: FeedbackStance,
        origin: FeedbackOrigin,
    ) -> Profile:
        """Store the participant's stance on a rule or knowledge
        recommendation of the current result; a repeated stance replaces
        the earlier one. Rule recommendations stay visible either way."""
        profile, _ = self.resolve(domain_id, participant_id)
        known = {item.recommendation_id: ("rule", item.title) for item in profile.result.recommendations}
        known.update(
            {item.recommendation_id: ("llm", item.title) for item in profile.result.knowledge_recommendations}
        )
        if recommendation_id not in known:
            raise ProfileServiceError(
                "Diese Empfehlung gibt es im aktuellen Ergebnis nicht.",
                code="invalid_recommendation",
            )
        kind, title = known[recommendation_id]
        now = datetime.now(UTC)
        profile.feedback = {
            **profile.feedback,
            recommendation_id: RecommendationFeedback(
                recommendation_id=recommendation_id, kind=kind, title=title,
                stance=stance, origin=origin, created_at=now,
            ),
        }
        profile.events = [
            *profile.events,
            AssessmentEvent(
                event_type="recommendation_feedback",
                payload={"recommendation_id": recommendation_id, "stance": stance, "origin": origin},
            ),
        ]
        profile.updated_at = now
        self.profiles.save(profile)
        logger.info(
            "recommendation_feedback profile_id=%s recommendation_id=%s stance=%s origin=%s",
            profile.profile_id, recommendation_id, stance, origin,
        )
        return profile


def _open_questions(pack: DomainPack, profile: Profile) -> list[QuestionDefinition]:
    """Required questions that are visible but unanswered, in pack order."""
    return [
        question
        for question in pack.questions
        if question.required
        and question.field_key not in profile.answers
        and is_question_visible(pack, profile, question)
    ]


def _remove_invisible_answers(pack: DomainPack, profile: Profile) -> None:
    questions = {question.field_key: question for question in pack.questions}
    changed = True
    while changed:
        changed = False
        for field_key in list(profile.answers):
            question = questions.get(field_key)
            if question is not None and not is_question_visible(pack, profile, question):
                del profile.answers[field_key]
                changed = True


def _by_label(question: QuestionDefinition, value: Any) -> Any:
    """Accept an option label in place of its value, as users write it."""
    if question.field_type == "select" and isinstance(value, str):
        for option in question.options:
            if value.strip().lower() in (option.value.lower(), option.label.lower()):
                return option.value
    if question.field_type == "multiselect" and isinstance(value, list):
        labels = {option.label.lower(): option.value for option in question.options}
        return [labels.get(str(item).strip().lower(), item) for item in value]
    return value


def _raw(value: Any) -> str:
    if value is None:
        return "unbekannt"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
