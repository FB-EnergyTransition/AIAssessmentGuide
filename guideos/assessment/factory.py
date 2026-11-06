import os
from pathlib import Path

from guideos.assessment.context_chat import LLMAssessmentChatAssistant
from guideos.assessment.interpreter import LLMAnswerInterpreter
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import (
    AssessmentRepository,
    DEFAULT_ASSESSMENT_STORAGE_PATH,
    LocalAssessmentRepository,
)
from guideos.domains.models import DomainPack
from guideos.knowledge.service import KnowledgeService
from guideos.profiles.repository import ProfileRepository
from guideos.providers.base import LLMProvider
from guideos.recommendations.service import knowledge_recommender
from guideos.tools.datasets import DatasetImporter
from guideos.tools.service import ToolService


def assessment_storage_path_from_env() -> Path:
    return Path(
        os.environ.get(
            "GUIDEOS_ASSESSMENT_PATH",
            str(DEFAULT_ASSESSMENT_STORAGE_PATH),
        )
    )


def build_assessment(
    pack: DomainPack,
    provider: LLMProvider,
    path: Path = DEFAULT_ASSESSMENT_STORAGE_PATH,
    repository: AssessmentRepository | None = None,
    knowledge: KnowledgeService | None = None,
    profiles: ProfileRepository | None = None,
    tools: ToolService | None = None,
) -> AssessmentOrchestrator:
    return AssessmentOrchestrator(
        pack,
        repository or assessment_repository_from_env(path),
        interpreter=LLMAnswerInterpreter(provider),
        chat_assistant=LLMAssessmentChatAssistant(provider),
        knowledge_recommender=knowledge_recommender(provider, knowledge),
        profiles=profiles,
        dataset_importer=DatasetImporter(tools) if tools is not None else None,
    )


def assessment_repository_from_env(
    path: Path = DEFAULT_ASSESSMENT_STORAGE_PATH,
) -> AssessmentRepository:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.assessment.postgres_repository import PostgresAssessmentRepository

        return PostgresAssessmentRepository(database_url)
    return LocalAssessmentRepository(path)
