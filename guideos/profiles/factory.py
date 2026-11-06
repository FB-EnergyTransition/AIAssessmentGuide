import os
from pathlib import Path

from guideos.profiles.repository import (
    DEFAULT_PROFILE_STORAGE_PATH,
    LocalProfileRepository,
    ProfileRepository,
)


def profile_storage_path_from_env() -> Path:
    return Path(os.environ.get("GUIDEOS_PROFILE_PATH", str(DEFAULT_PROFILE_STORAGE_PATH)))


def profile_repository_from_env(path: Path | None = None) -> ProfileRepository:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url:
        from guideos.profiles.postgres_repository import PostgresProfileRepository

        return PostgresProfileRepository(database_url)
    return LocalProfileRepository(path or profile_storage_path_from_env())
