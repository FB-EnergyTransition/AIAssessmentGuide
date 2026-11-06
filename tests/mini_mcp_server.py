"""A small FastMCP server over HTTP for the tool-client and admin tests:
one well-behaved tool, one that fails with a structured error, one that is
slow and one whose output violates its schema."""

import json
import socket
import threading
import time
from typing import Literal

import uvicorn
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.tools import ToolResult
from pydantic import BaseModel

MINI_TOKEN = "mini-token-0123456789"


class EchoOut(BaseModel):
    text: str
    n: int
    provenance: dict


class MiniServer:
    def __init__(self, url: str, server: uvicorn.Server, thread: threading.Thread) -> None:
        self.url = url
        self._server = server
        self._thread = thread
        self.calls: list[dict] = []

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def start_mini_server(token: str = MINI_TOKEN, slow_seconds: float = 3.0) -> MiniServer:
    auth = StaticTokenVerifier(tokens={token: {"client_id": "test", "scopes": ["tools"]}})
    mcp = FastMCP("mini-tools", version="0.0.1", auth=auth, mask_error_details=True)
    calls: list[dict] = []

    @mcp.tool(name="echo", description="Gibt den Text zurück. Voraussetzung: keine.",
              output_schema=EchoOut.model_json_schema())
    def echo(owner: str, text: str = "hallo", n: int = 1, mode: Literal["a", "b"] = "a") -> ToolResult:
        calls.append({"owner": owner, "text": text, "n": n, "mode": mode})
        return ToolResult(
            content=f"Echo {text}",
            structured_content={"text": text, "n": n, "provenance": {"calculation_id": f"calc-{text}"}},
        )

    @mcp.tool(name="fail", description="Scheitert immer mit not_found.",
              output_schema=EchoOut.model_json_schema())
    def fail(owner: str) -> ToolResult:
        raise McpToolError(json.dumps({"code": "not_found", "message": "Kein Datensatz.",
                                       "details": {"dataset_id": "ds_x"}}))

    @mcp.tool(name="slow", description="Braucht lange.", output_schema=EchoOut.model_json_schema())
    def slow(owner: str) -> ToolResult:
        time.sleep(slow_seconds)
        return ToolResult(content="spät", structured_content={"text": "spät", "n": 1, "provenance": {}})

    @mcp.tool(name="bad_output", description="Liefert etwas anderes als das Schema.",
              output_schema=EchoOut.model_json_schema())
    def bad_output(owner: str) -> ToolResult:
        return ToolResult(content="x", structured_content={"unexpected": True})

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(mcp.http_app(path="/mcp"), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    mini = MiniServer(f"http://127.0.0.1:{port}/mcp", server, thread)
    mini.calls = calls
    return mini
