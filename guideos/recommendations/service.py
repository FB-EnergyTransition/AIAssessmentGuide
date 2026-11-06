from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from guideos.assessment.models import (
    AssessmentEvent,
    AssessmentSession,
    ExcludedRecommendation,
    KnowledgeGeneration,
    LLMRecommendation,
)
from guideos.assessment.repository import AssessmentRepository
from guideos.context import AdvisoryContext, build_advisory_context
from guideos.domains.catalog import DomainCatalogError, load_domain_pack_version
from guideos.domains.loader import DomainPackError
from guideos.domains.models import DomainPack, display_value
from guideos.knowledge.models import KnowledgeError, RetrievedPassage
from guideos.knowledge.service import KnowledgeService
from guideos.profiles.models import Profile
from guideos.profiles.repository import ProfileRepository
from guideos.providers.base import LLMProvider, ProviderError
from guideos.recommendations.llm import (
    MAX_RECOMMENDATIONS,
    PROMPT_VERSION,
    LLMRecommendationError,
    LLMRecommendationGenerator,
)

logger = logging.getLogger(__name__)

RecommendationServiceErrorCode = Literal[
    "invalid_state",
    "profile_not_found",
    "domain_version_unavailable",
    "provider_error",
    "knowledge_unavailable",
]


class RecommendationServiceError(Exception):
    def __init__(self, message: str, code: RecommendationServiceErrorCode):
        super().__init__(message)
        self.code = code


class KnowledgeRecommender(Protocol):
    """Produces the knowledge recommendations of a session for the
    orchestrator: the checked items and how they were generated. Raises
    RecommendationServiceError with a stable code."""

    def __call__(
        self,
        pack: DomainPack,
        session: AssessmentSession,
        endpoint: str,
        model: str,
    ) -> tuple[list[LLMRecommendation], KnowledgeGeneration, list[ExcludedRecommendation]]: ...


def knowledge_recommender(
    provider: LLMProvider,
    knowledge: KnowledgeService | None = None,
) -> KnowledgeRecommender:
    def recommend(
        pack: DomainPack,
        session: AssessmentSession,
        endpoint: str,
        model: str,
    ) -> tuple[list[LLMRecommendation], KnowledgeGeneration, list[ExcludedRecommendation]]:
        return generate_for_session(pack, session, provider, endpoint, model, knowledge)

    return recommend


def generate_for_session(
    pack: DomainPack,
    session: AssessmentSession,
    provider: LLMProvider,
    endpoint: str,
    model: str,
    knowledge: KnowledgeService | None = None,
) -> tuple[list[LLMRecommendation], KnowledgeGeneration, list[ExcludedRecommendation]]:
    """Knowledge recommendations for a session whose rule result is already
    computed, grounded in the domain's knowledge base when one is given. The
    session is not saved here. Raises RecommendationServiceError with a
    stable code."""
    return generate_for_context(
        pack, build_advisory_context(pack, session), session.session_id,
        provider, endpoint, model, knowledge,
    )


