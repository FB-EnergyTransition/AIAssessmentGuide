from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from testsuite.repository import KINDS, TERMINAL, validate_id


def migrate(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(719082601)")
        connection.execute("CREATE SCHEMA IF NOT EXISTS testsuite")
        connection.execute("CREATE TABLE IF NOT EXISTS testsuite.schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            if not connection.execute("SELECT 1 FROM testsuite.schema_migrations WHERE version = %s", (path.name,)).fetchone():
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO testsuite.schema_migrations (version) VALUES (%s)", (path.name,))


class PostgresRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def write(self, kind: str, record: dict) -> None:
        if kind not in KINDS:
            raise ValueError("Unbekannter Ergebnistyp.")
        validate_id(record["run_id"])
        fields = ["id", "run_id", "data"]
        values = [record["id"], record["run_id"], Jsonb(record)]
        if kind in {"events", "assertions", "artifacts", "ratings"}:
            fields.append("case_id")
            values.append(record.get("case_id"))
        table = sql.Identifier("testsuite", f"test_{kind}")
        query = sql.SQL("INSERT INTO {} AS target ({}) VALUES ({}) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data WHERE target.run_id = EXCLUDED.run_id").format(
            table, sql.SQL(", ").join(map(sql.Identifier, fields)), sql.SQL(", ").join(sql.Placeholder() for _ in fields))
        with psycopg.connect(self.database_url) as connection:
            if connection.execute(query, values).rowcount != 1:
                raise ValueError("Ergebnis-ID gehört zu einem anderen Lauf.")

    def read(self, run_id: str) -> dict[str, list[dict]]:
        validate_id(run_id)
        with psycopg.connect(self.database_url) as connection:
            result = {kind: [row[0] for row in connection.execute(
                sql.SQL("SELECT data FROM {} WHERE run_id = %s ORDER BY data->>'started_at', data->>'created_at', id").format(
                    sql.Identifier("testsuite", f"test_{kind}")), (run_id,))] for kind in KINDS}
        if not result["runs"]:
            raise LookupError("Testlauf nicht gefunden.")
        # Scenario order must survive persistence and reruns.
        result["cases"].sort(key=lambda case: case.get("sequence_no", 0))
        result["events"].sort(key=lambda event: (event.get("case_id", ""), event.get("sequence_no", 0)))
        return result

    def runs(self) -> list[dict]:
        with psycopg.connect(self.database_url) as connection:
            return [row[0] for row in connection.execute("SELECT data FROM testsuite.test_runs ORDER BY data->>'started_at', id")]

    def delete(self, run_id: str) -> None:
        validate_id(run_id)
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute("SELECT data FROM testsuite.test_runs WHERE id = %s FOR UPDATE", (run_id,)).fetchone()
            if row is None:
                raise LookupError("Testlauf nicht gefunden.")
            if row[0]["status"] not in TERMINAL:
                raise ValueError("Aktive Läufe dürfen nicht gelöscht werden.")
            connection.execute("DELETE FROM testsuite.test_runs WHERE id = %s", (run_id,))
