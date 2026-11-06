from pathlib import Path

import httpx
import pytest

from testsuite.lifecycle import managed_api
from testsuite.cli import main


@pytest.fixture(autouse=True)
def local_cli_storage(monkeypatch):
    # CI configures a PostgreSQL sink for integration tests, while these cases
    # deliberately exercise the local CLI repository and its exported files.
    monkeypatch.delenv("GUIDEOS_TESTSUITE_DATABASE_URL", raising=False)


def test_managed_api_ignores_pilot_environment_and_cleans_up(monkeypatch, tmp_path):
    pilot_path = tmp_path / "pilot.json"
    pilot_path.write_text("do not touch")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://invalid/pilot")
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(pilot_path))
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    with managed_api() as target:
        workdir = Path(target["working_directory"])
        with httpx.Client(base_url=target["base_url"], trust_env=False) as client:
            response = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"})
        assert response.json()["ok"] is True
        assert workdir.exists()
        assert target["manifest"]["source"] == "managed"
        assert target["manifest"]["domain_packs"][0]["content_hash"]
    assert not workdir.exists()
    assert pilot_path.read_text() == "do not touch"


def test_cli_validation_does_not_start_an_api(tmp_path, capsys):
    assert main(["--scenario-dir", str(tmp_path), "--results-dir", str(tmp_path / "results")]) == 2
    assert "error" in capsys.readouterr().out


def test_cleanup_preview_and_confirm(tmp_path, capsys):
    from testsuite.repository import JsonlRepository
    repo = JsonlRepository(tmp_path)
    repo.write("runs", {"id": "old", "run_id": "old", "status": "passed", "finished_at": "2000-01-01T00:00:00+00:00"})
    args = ["--results-dir", str(tmp_path), "--cleanup", "--run-id", "old"]
    assert main(args) == 0
    assert repo.read("old")["runs"]
    assert main(args + ["--confirm"]) == 0
    assert repo.runs() == []


def test_cli_failure_rerun_and_reports_use_saved_snapshots(tmp_path, capsys):
    import json
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    source = scenario_dir / "failure.json"
    source.write_text(json.dumps({"schema_version": 1, "id": "failure", "version": "1", "domain_id": "verein-demo",
        "domain_version": "1.0.0", "persist_raw": True, "steps": [
            {"id": "start", "action": "start_assessment", "assertions": [
                {"path": "/status", "op": "eq", "expected": "completed"}]}]}))
    results = tmp_path / "results"
    assert main(["--scenario-dir", str(scenario_dir), "--results-dir", str(results)]) == 1
    first = json.loads(capsys.readouterr().out)
    source.unlink()
    assert main(["--rerun", first["run_id"], "--results-dir", str(results), "--report", "html"]) == 1
    second = json.loads(capsys.readouterr().out)
    assert second["run_id"] != first["run_id"]
    assert Path(second["report"]).exists()
    from testsuite.repository import JsonlRepository
    repo = JsonlRepository(results)
    data = repo.read(second["run_id"])
    assert data["runs"][0]["parent_run_id"] == first["run_id"]
    assert data["cases"][0]["session_id"] != repo.read(first["run_id"])["cases"][0]["session_id"]
    assert main(["--run-id", first["run_id"], "--results-dir", str(results), "--report", "html"]) == 0


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1"])
def test_cli_rejects_nonfinite_or_nonpositive_timeouts(timeout, tmp_path, capsys):
    assert main(["--read-timeout", timeout, "--results-dir", str(tmp_path)]) == 2
    assert "Timeout" in capsys.readouterr().out


