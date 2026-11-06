"""ToolService: registrations per domain, the cached tool specs the model may
use, GuideOS-driven calls (upload, deletion) and the tool loop for the
advisory. Logs servers, tools, codes and durations, never arguments or
outputs."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from guideos.tools.client import DEFAULT_CALL_TIMEOUT
from guideos.tools.guard import ToolGuard
from guideos.tools.loop import LoopOutcome, run_tool_loop
from guideos.tools.models import ToolError, ToolResult, ToolServer, ToolSpec
from guideos.tools.store import ToolServerStore

logger = logging.getLogger("guideos.tools")


class ToolService:
    def __init__(self, store: ToolServerStore, client, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    # --- registrations (Domain-Admin)

    def list_servers(self, domain_id: str) -> list[ToolServer]:
        return self.store.list(domain_id)

    def get_server(self, domain_id: str, name: str) -> ToolServer | None:
        return self.store.get(domain_id, name)

    def register(self, domain_id: str, name: str, url: str, token: str, enabled: bool = True) -> ToolServer:
        """Create a server or update url, token and state of an existing one;
        allowed tools and the schema cache survive an update."""
        existing = self.store.get(domain_id, name)
        if existing is None:
            server = ToolServer.create(domain_id, name, url, token, enabled, now=self._clock())
        else:
            server = existing.model_copy(update={"url": url, "token": token, "enabled": enabled,
                                                 "updated_at": self._clock()})
            server = ToolServer.model_validate(server.model_dump())
        self.store.save(server)
        logger.info("tool_server_saved domain_id=%s server=%s enabled=%s", domain_id, name, enabled)
        return server

    def remove(self, domain_id: str, name: str) -> bool:
        removed = self.store.delete(domain_id, name)
        if removed:
            logger.info("tool_server_removed domain_id=%s server=%s", domain_id, name)
        return removed

    def refresh_tools(self, domain_id: str, name: str) -> ToolServer:
        server = self._require(domain_id, name)
        specs = self.client.list_tools(server)
        names = {spec.name for spec in specs}
        server = server.model_copy(update={
            "tool_specs": specs, "tools_fetched_at": self._clock(), "updated_at": self._clock(),
            "allowed_tools": [tool for tool in server.allowed_tools if tool in names],
        })
        self.store.save(server)
        logger.info("tool_server_refreshed domain_id=%s server=%s tools=%d", domain_id, name, len(specs))
        return server

    def set_allowed_tools(self, domain_id: str, name: str, allowed: list[str]) -> ToolServer:
        server = self._require(domain_id, name)
        known = {spec.name for spec in server.tool_specs}
        unknown = [tool for tool in allowed if tool not in known]
        if unknown:
            raise ToolError("tool_not_allowed",
                            "Diese Werkzeuge bietet der Server nicht an: " + ", ".join(unknown),
                            {"server": name, "unknown": unknown})
        server = server.model_copy(update={"allowed_tools": list(dict.fromkeys(allowed)),
                                           "updated_at": self._clock()})
        self.store.save(server)
        logger.info("tool_server_allowed domain_id=%s server=%s allowed=%d", domain_id, name, len(allowed))
        return server

    # --- what the advisory may use

    def specs_for(self, domain_id: str) -> list[ToolSpec]:
        """Allowed tools of the enabled servers of a domain, from the cache;
        a tool name offered twice is taken from the first server."""
        specs: list[ToolSpec] = []
        seen: set[str] = set()
        for server in self.store.list(domain_id):
            if not server.enabled:
                continue
            for spec in server.allowed_specs():
                if spec.name in seen:
                    logger.warning("tool_name_duplicate domain_id=%s server=%s tool=%s",
                                   domain_id, server.name, spec.name)
                    continue
                seen.add(spec.name)
                specs.append(spec)
        return specs

    def servers_by_name(self, domain_id: str) -> dict[str, ToolServer]:
        return {server.name: server for server in self.store.list(domain_id) if server.enabled}

    def call(self, domain_id: str, participant_id: str, tool_name: str, arguments: dict,
             timeout: float = DEFAULT_CALL_TIMEOUT) -> ToolResult:
        """A call GuideOS makes itself (dataset upload, deletion): same
        allowlist and checks as for the model, owner set by GuideOS."""
        guard = ToolGuard(self.specs_for(domain_id), participant_id, max_calls=1, max_seconds=timeout + 1)
        spec = guard.spec_for(tool_name)
        checked = guard.check_arguments(spec, arguments)
        result = self.client.call(self.servers_by_name(domain_id)[spec.server_name], spec.name,
                                  guard.with_owner(checked), timeout)
        guard.check_output(spec, result.structured_content)
        return result

    def run_loop(self, domain_id: str, participant_id: str, provider, endpoint: str, model: str,
                 messages: list[dict], max_calls: int = 6, max_seconds: float = 90.0,
                 timeout: float = DEFAULT_CALL_TIMEOUT) -> LoopOutcome:
        guard = ToolGuard(self.specs_for(domain_id), participant_id, max_calls, max_seconds)
        return run_tool_loop(provider, endpoint, model, messages, guard, self.client,
                             self.servers_by_name(domain_id), timeout)

    def _require(self, domain_id: str, name: str) -> ToolServer:
        server = self.store.get(domain_id, name)
        if server is None:
            raise ToolError("tool_server_unavailable", f"Der Server {name} ist für diese Domäne nicht "
                            "registriert.", {"server": name})
        return server
