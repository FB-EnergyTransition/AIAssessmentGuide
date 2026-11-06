"""Domain-Admin tab Werkzeuge: register an MCP server, fetch its tools and
allow some of them, against a mini FastMCP server over HTTP."""

import pytest
from streamlit.testing.v1 import AppTest

from guideos.domains.catalog import LocalCatalogStore, list_domain_versions
from guideos.tools.store import LocalToolServerStore
from tests.mini_mcp_server import MINI_TOKEN, start_mini_server


@pytest.fixture(scope="module")
def mini():
    server = start_mini_server()
    yield server
    server.stop()


@pytest.fixture
def admin_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ADMIN_ENABLED", "1")
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setenv("GUIDEOS_TOOL_SERVERS_PATH", str(tmp_path / "mcp_servers.json"))
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Domain-Admin").run()
    ref = next(r for r in list_domain_versions()
               if r.domain_id == "verein-demo" and r.version == "1.0.0")
    next(item for item in app.selectbox if item.label == "Domain-Version").set_value(ref).run()
    assert not app.error
    return app, LocalToolServerStore(tmp_path / "mcp_servers.json")


def _button(app, label):
    return next(item for item in app.button if item.label == label)


def _input(app, label):
    return next(item for item in app.text_input if item.label == label)


def test_admin_registers_a_server_fetches_tools_and_allows_one(admin_app, mini):
    app, store = admin_app
    assert any("kein MCP-Server registriert" in item.value for item in app.info)

    _input(app, "Name des Servers").set_value("demo")
    _input(app, "Adresse des Servers (URL)").set_value(mini.url)
    _input(app, "Token").set_value(MINI_TOKEN)
    _button(app, "Server speichern").click().run()
    app.run()
    assert not app.error, [e.value for e in app.error]
    saved = store.get("verein-demo", "demo")
    assert saved is not None and saved.url == mini.url and saved.token == MINI_TOKEN
    assert any("demo" in item.value and "noch nicht abgerufen" in item.value for item in app.markdown)

    _button(app, "Werkzeuge abrufen").click().run()
    app.run()
    assert not app.error, [e.value for e in app.error]
    refreshed = store.get("verein-demo", "demo")
    assert {spec.name for spec in refreshed.tool_specs} == {"echo", "fail", "slow", "bad_output"}
    assert refreshed.allowed_tools == []

    next(item for item in app.checkbox if item.label == "Freigeben: echo").check()
    _button(app, "Freigaben speichern").click().run()
    app.run()
    assert not app.error, [e.value for e in app.error]
    allowed = store.get("verein-demo", "demo")
    assert allowed.allowed_tools == ["echo"]
    assert any("1 von 4 Werkzeugen freigegeben" in item.value for item in app.markdown)


def test_admin_rejects_an_invalid_server_name_and_url(admin_app):
    app, store = admin_app
    _input(app, "Name des Servers").set_value("Mein Server")
    _input(app, "Adresse des Servers (URL)").set_value("http://127.0.0.1:1/mcp")
    _button(app, "Server speichern").click().run()
    assert any("Kleinbuchstaben" in item.value for item in app.error)

    _input(app, "Name des Servers").set_value("ok")
    _input(app, "Adresse des Servers (URL)").set_value("ftp://x/mcp")
    _button(app, "Server speichern").click().run()
    assert any("http://" in item.value for item in app.error)
    assert store.list("verein-demo") == []
