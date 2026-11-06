from __future__ import annotations

import argparse
import os

import psycopg


def purge_pilot_data(connection, *, database_url: str | None = None, tools=None) -> None:
    """Delete all pilot data: datasets on the tool servers (best effort, when a
    tool service is given), then advisory conversations, profiles and sessions
    (pilot feedback cascades from sessions). Server registrations
    (mcp_servers) are configuration and stay."""
    if tools is not None and database_url:
        from guideos.assessment.postgres_repository import PostgresAssessmentRepository
        from guideos.profiles.postgres_repository import PostgresProfileRepository
        from guideos.profiles.service import delete_datasets_on_server, delete_session_datasets_on_server

        profiles = PostgresProfileRepository(database_url).list()
        delete_datasets_on_server(profiles, tools)
        delete_session_datasets_on_server(
            PostgresAssessmentRepository(database_url).list(), tools,
            skip_ids={p.dataset.dataset_id for p in profiles if p.dataset is not None},
        )
    connection.execute("DELETE FROM conversations")
    connection.execute("DELETE FROM profiles")
    connection.execute("DELETE FROM assessment_sessions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all GuideOS pilot data.")
    parser.add_argument("--database-url", default=os.environ.get("GUIDEOS_DATABASE_URL"))
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or GUIDEOS_DATABASE_URL is required")
    if not args.confirm:
        parser.error("--confirm is required to delete pilot data")
    from guideos.tools.factory import tool_service_from_env

    with psycopg.connect(args.database_url) as connection:
        purge_pilot_data(connection, database_url=args.database_url, tools=tool_service_from_env())


if __name__ == "__main__":
    main()
