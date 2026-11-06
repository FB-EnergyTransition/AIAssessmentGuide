"""run_tool_loop: the model answers with tool calls or text; each call is
checked by the guard, executed by the client and fed back as a tool message
until the model answers with text, the budget is used up or a server fails."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from guideos.providers.base import ModelWithoutToolsError
from guideos.tools.client import DEFAULT_CALL_TIMEOUT
from guideos.tools.guard import ToolGuard
from guideos.tools.models import ToolError, ToolResult, ToolServer

logger = logging.getLogger("guideos.tools")
StopReason = Literal["text", "budget", "error", "model_without_tools", "no_tools"]
_FATAL_CODES = {"tool_server_unavailable", "tool_timeout"}


@dataclass
class LoopOutcome:
    tool_results: list[ToolResult] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    stop_reason: StopReason = "text"
    error: ToolError | None = None


def tool_message(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Tool output or a tool error as a transcript message; the content is JSON
    data for the model, never free text it should obey."""
    return {"role": "tool", "tool_name": name,
            "content": json.dumps(payload, ensure_ascii=False, default=str)}


def run_tool_loop(provider, endpoint: str, model: str, messages: list[dict[str, Any]],
                  guard: ToolGuard, client, servers: dict[str, ToolServer],
                  timeout: float = DEFAULT_CALL_TIMEOUT) -> LoopOutcome:
    outcome = LoopOutcome(transcript=[dict(message) for message in messages])
    if not guard.specs:
        outcome.stop_reason = "no_tools"
        return outcome
    tools = [spec.for_model() for spec in guard.specs]
    while True:
        try:
            guard.budget.check()
        except ToolError as exc:
            outcome.stop_reason, outcome.error = "budget", exc
            logger.info("tool_loop_stopped reason=budget calls=%d", guard.budget.calls_made)
            return outcome
        try:
            turn = provider.chat_with_tools(endpoint, model, outcome.transcript, tools)
        except ModelWithoutToolsError as exc:
            outcome.stop_reason = "model_without_tools"
            outcome.error = ToolError("model_without_tools", str(exc), {"model": model})
            logger.warning("tool_loop_stopped reason=model_without_tools model=%s", model)
            return outcome
        if not turn.calls:
            outcome.final_text = turn.text
            outcome.stop_reason = "text"
            logger.info("tool_loop_finished calls=%d results=%d", guard.budget.calls_made,
                        len(outcome.tool_results))
            return outcome
        outcome.transcript.append(dict(turn.message))
        for call in turn.calls:
            try:
                guard.budget.check()
            except ToolError as exc:
                outcome.transcript.append(tool_message(call.name, {"error": exc.code, "message": exc.message}))
                outcome.stop_reason, outcome.error = "budget", exc
                logger.info("tool_loop_stopped reason=budget calls=%d", guard.budget.calls_made)
                return outcome
            guard.budget.record()
            try:
                spec = guard.spec_for(call.name)
                arguments = guard.check_arguments(spec, call.arguments)
                result = client.call(servers[spec.server_name], spec.name, guard.with_owner(arguments),
                                     timeout)
                guard.check_output(spec, result.structured_content)
            except ToolError as exc:
                logger.info("tool_loop_call_failed tool=%s code=%s", call.name, exc.code)
                if exc.code in _FATAL_CODES:
                    outcome.stop_reason, outcome.error = "error", exc
                    return outcome
                outcome.transcript.append(tool_message(call.name, {
                    "error": exc.code, "message": exc.message, "details": exc.details,
                }))
                continue
            outcome.tool_results.append(result)
            outcome.transcript.append(tool_message(call.name, result.structured_content or
                                                   {"summary": result.summary}))
