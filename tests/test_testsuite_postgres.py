import os
from uuid import uuid4

import pytest

from testsuite.postgres import PostgresRepository, migrate


@pytest.mark.skipif(not os.environ.get("GUIDEOS_TESTSUITE_DATABASE_URL"), reason="Testlauf-Datenbank erforderlich.")
def test_postgres_migrations_roundtrip_cleanup_and_isolation():
    url = os.environ["GUIDEOS_TESTSUITE_DATABASE_URL"]
    migrate(url)
    migrate(url)
    repo = PostgresRepository(url)
    run_id, other = uuid4().hex, uuid4().hex
    try:
        for id in (run_id, other):
            repo.write("runs", {"id": id, "run_id": id, "status": "running"})
        repo.write("cases", {"id": "case_" + run_id, "run_id": run_id, "status": "passed"})
        for kind in ("events", "assertions", "artifacts", "ratings"):
            repo.write(kind, {"id": kind + run_id, "run_id": run_id, "case_id": "case_" + run_id, "value": {"x": 1}})
        assert repo.read(run_id)["events"][0]["value"] == {"x": 1}
        assert repo.read(run_id)["ratings"][0]["id"] == "ratings" + run_id
        with pytest.raises(ValueError):
            repo.delete(run_id)
        repo.write("runs", {"id": run_id, "run_id": run_id, "status": "passed"})
        repo.delete(run_id)
        with pytest.raises(LookupError):
            repo.read(run_id)
        assert repo.read(other)["runs"][0]["status"] == "running"
    finally:
        for id in (run_id, other):
            if any(r["id"] == id for r in repo.runs()):
                repo.write("runs", {"id": id, "run_id": id, "status": "passed"})
                repo.delete(id)