def test_cli_repeat_reports_pass_rates(tmp_path, capsys):
    import json
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "basic.json").write_text(json.dumps({
        "schema_version": 1, "id": "basic", "version": "1", "domain_id": "verein-demo",
        "domain_version": "1.0.0", "steps": [{"id": "start", "action": "start_assessment"}]}))
    results = tmp_path / "results"

    assert main(["--scenario-dir", str(scenario_dir), "--results-dir", str(results), "--repeat", "2"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["counts"]["passed"] == 2
    assert summary["pass_rates"] == {"basic": {"passed": 2, "total": 2}}

    assert main(["--scenario-dir", str(scenario_dir), "--results-dir", str(results), "--repeat", "0"]) == 2


def test_seed_knowledge_indexes_documents_per_domain(tmp_path):
    from guideos.knowledge.store import LocalKnowledgeStore
    from testsuite.lifecycle import seed_knowledge

    source = tmp_path / "wissen"
    (source / "verein-demo").mkdir(parents=True)
    (source / "verein-demo" / "leitfaden.md").write_text(
        "# Heizen\n\nEin hydraulischer Abgleich senkt den Verbrauch.", encoding="utf-8"
    )
    (source / "verein-demo" / ".gitkeep").write_text("", encoding="utf-8")
    path = tmp_path / "knowledge.json"

    seeded = seed_knowledge(path, source)

    [document] = LocalKnowledgeStore(path).list("verein-demo")
    assert document.kind == "document" and document.filename == "leitfaden.md"
    assert seeded == {
        "verein-demo": [{"title": document.title, "filename": "leitfaden.md",
                         "content_hash": document.content_hash}]
    }
    assert LocalKnowledgeStore(path).load_original(document.content_hash) is not None


def test_seed_knowledge_rejects_files_outside_a_domain_directory(tmp_path):
    from testsuite.lifecycle import seed_knowledge

    source = tmp_path / "wissen"
    source.mkdir()
    (source / "leitfaden.md").write_text("Text", encoding="utf-8")

    with pytest.raises(ValueError, match="Domain-Verzeichnis"):
        seed_knowledge(tmp_path / "knowledge.json", source)
    with pytest.raises(ValueError, match="nicht gefunden"):
        seed_knowledge(tmp_path / "knowledge.json", tmp_path / "fehlt")


def test_managed_api_seeds_the_knowledge_base_from_a_directory(tmp_path):
    source = tmp_path / "wissen"
    (source / "verein-demo").mkdir(parents=True)
    (source / "verein-demo" / "leitfaden.md").write_text("# Heizen\n\nText.", encoding="utf-8")

    with managed_api(knowledge_dir=source) as target:
        knowledge = target["manifest"]["configuration"]["knowledge"]

    assert [item["filename"] for item in knowledge["verein-demo"]] == ["leitfaden.md"]


def test_cli_passes_the_knowledge_directory_to_the_managed_api(tmp_path, capsys, monkeypatch):
    import testsuite.cli as cli

    seen = {}

    def fake_managed_api(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(cli, "managed_api", fake_managed_api)

    code = main(["--knowledge-dir", str(tmp_path), "--results-dir", str(tmp_path / "r")])

    assert code == 2 and "stop" in capsys.readouterr().out
    assert seen["knowledge_dir"] == tmp_path


def test_tool_server_configs_come_from_one_file_per_domain(tmp_path):
    import json
    from testsuite.lifecycle import tool_server_configs

    (tmp_path / "demo-domain.json").write_text(json.dumps({
        "module": "demo_server", "token_env": "DEMO_TOKEN",
        "environment": {"DEMO_STORE": "memory"}, "in_process": "tests.demo:start_server"}), encoding="utf-8")

    assert tool_server_configs([], tmp_path) == {}
    configs = tool_server_configs(["demo-domain"], tmp_path)
    assert configs["demo-domain"]["module"] == "demo_server"
    assert configs["demo-domain"]["token_env"] == "DEMO_TOKEN"
    assert configs["demo-domain"]["environment"] == {"DEMO_STORE": "memory"}
    assert configs["demo-domain"]["in_process"] == "tests.demo:start_server"
    with pytest.raises(RuntimeError, match="other-domain"):
        tool_server_configs(["demo-domain", "other-domain"], tmp_path)
    (tmp_path / "broken.json").write_text(json.dumps({"module": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="token_env"):
        tool_server_configs(["broken"], tmp_path)


def test_external_tool_servers_from_environment_apply_per_domain_or_to_all():
    from testsuite.lifecycle import external_tool_servers

    assert external_tool_servers({}, ["a-domain", "b-domain"]) is None
    shared = {"GUIDEOS_TESTSUITE_MCP_URL": "http://shared/mcp", "GUIDEOS_TESTSUITE_MCP_TOKEN": "t"}
    assert external_tool_servers(shared, ["a-domain", "b-domain"]) == {
        "a-domain": {"url": "http://shared/mcp", "token": "t"},
        "b-domain": {"url": "http://shared/mcp", "token": "t"}}
    specific = {"GUIDEOS_TESTSUITE_MCP_URL__A_DOMAIN": "http://a/mcp", "GUIDEOS_TESTSUITE_MCP_TOKEN__A_DOMAIN": "ta"}
    assert external_tool_servers(specific, ["a-domain", "b-domain"]) == {"a-domain": {"url": "http://a/mcp", "token": "ta"}}
    assert external_tool_servers({**shared, **specific}, ["a-domain", "b-domain"])["b-domain"]["url"] == "http://shared/mcp"
    assert external_tool_servers({**shared, **specific}, ["a-domain", "b-domain"])["a-domain"]["url"] == "http://a/mcp"
