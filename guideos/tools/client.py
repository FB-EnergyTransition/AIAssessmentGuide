"""ToolClient: a synchronous facade over fastmcp.Client. Streamlit scripts and
the synchronous FastAPI handlers run in threads without an event loop, so
every call opens its own connection and runs it to completion."""

import asyncio
import concurrent.futures
import json
import logging
import time
from typing import Any

from fastmcp import Client
from mcp.shared.exceptions import MCPError

from guideos.tools.models import OWNER_FIELD, ToolCall, ToolError, ToolResult, ToolServer, ToolSpec

logger = logging.getLogger("guideos.tools")
DEFAULT_CALL_TIMEOUT = 60.0


class ToolClient:
    def __init__(self, connect_timeout: float = 10.0) -> None:
        self.connect_timeout = connect_timeout

    def list_tools(self, server: ToolServer) -> list[ToolSpec]:
        async def run():
            async with Client(server.url, auth=server.token or None, timeout=self.connect_timeout) as client:
                return await client.list_tools()

        started = time.monotonic()
        try:
            tools = _run(run())
        except Exception as exc:
            raise _unavailable(server, exc) from exc
        logger.info("tool_server_listed server=%s tools=%d duration_ms=%d", server.name, len(tools),
                    (time.monotonic() - started) * 1000)
        return [
            ToolSpec(name=tool.name, description=tool.description or "",
                     input_schema=dict(tool.input_schema or {"type": "object"}),
                     output_schema=dict(tool.output_schema) if tool.output_schema else None,
                     server_name=server.name)
            for tool in tools
        ]

    def call(self, server: ToolServer, name: str, arguments: dict[str, Any],
             timeout: float = DEFAULT_CALL_TIMEOUT) -> ToolResult:
        async def run():
            async with Client(server.url, auth=server.token or None, timeout=self.connect_timeout) as client:
                return await client.call_tool(name, arguments, timeout=timeout, raise_on_error=False)

        started = time.monotonic()
        try:
            result = _run(run())
        except MCPError as exc:
            if "timed out" in str(exc).lower():
                logger.warning("tool_call_timeout server=%s tool=%s timeout_s=%s", server.name, name, timeout)
                raise ToolError("tool_timeout", f"Das Werkzeug {name} hat nicht innerhalb von "
                                f"{timeout:g} Sekunden geantwortet.", {"tool": name}) from exc
            logger.warning("tool_call_rejected server=%s tool=%s", server.name, name)
            raise ToolError("tool_error", f"Der Werkzeugserver hat den Aufruf von {name} abgewiesen: "
                            f"{exc}", {"tool": name, "code": "rejected"}) from exc
        except RuntimeError as exc:
            if "structured content" in str(exc).lower():
                logger.warning("tool_output_invalid server=%s tool=%s", server.name, name)
                raise ToolError("tool_output_invalid", f"Die Ausgabe des Werkzeugs {name} entspricht "
                                "nicht ihrem Schema.", {"tool": name}) from exc
            raise _unavailable(server, exc) from exc
        except Exception as exc:
            raise _unavailable(server, exc) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        text = " ".join(getattr(block, "text", "") for block in result.content or []).strip()
        if result.is_error:
            payload = _error_payload(text)
            logger.info("tool_call_failed server=%s tool=%s code=%s duration_ms=%d", server.name, name,
                        payload["code"], duration_ms)
            raise ToolError("tool_error", payload["message"],
                            {"tool": name, "code": payload["code"], "details": payload["details"]})
        structured = result.structured_content if isinstance(result.structured_content, dict) else None
        provenance = (structured or {}).get("provenance")
        calculation_id = provenance.get("calculation_id") if isinstance(provenance, dict) else None
        logger.info("tool_call_completed server=%s tool=%s calculation_id=%s duration_ms=%d",
                    server.name, name, calculation_id, duration_ms)
        return ToolResult(
            call=ToolCall(name=name, arguments={k: v for k, v in arguments.items() if k != OWNER_FIELD}),
            server_name=server.name, structured_content=structured, summary=text,
            calculation_id=calculation_id, duration_ms=duration_ms,
        )


def _run(coro):
    """Run a coroutine to completion from synchronous code, also when the
    current thread already drives an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def _unavailable(server: ToolServer, exc: Exception) -> ToolError:
    reason = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
    logger.warning("tool_server_unavailable server=%s reason=%s", server.name, reason)
    return ToolError("tool_server_unavailable",
                     f"Der Werkzeugserver {server.name} ist nicht erreichbar oder lehnt die Verbindung ab.",
                     {"server": server.name, "reason": reason})


def _error_payload(text: str) -> dict[str, Any]:
    """The JSON {code, message, details} a server puts into the error text; any
    other text becomes code unknown."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("code"), str):
        return {"code": data["code"], "message": str(data.get("message") or data["code"]),
                "details": data.get("details") if isinstance(data.get("details"), dict) else {}}
    return {"code": "unknown", "message": text or "Das Werkzeug hat einen Fehler gemeldet.", "details": {}}
