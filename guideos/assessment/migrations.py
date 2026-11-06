from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from guideos.config import DATABASE_URL


MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


def migrate_database(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS guideos_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for migration_path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
            version = migration_path.name
            existing = connection.execute(
                "SELECT 1 FROM guideos_schema_migrations WHERE version = %s",
                (version,),
            ).fetchone()
            if existing is None:
                connection.execute(migration_path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO guideos_schema_migrations (version) VALUES (%s)",
                    (version,),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the GuideOS PostgreSQL schema.")
    parser.add_argument("--database-url", default=DATABASE_URL)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or GUIDEOS_DATABASE_URL is required")
    migrate_database(args.database_url)


if __name__ == "__main__":
    main()
