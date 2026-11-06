import json

import httpx
import pytest

from testsuite.client import GuideOSClient, validate_target
from testsuite.repository import JsonlRepository
from testsuite.redaction import redact
from testsuite.reports import build_report


def test_get_retries_connection_errors_but_post_never_retries():
    attempts = []
    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("no connection", request=request)
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            client.request("GET", "/api/v1/health")
        assert len(attempts) == 3
        attempts.clear()
        with pytest.raises(httpx.ConnectError):
            client.request("POST", "/api/v1/assessment-sessions", {})
        assert len(attempts) == 1
        assert "x-request-id" in attempts[0].headers


def test_redirects_are_not_followed():
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "https://example.com"})
    )) as client:
        event = client.request("GET", "/api/v1/health")
        assert event["http_status"] == 302


def test_external_target_requires_manifest_allowlist_and_production_override():
    with pytest.raises(ValueError):
        validate_target("http://localhost:8000", None, [], False)
    manifest = {"environment": "test", "base_url": "http://api:8000"}
    with pytest.raises(ValueError):
        validate_target("http://api:8000", manifest, [], False)
    validate_target("http://api:8000", manifest, ["api"], False)
    manifest["environment"] = "production"
    with pytest.raises(ValueError):
        validate_target("http://api:8000", manifest, ["api"], False)
    validate_target("http://api:8000", manifest, ["api"], True)


def test_repository_updates_replay_and_cleanup_are_run_scoped(tmp_path):
    repo = JsonlRepository(tmp_path)
    repo.write("runs", {"id": "one", "run_id": "one", "status": "running"})
    repo.write("runs", {"id": "two", "run_id": "two", "status": "passed"})
    repo.write("cases", {"id": "case", "run_id": "one", "status": "running"})
    assert repo.read("one")["cases"][0]["status"] == "running"
    with pytest.raises(ValueError):
        repo.delete("one")
    repo.write("runs", {"id": "one", "run_id": "one", "status": "passed"})
    repo.delete("one")
    assert repo.read("two")["runs"][0]["status"] == "passed"
    with pytest.raises(ValueError):
        repo.read("../../outside")


def test_redaction_covers_nested_secrets_urls_free_text_and_known_values():
    payload = {"Authorization": "Bearer abc", "nested": {"api_key": "abc"},
               "endpoint": "https://user:password@host/path?token=abc",
               "raw_answer": "private answer", "assistant_message": "private result",
               "error": "failed with configured-secret", "ok": False}
    result = redact(payload, persist_raw=False, secrets=["configured-secret"])
    encoded = json.dumps(result)
    for secret in ["Bearer abc", "private answer", "private result", "password", "configured-secret"]:
        assert secret not in encoded
    assert result["ok"] is False
    assert redact({"raw_answer": "synthetic"}, persist_raw=True)["raw_answer"] == "synthetic"


def test_html_escapes_untrusted_text_and_reports_assertion_evidence():
    data = {"runs": [{"id": "x", "status": "failed"}],
            "cases": [{"id": "c", "scenario_id": "<script>alert(1)</script>", "status": "failed"}],
            "assertions": [{"case_id": "c", "passed": False, "path": "/ok", "expected": True, "actual": False}],
            "events": [], "artifacts": []}
    report = build_report(data, "html")
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "/ok" in report
    assert "false" in report


def test_raw_opt_out_removes_nested_free_text_from_assertion_evidence():
    data = {"path": "/report/content", "op": "eq", "actual": ["private prose"], "expected": {"value": "private prose"}}
    assert "private prose" not in json.dumps(redact(data, persist_raw=False))


def test_read_timeout_is_not_retried_even_for_get():
    attempts = []
    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("timed out", request=request)
    with GuideOSClient("http://localhost", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.request("GET", "/api/v1/health")
    assert len(attempts) == 1


def test_raw_opt_out_removes_cited_knowledge_excerpts():
    message = {"sections": [{"text": "private answer", "sources": [
        {"kind": "document", "title": "Leitfaden", "excerpt": "interner Dokumenttext"}]}]}

    encoded = json.dumps(redact(message, persist_raw=False))

    assert "interner Dokumenttext" not in encoded and "private answer" not in encoded
    assert '"kind": "document"' in encoded