def generate_for_context(
    pack: DomainPack,
    context: AdvisoryContext,
    log_id: str,
    provider: LLMProvider,
    endpoint: str,
    model: str,
    knowledge: KnowledgeService | None = None,
) -> tuple[list[LLMRecommendation], KnowledgeGeneration, list[ExcludedRecommendation]]:
    """Checked knowledge recommendations for a context, the generation
    metadata, and the items dropped because an exclude rule of the pack
    matched their topic. Prioritize rules order the kept items."""
    settings = pack.knowledge_recommendations
    max_count = settings.max_count if settings is not None else MAX_RECOMMENDATIONS
    passages: list[RetrievedPassage] = []
    retrieval_mode = None
    try:
        if knowledge is not None:
            passages, retrieval_mode = knowledge.search_with_mode(
                pack, _knowledge_query(pack, context), endpoint
            )
        recommendations = LLMRecommendationGenerator(provider).generate(
            endpoint, model, context, passages, max_count=max_count
        )
    except KnowledgeError as exc:
        raise RecommendationServiceError(str(exc), code="knowledge_unavailable") from exc
    except (ProviderError, LLMRecommendationError) as exc:
        raise RecommendationServiceError(str(exc), code="provider_error") from exc
    generation = KnowledgeGeneration(
        model=model,
        prompt_version=PROMPT_VERSION,
        generated_at=datetime.now(UTC),
        passages=passages,
        retrieval_mode=retrieval_mode,
        embedding_model=knowledge.embedding_model if knowledge is not None else None,
        rerank_model=knowledge.rerank_model if knowledge is not None else None,
        tool_calculation_ids=[r.calculation_id for r in context.tool_results if r.calculation_id],
    )
    recommendations, excluded = _apply_topic_rules(context, recommendations)
    logger.info(
        "llm_recommendations_generated session_id=%s count=%d excluded_by_rule=%d",
        log_id,
        len(recommendations),
        len(excluded),
    )
    return recommendations, generation, excluded


def _apply_topic_rules(
    context: AdvisoryContext, generated: list[LLMRecommendation]
) -> tuple[list[LLMRecommendation], list[ExcludedRecommendation]]:
    exclusions = dict.fromkeys(context.excluded_topics)
    priorities = {item.topic_id: item.priority for item in context.topic_priorities}
    kept: list[LLMRecommendation] = []
    excluded: list[ExcludedRecommendation] = []
    for item in generated:
        if item.topic is not None and item.topic in exclusions:
            excluded.append(
                ExcludedRecommendation(
                    recommendation_id=item.recommendation_id,
                    title=item.title,
                    trigger="rule",
                    rule_id=context.excluding_rules.get(item.topic),
                    topic=item.topic,
                )
            )
            continue
        item.priority = priorities.get(item.topic) if item.topic is not None else None
        kept.append(item)
    kept.sort(key=lambda item: (item.priority is None, item.priority or 0))
    return kept, excluded


def generate_profile_recommendations(
    profiles: ProfileRepository,
    domain_id: str,
    participant_id: str | None,
    provider: LLMProvider,
    endpoint: str,
    model: str,
    load_pack: Callable[[str, str], DomainPack] = load_domain_pack_version,
    knowledge: KnowledgeService | None = None,
) -> list[LLMRecommendation]:
    """Regenerate the knowledge recommendations of the participant's profile
    of a domain on request and persist them on the profile, replacing the
    earlier set. Raises RecommendationServiceError with a stable code; a
    failed attempt leaves the profile unchanged."""
    try:
        profile = profiles.get(domain_id, participant_id)
    except LookupError as exc:
        raise RecommendationServiceError(str(exc), code="profile_not_found") from exc
    try:
        pack = load_pack(profile.domain_id, profile.domain_version)
    except (DomainCatalogError, DomainPackError) as exc:
        raise RecommendationServiceError(
            "Die Domain-Version des Profils ist nicht mehr verfügbar.",
            code="domain_version_unavailable",
        ) from exc
    if pack.content_hash != profile.domain_content_hash:
        raise RecommendationServiceError(
            "Die Domain-Version des Profils ist nicht mehr verfügbar.",
            code="domain_version_unavailable",
        )
    generated, generation, excluded_by_rule = generate_for_context(
        pack, build_advisory_context(pack, profile), profile.profile_id,
        provider, endpoint, model, knowledge,
    )
    recommendations, excluded = _apply_feedback(profile, generated)
    profile.result.knowledge_recommendations = recommendations
    profile.result.knowledge_generation = generation
    profile.result.excluded = [*excluded_by_rule, *excluded]
    profile.knowledge_stale = False
    profile.events = [
        *profile.events,
        AssessmentEvent(
            event_type="knowledge_recommendations_generated",
            payload={"count": len(recommendations)},
        ),
    ]
    profile.updated_at = datetime.now(UTC)
    profiles.save(profile)
    logger.info(
        "knowledge_recommendations_persisted profile_id=%s count=%d",
        profile.profile_id,
        len(recommendations),
    )
    return recommendations


