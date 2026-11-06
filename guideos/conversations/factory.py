import os
from pathlib import Path

from guideos.conversations.repository import (
    DEFAULT_CONVERSATION_STORAGE_PATH,
    ConversationRepository,
    LocalConversationRepository,
)


def conversation_storage_path_from_env() -> Path:
    return Path(
        os.environ.get(
            "GUIDEOS_CONVERSATION_PATH",
            str(DEFAULT_CONVERSATION_STORAGE_PATH),
        )
    )


def conversation_repository_from_env(
    path: Path = DEFAULT_CONVERSATION_STORAGE_PATH,
) -> ConversationRepository:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.conversations.postgres_repository import (
            PostgresConversationRepository,
        )

        return PostgresConversationRepository(database_url)
    return LocalConversationRepository(path)
