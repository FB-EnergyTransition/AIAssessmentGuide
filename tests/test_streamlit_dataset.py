"""Streamlit result area: the measurement-data panel of a pack with tools,
with a completed assessment prepared in the app's local stores."""

from datetime import UTC, datetime

from streamlit.testing.v1 import AppTest

from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.domains.catalog import load_active_domain_pack
from guideos.profiles.repository import LocalProfileRepository
from guideos.tools.models import DatasetRef, ToolServer
from guideos.tools.store import LocalToolServerStore
from tests.tool_fakes import DELETE_SPEC, IMPORT_SPEC, ANALYZE_SPEC
from tests.pack_fixtures import TOOLS_DOMAIN, TOOLS_PACK_ANSWERS, install_tools_pack


def _prepare(tmp_path, monkeypatch, with_dataset: bool):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setenv("GUIDEOS_TOOL_SERVERS_PATH", str(tmp_path / "mcp_servers.json"))
    install_tools_pack(tmp_path, monkeypatch)
    pack = load_active_domain_pack(TOOLS_DOMAIN)
    assert pack.tools is not None and pack.tools.enabled
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles)
    response = orchestrator.start()
    # The pack asks for the file first; without one the questions follow as before.
    for question_id, value in TOOLS_PACK_ANSWERS:
        response = orchestrator.submit(response.session_id, question_id, value)
    orchestrator.complete(response.session_id)
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = ToolServer.create(pack.domain_id, "demo", "http://127.0.0.1:1/mcp", "token-0123456789abcdef")
    store.save(server.model_copy(update={"tool_specs": [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC],
                                         "allowed_tools": ["analyze_dataset", "import_dataset",
                                                           "delete_dataset"]}))
    if with_dataset:
        profile = profiles.get(pack.domain_id)
        profile.dataset = DatasetRef(dataset_id="ds_1", content_hash="c" * 64, filename="export.csv",
                                     imported_at=datetime(2026, 10, 19, 12, tzinfo=UTC),
                                     server_name="demo", tool_name="import_dataset",
                                     summary="2976 Werte, 1 vollständiger Monat.")
        profiles.save(profile)
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Assessment").run()
    domain = next(item for item in app.sidebar.selectbox if item.label == "Assessment-Domain")
    domain.set_value(next(option for option in domain.options if "Kassabuch" in option)).run()
    return app


def test_dataset_panel_offers_upload_with_the_tools_formats(tmp_path, monkeypatch):
    app = _prepare(tmp_path, monkeypatch, with_dataset=False)
    assert not app.exception, [e.value for e in app.exception]
    assert any(item.label == "Messdaten (CSV)" for item in app.expander)
    assert any("Noch keine Messdaten" in item.value for item in app.caption)
    formats = next(item for item in app.selectbox if item.label == "Dateiformat")
    assert list(formats.options) == ["auto", "csv"]  # the enum of the dataset tool's schema
    assert any(item.label == "Messdaten importieren" for item in app.button)
    assert all(item.label != "Messdaten löschen" for item in app.button)


def test_dataset_panel_shows_the_dataset_and_deletes_it(tmp_path, monkeypatch):
    app = _prepare(tmp_path, monkeypatch, with_dataset=True)
    assert any("export.csv" in item.value for item in app.markdown)
    assert any("2976 Werte" in item.value for item in app.caption)
    delete = next(item for item in app.button if item.label == "Messdaten löschen")
    assert delete.disabled is True
    next(item for item in app.checkbox if item.label.startswith("Messdaten und Berechnungen")).check().run()
    next(item for item in app.button if item.label == "Messdaten löschen").click().run()
    app.run()
    # The server on port 1 is unreachable; the deletion in GuideOS goes on and is reported.
    assert not app.exception, [e.value for e in app.exception]
    profile = LocalProfileRepository(tmp_path / "profiles.json").get(TOOLS_DOMAIN, "local")
    assert profile.dataset is None
    assert any("gelöscht" in item.value for item in app.markdown)
