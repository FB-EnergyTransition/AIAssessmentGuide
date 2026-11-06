import logging
from urllib import error

import pytest

from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.config import configure_logging
from guideos.domains.loader import load_domain_pack
from guideos.providers.base import ProviderError
from guideos.providers.ollama import OllamaProvider


@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    package = logging.getLogger("guideos")
    previous_handlers = list(root.handlers)
    previous_level = package.level
    yield
    for handler in root.handlers:
        if handler not in previous_handlers:
            root.removeHandler(handler)
    package.setLevel(previous_level)


@pytest.fixture
def orchestrator(tmp_path):
    return AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(tmp_path / "assessments.json"),
    )


def test_configure_logging_reads_level_from_environment(monkeypatch, clean_logging):
    monkeypatch.setenv("GUIDEOS_LOG_LEVEL", "debug")

    configure_logging()

    assert logging.getLogger("guideos").level == logging.DEBUG


def test_configure_logging_defaults_to_info_and_adds_one_handler(monkeypatch, clean_logging):
    monkeypatch.delenv("GUIDEOS_LOG_LEVEL", raising=False)

    configure_logging()
    configure_logging()

    handlers = [h for h in logging.getLogger().handlers if h.name == "guideos"]
    assert len(handlers) == 1
    assert logging.getLogger("guideos").level == logging.INFO


def test_orchestrator_logs_workflow_events_without_answer_values(orchestrator, caplog):
    with caplog.at_level(logging.INFO, logger="guideos"):
        response = orchestrator.start()
        session_id = response.session_id
        orchestrator.submit(session_id, "club_type", "sports")
        orchestrator.submit(session_id, "founded_year", 1500)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "guideos.assessment.orchestrator"
    ]
    assert any(
        "session_started" in m and f"session_id={session_id}" in m and "domain_id=verein-demo" in m
        for m in messages
    )
    assert any("answer_accepted" in m and "field_key=club_type" in m for m in messages)
    assert any(
        "answer_rejected" in m and "field_key=founded_year" in m and "code=" in m
        for m in messages
    )
    assert not any("sports" in m or "1500" in m for m in messages)


def test_orchestrator_logs_completion(orchestrator, caplog):
    response = orchestrator.start()
    session_id = response.session_id
    for field_key, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        orchestrator.submit(session_id, field_key, value)

    with caplog.at_level(logging.INFO, logger="guideos"):
        orchestrator.complete(session_id)

    messages = [r.getMessage() for r in caplog.records]
    assert any("session_completed" in m and f"session_id={session_id}" in m for m in messages)


def test_ollama_provider_logs_failures_with_endpoint_and_model(monkeypatch, caplog):
    def fake_urlopen(chat_request, timeout):
        raise error.URLError("connection refused")

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    with caplog.at_level(logging.WARNING, logger="guideos"):
        with pytest.raises(ProviderError):
            OllamaProvider().generate_structured(
                "http://localhost:11434", "gpt-oss:20b", [], {"type": "object"}
            )
        with pytest.raises(ProviderError):
            OllamaProvider().list_models("http://localhost:11434")

    records = [r for r in caplog.records if r.name == "guideos.providers.ollama"]
    assert len(records) == 2
    assert all(r.levelno == logging.WARNING for r in records)
    assert "endpoint=http://localhost:11434" in records[0].getMessage()
    assert "model=gpt-oss:20b" in records[0].getMessage()
    assert "connection refused" in records[0].getMessage()


def test_ollama_provider_logs_completed_requests_at_debug(monkeypatch, caplog):
    class FakeResponse:
        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            if self.url.endswith("/api/tags"):
                return b'{"models": [{"name": "gpt-oss:20b"}]}'
            return b'{"message":{"content":"{}"}}'

        def __iter__(self):
            yield b'{"message":{"content":"Hi"},"done":false}\n'
            yield b'{"done":true}\n'

    monkeypatch.setattr(
        "guideos.providers.ollama.request.urlopen",
        lambda req, timeout: FakeResponse(req.full_url),
    )
    provider = OllamaProvider()

    with caplog.at_level(logging.DEBUG, logger="guideos"):
        provider.generate_structured("http://localhost:11434", "gpt-oss:20b", [], {})
        list(provider.stream_chat("http://localhost:11434", "gpt-oss:20b", []))
        provider.list_models("http://localhost:11434")

    records = [r for r in caplog.records if r.name == "guideos.providers.ollama"]
    assert [r.levelno for r in records] == [logging.DEBUG] * 3
    texts = [r.getMessage() for r in records]
    assert all("ollama_request_completed" in t and "duration_ms=" in t for t in texts)
    assert "kind=structured endpoint=http://localhost:11434 model=gpt-oss:20b" in texts[0]
    assert "kind=stream endpoint=http://localhost:11434 model=gpt-oss:20b" in texts[1]
    assert "kind=models endpoint=http://localhost:11434 model=None" in texts[2]
