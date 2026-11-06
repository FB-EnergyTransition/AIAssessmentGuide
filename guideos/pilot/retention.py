"""Aufbewahrungsfrist für Pilotdaten: einstellbar über GUIDEOS_RETENTION_DAYS,
standardmäßig unbegrenzt. Mit einer Frist entfernt der Lauf Konversationen,
Profile und Assessment-Sessions, deren letzte Änderung älter ist. Ein Profil
bleibt, solange eine seiner Konversationen jünger ist, und die Quell-Session
eines bleibenden Profils bleibt ebenfalls, weil die Oberfläche das Profil an
ihr zeigt. Pilot-Feedback hängt an den Sessions und wird mit ihnen entfernt."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from guideos.assessment.models import AssessmentSession
from guideos.assessment.repository import AssessmentRepository
from guideos.conversations.models import Conversation
from guideos.conversations.repository import ConversationRepository
from guideos.profiles.models import Profile
from guideos.profiles.repository import ProfileRepository
from guideos.domains.catalog import load_domain_pack_version
from guideos.profiles.service import delete_datasets_on_server, delete_session_datasets_on_server

RETENTION_ENV = "GUIDEOS_RETENTION_DAYS"
UNLIMITED = {"", "unbegrenzt", "unlimited"}

logger = logging.getLogger(__name__)


class RetentionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int | None
    cutoff: datetime | None = None
    conversation_ids: list[str] = Field(default_factory=list)
    profile_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    # The expired objects themselves, for the deletion with participant scope.
    _expired: tuple = PrivateAttr(default=([], [], []))

    @property
    def is_empty(self) -> bool:
        return not (self.conversation_ids or self.profile_ids or self.session_ids)


def retention_days_from_env(value: str | None = None) -> int | None:
    """Days to keep data, None for unlimited (the default). Reads the
    environment when no value is given."""
    raw = os.environ.get(RETENTION_ENV) if value is None else value
    text = (raw or "").strip().lower()
    if text in UNLIMITED:
        return None
    if not text.isdigit() or int(text) < 1:
        raise ValueError(
            f"{RETENTION_ENV} muss eine ganze Zahl ab 1 (Tage) sein oder leer bleiben für unbegrenzt."
        )
    return int(text)


def plan_retention(
    sessions: list[AssessmentSession],
    profiles: list[Profile],
    conversations: list[Conversation],
    cutoff: datetime,
) -> RetentionPlan:
    expired_conversations = [c for c in conversations if c.updated_at < cutoff]
    kept_profile_ids = {c.profile_id for c in conversations if c.updated_at >= cutoff}
    expired_profiles = [
        p for p in profiles if p.updated_at < cutoff and p.profile_id not in kept_profile_ids
    ]
    expired_profile_ids = {p.profile_id for p in expired_profiles}
    kept_sources = {p.source_session_id for p in profiles if p.profile_id not in expired_profile_ids}
    expired_sessions = [
        s for s in sessions if s.updated_at < cutoff and s.session_id not in kept_sources
    ]
    plan = RetentionPlan(days=None, cutoff=cutoff)
    plan.conversation_ids = [c.conversation_id for c in expired_conversations]
    plan.profile_ids = [p.profile_id for p in expired_profiles]
    plan.session_ids = [s.session_id for s in expired_sessions]
    plan._expired = (expired_conversations, expired_profiles, expired_sessions)
    return plan


def apply_retention(
    assessments: AssessmentRepository,
    profiles: ProfileRepository,
    conversations: ConversationRepository,
    days: int | None,
    *,
    now: datetime | None = None,
    apply: bool = False,
    tools=None,
    load_pack=load_domain_pack_version,
) -> RetentionPlan:
    """Plan (and with apply=True delete) what the retention removes. None
    days means unlimited retention: nothing is planned or deleted. With a
    tool service, the datasets of expired profiles are deleted on their
    tool servers first (best effort, failures are logged)."""
    if days is None:
        return RetentionPlan(days=None)
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    plan = plan_retention(assessments.list(), profiles.list(), conversations.list(), cutoff)
    plan.days = days
    if not apply:
        return plan
    expired_conversations, expired_profiles, expired_sessions = plan._expired
    for conversation in expired_conversations:
        conversations.delete(conversation.conversation_id, conversation.participant_id)
    if tools is not None:
        delete_datasets_on_server(expired_profiles, tools, load_pack)
        # Datasets of expired sessions' file answers, except those a kept
        # profile still uses for the advisory.
        kept_dataset_ids = {
            p.dataset.dataset_id for p in profiles.list()
            if p.dataset is not None and p.profile_id not in plan.profile_ids
        }
        delete_session_datasets_on_server(expired_sessions, tools, load_pack, skip_ids=kept_dataset_ids)
    for profile in expired_profiles:
        profiles.delete(profile.domain_id, profile.participant_id)
    for session in expired_sessions:
        assessments.delete(session.session_id, session.participant_id)
    logger.info(
        "retention_applied days=%d conversations=%d profiles=%d sessions=%d",
        days, len(expired_conversations), len(expired_profiles), len(expired_sessions),
    )
    return plan


def describe(plan: RetentionPlan, apply: bool) -> str:
    if plan.days is None:
        return "Aufbewahrungsfrist: unbegrenzt, nichts zu entfernen."
    verb = "entfernt" if apply else "Probelauf, würde entfernen"
    return (
        f"Aufbewahrungsfrist {plan.days} Tage (Stichtag {plan.cutoff:%d.%m.%Y}): {verb} "
        f"{len(plan.session_ids)} Session(s), {len(plan.profile_ids)} Profil(e), "
        f"{len(plan.conversation_ids)} Konversation(en)."
    )


def main(argv: list[str] | None = None) -> int:
    from guideos.assessment.factory import assessment_repository_from_env, assessment_storage_path_from_env
    from guideos.conversations.factory import (
        conversation_repository_from_env,
        conversation_storage_path_from_env,
    )
    from guideos.profiles.factory import profile_repository_from_env

    parser = argparse.ArgumentParser(
        description="Entfernt Pilotdaten, die älter als die Aufbewahrungsfrist sind (Standard: unbegrenzt)."
    )
    parser.add_argument("--days", help=f"Frist in Tagen; Standard aus {RETENTION_ENV}, leer = unbegrenzt")
    parser.add_argument("--apply", action="store_true", help="wirklich löschen statt Probelauf")
    args = parser.parse_args(argv)
    try:
        days = retention_days_from_env(args.days)
    except ValueError as exc:
        parser.error(str(exc))
    from guideos.tools.factory import tool_service_from_env

    plan = apply_retention(
        assessment_repository_from_env(assessment_storage_path_from_env()),
        profile_repository_from_env(),
        conversation_repository_from_env(conversation_storage_path_from_env()),
        days,
        apply=args.apply,
        tools=tool_service_from_env(),
    )
    sys.stdout.write(describe(plan, args.apply) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
