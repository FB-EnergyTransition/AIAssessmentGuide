"""guideos.tools.store: MCP server registrations per domain, locally as a
versioned JSON file and in PostgreSQL (skipped without a database)."""

import json
import os
from uuid import uuid4

import pytest

from guideos.tools.models import ToolServer, ToolSpec
from guideos.tools.store import LocalToolServerStore, ToolStoreError

POSTGRES_URL = os.environ.get("GUIDEOS_TEST_POSTGRES_URL")


def _server(domain_id="verein-demo", name="demo", **overrides) -> ToolServer:
    values = {"domain_id": domain_id, "name": name, "url": "http://127.0.0.1:8600/mcp",
              "token": "geheim-0123456789abcdef"}
    values.update(overrides)
    return ToolServer.create(**values)


def test_tool_server_model_validates_name_and_url():
    server = _server()
    assert server.server_id.startswith("srv_") and server.enabled is True
    assert server.allowed_tools == [] and server.tool_specs == []
    with pytest.raises(ValueError):
        _server(name="Mein Server")
    with pytest.raises(ValueError):
        _server(url="ftp://example.org/mcp")
    with pytest.raises(ValueError):
        _server(url="127.0.0.1:8600/mcp")


def test_tool_spec_for_model_hides_owner_and_uses_the_function_format():
    spec = ToolSpec(
        name="echo", description="Gibt zurück.", server_name="demo",
        input_schema={"type": "object", "properties": {"owner": {"type": "string"},
                                                      "text": {"type": "string"}},
                      "required": ["owner", "text"], "additionalProperties": False},
        output_schema={"type": "object"},
    )
    exposed = spec.for_model()
    assert exposed["type"] == "function"
    assert exposed["function"]["name"] == "echo"
    assert exposed["function"]["description"] == "Gibt zurück."
    assert "owner" not in exposed["function"]["parameters"]["properties"]
    assert exposed["function"]["parameters"]["required"] == ["text"]
    assert "owner" in spec.input_schema["properties"]  # the original stays intact


def test_local_store_roundtrip_unique_names_and_versioned_file(tmp_path):
    path = tmp_path / "mcp_servers.json"
    store = LocalToolServerStore(path)
    assert store.list("verein-demo") == []

    server = _server()
    store.save(server)
    other_domain = _server(domain_id="other-demo")
    store.save(other_domain)
    assert [s.name for s in store.list("verein-demo")] == ["demo"]
    assert store.get("verein-demo", "demo").token == "geheim-0123456789abcdef"
    assert store.get("verein-demo", "unbekannt") is None

    updated = server.model_copy(update={"url": "http://127.0.0.1:8601/mcp", "enabled": False})
    store.save(updated)
    assert store.get("verein-demo", "demo").url == "http://127.0.0.1:8601/mcp"
    assert len(store.list("verein-demo")) == 1

    with pytest.raises(ToolStoreError, match="existiert bereits"):
        store.save(_server())  # a second server_id with the same name in the domain
    assert store.delete("verein-demo", "demo") is True
    assert store.delete("verein-demo", "demo") is False
    assert store.list("other-demo")[0].domain_id == "other-demo"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    path.write_text(json.dumps({"version": 99, "servers": []}), encoding="utf-8")
    with pytest.raises(ToolStoreError, match="Version"):
        store.list("verein-demo")
    path.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ToolStoreError, match="JSON"):
        store.list("verein-demo")


@pytest.mark.skipif(not POSTGRES_URL, reason="GUIDEOS_TEST_POSTGRES_URL fehlt")
def test_postgres_store_roundtrip_and_unique_names():
    from guideos.assessment.migrations import migrate_database
    from guideos.tools.postgres_store import PostgresToolServerStore

    migrate_database(POSTGRES_URL)
    store = PostgresToolServerStore(POSTGRES_URL)
    domain_id = f"tools-pg-{uuid4().hex[:8]}"
    server = _server(domain_id=domain_id)
    store.save(server)
    store.save(server.model_copy(update={"enabled": False, "allowed_tools": ["echo"]}))
    loaded = store.get(domain_id, "demo")
    assert loaded.enabled is False and loaded.allowed_tools == ["echo"]
    assert loaded.token == server.token
    assert [s.server_id for s in store.list(domain_id)] == [server.server_id]
    with pytest.raises(ToolStoreError, match="existiert bereits"):
        store.save(_server(domain_id=domain_id))
    assert store.delete(domain_id, "demo") is True
    assert store.get(domain_id, "demo") is None