def generate_llm_recommendations(
    assessments: AssessmentRepository,
    session_id: str,
    provider: LLMProvider,
    endpoint: str,
    model: str,
    load_pack: Callable[[str, str], DomainPack] = load_domain_pack_version,
    knowledge: KnowledgeService | None = None,
) -> list[LLMRecommendation]:
    """Generate the knowledge recommendations of a completed session on
    request and persist them on its result, replacing an earlier set.
    Raises LookupError for unknown sessions and RecommendationServiceError
    with a stable code otherwise; a failed attempt leaves the session
    unchanged."""
    session = assessments.get(session_id)
    if session.status != "completed" or session.result is None:
        raise RecommendationServiceError(
            "KI-Empfehlungen sind erst nach Abschluss des Assessments verfügbar.",
            code="invalid_state",
        )
    try:
        pack = load_pack(session.domain_id, session.domain_version)
    except (DomainCatalogError, DomainPackError) as exc:
        raise RecommendationServiceError(
            "Die Domain-Version der Session ist nicht mehr verfügbar.",
            code="domain_version_unavailable",
        ) from exc
    if pack.content_hash != session.domain_content_hash:
        raise RecommendationServiceError(
            "Die Domain-Version der Session ist nicht mehr verfügbar.",
            code="domain_version_unavailable",
        )
    recommendations, generation, excluded = generate_for_session(
        pack, session, provider, endpoint, model, knowledge
    )
    session.result.knowledge_recommendations = recommendations
    session.result.knowledge_generation = generation
    session.result.excluded = excluded
    session.updated_at = datetime.now(UTC)
    assessments.save(session)
    logger.info(
        "knowledge_recommendations_persisted session_id=%s count=%d",
        session.session_id,
        len(recommendations),
    )
    return recommendations


def _apply_feedback(
    profile: Profile, generated: list[LLMRecommendation]
) -> tuple[list[LLMRecommendation], list[ExcludedRecommendation]]:
    """Accepted knowledge recommendations are kept unchanged and listed
    first; generated items that repeat a rejected one (same id or title)
    are dropped and recorded as excluded."""
    rejected_ids = {
        item.recommendation_id for item in profile.feedback.values() if item.stance == "rejected"
    }
    rejected_titles = {
        _title_key(item.title) for item in profile.feedback.values() if item.stance == "rejected"
    }
    accepted = [
        item
        for item in profile.result.knowledge_recommendations
        if profile.feedback.get(item.recommendation_id, None) is not None
        and profile.feedback[item.recommendation_id].stance == "accepted"
    ]
    kept_ids = {item.recommendation_id for item in accepted}
    kept_titles = {_title_key(item.title) for item in accepted}
    recommendations = list(accepted)
    excluded: list[ExcludedRecommendation] = []
    for item in generated:
        if item.recommendation_id in rejected_ids or _title_key(item.title) in rejected_titles:
            excluded.append(
                ExcludedRecommendation(
                    recommendation_id=item.recommendation_id, title=item.title, trigger="user_feedback"
                )
            )
            continue
        if item.recommendation_id in kept_ids or _title_key(item.title) in kept_titles:
            continue
        recommendations.append(item)
    return recommendations, excluded


def _title_key(title: str) -> str:
    return " ".join(title.lower().split())


def _knowledge_query(pack: DomainPack, context: AdvisoryContext) -> str:
    """Search text for a session without a user question: what the rules
    recommend and what the user stated, in the wording shown to the user."""
    questions = {question.field_key: question for question in pack.questions}
    parts = [pack.name]
    parts.extend(f"{item.title} {item.message}" for item in context.rule_recommendations)
    for fact in context.facts:
        question = questions.get(fact.field_key)
        if question is not None and fact.normalized_value is not None:
            parts.append(f"{question.text} {display_value(question, fact.normalized_value)}")
    return " ".join(parts)
