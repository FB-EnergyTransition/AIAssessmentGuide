"""guideos.tools.client: the synchronous facade over fastmcp.Client against a
mini FastMCP server over HTTP with a bearer token."""

import pytest

from guideos.tools.client import ToolClient
from guideos.tools.models import ToolError, ToolServer
from tests.mini_mcp_server import MINI_TOKEN, start_mini_server


@pytest.fixture(scope="module")
def mini():
    server = start_mini_server(slow_seconds=3.0)
    yield server
    server.stop()


def _registration(url, token=MINI_TOKEN, **overrides) -> ToolServer:
    values = {"domain_id": "verein-demo", "name": "mini", "url": url, "token": token}
    values.update(overrides)
    return ToolServer.create(**values)


def test_list_tools_returns_specs_with_schemas(mini):
    specs = ToolClient().list_tools(_registration(mini.url))
    by_name = {spec.name: spec for spec in specs}
    assert set(by_name) == {"echo", "fail", "slow", "bad_output"}
    echo = by_name["echo"]
    assert echo.server_name == "mini"
    assert echo.description.startswith("Gibt den Text zurück")
    assert "owner" in echo.input_schema["properties"]
    assert echo.input_schema["properties"]["mode"]["enum"] == ["a", "b"]
    assert echo.output_schema["required"] == ["text", "n", "provenance"]


def test_call_returns_summary_structured_content_and_calculation_id(mini):
    result = ToolClient().call(_registration(mini.url), "echo",
                               {"owner": "teilnehmer-1", "text": "servus", "n": 2}, timeout=10)
    assert result.call.name == "echo"
    assert result.call.arguments == {"text": "servus", "n": 2}  # owner is not recorded
    assert result.server_name == "mini"
    assert result.summary == "Echo servus"
    assert result.structured_content["text"] == "servus"
    assert result.calculation_id == "calc-servus"
    assert result.duration_ms >= 0
    assert mini.calls[-1]["owner"] == "teilnehmer-1"


def test_tool_errors_timeouts_and_invalid_outputs_have_codes(mini):
    client = ToolClient()
    with pytest.raises(ToolError) as excinfo:
        client.call(_registration(mini.url), "fail", {"owner": "p"}, timeout=10)
    assert excinfo.value.code == "tool_error"
    assert excinfo.value.details["code"] == "not_found"
    assert excinfo.value.message == "Kein Datensatz."
    assert excinfo.value.details["details"] == {"dataset_id": "ds_x"}

    with pytest.raises(ToolError) as excinfo:
        client.call(_registration(mini.url), "slow", {"owner": "p"}, timeout=0.5)
    assert excinfo.value.code == "tool_timeout"

    with pytest.raises(ToolError) as excinfo:
        client.call(_registration(mini.url), "bad_output", {"owner": "p"}, timeout=10)
    assert excinfo.value.code == "tool_output_invalid"

    with pytest.raises(ToolError) as excinfo:
        client.call(_registration(mini.url), "unbekannt", {"owner": "p"}, timeout=10)
    assert excinfo.value.code == "tool_error"


def test_wrong_token_and_unreachable_server_are_unavailable(mini):
    client = ToolClient(connect_timeout=2)
    with pytest.raises(ToolError) as excinfo:
        client.list_tools(_registration(mini.url, token="falsches-token-0123456789"))
    assert excinfo.value.code == "tool_server_unavailable"
    with pytest.raises(ToolError) as excinfo:
        client.call(_registration("http://127.0.0.1:1/mcp"), "echo", {"owner": "p"}, timeout=2)
    assert excinfo.value.code == "tool_server_unavailable"
    assert excinfo.value.message.startswith("Der Werkzeugserver mini ist nicht erreichbar")
