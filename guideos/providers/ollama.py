import json
import logging
import time
from collections.abc import Iterable, Iterator
from typing import Any
from urllib import error, request

from guideos.chat import Message
from guideos.config import DEFAULT_OLLAMA_ENDPOINT, DEFAULT_OLLAMA_MODEL, provider_timeout_seconds
from guideos.providers.base import ModelWithoutToolsError, ProviderError, ToolCallRequest, ToolTurn

_NO_TOOL_SUPPORT = "does not support tools"


logger = logging.getLogger(__name__)


def _failure(message: str, endpoint: str, model: str | None = None) -> ProviderError:
    logger.warning(
        "ollama_request_failed endpoint=%s model=%s reason=%s",
        endpoint,
        model,
        message.splitlines()[0],
    )
    return ProviderError(message)


def _log_completed(kind: str, endpoint: str, model: str | None, started: float) -> None:
    logger.debug(
        "ollama_request_completed kind=%s endpoint=%s model=%s duration_ms=%d",
        kind,
        endpoint,
        model,
        (time.monotonic() - started) * 1000,
    )


def iter_ollama_stream_chunks(lines: Iterable[bytes | str]) -> Iterator[str]:
    for raw_line in lines:
        line = raw_line.decode("utf-8").strip() if isinstance(raw_line, bytes) else raw_line.strip()
        if not line:
            continue

        try:
            response_json = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama hat keine gültige JSON-Antwort geliefert.") from exc

        if "error" in response_json:
            raise ProviderError(str(response_json["error"]))

        message = response_json.get("message")
        if message is None and response_json.get("done"):
            continue
        if not isinstance(message, dict):
            raise ProviderError("Ollama-Antwort enthält kein Feld `message.content`.")
        if "content" not in message:
            raise ProviderError("Ollama-Antwort enthält kein Feld `message.content`.")

        content = message["content"]
        if content is None:
            continue
        if not isinstance(content, str):
            raise ProviderError("Ollama-Antwort enthält kein gültiges Feld `message.content`.")

        yield content


def parse_ollama_structured_response(payload: bytes | str) -> dict[str, Any]:
    try:
        response_json = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProviderError("Ollama hat keine gültige JSON-Antwort geliefert.") from exc

    if "error" in response_json:
        raise ProviderError(str(response_json["error"]))
    message = response_json.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ProviderError("Ollama-Antwort enthält kein Feld `message.content`.")
    try:
        content = json.loads(message["content"])
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "Ollama hat kein gültiges strukturiertes JSON geliefert."
        ) from exc
    if not isinstance(content, dict):
        raise ProviderError(
            "Ollama hat kein gültiges strukturiertes JSON-Objekt geliefert."
        )
    return content


def _ollama_error(message: str) -> ProviderError:
    if _NO_TOOL_SUPPORT in message.lower():
        return ModelWithoutToolsError(
            "Das gewählte Modell unterstützt keine Werkzeugaufrufe (Ollama: does not support tools)."
        )
    return ProviderError(message)


def parse_ollama_tool_turn(payload: bytes | str) -> ToolTurn:
    """The assistant message of a /api/chat answer with `tools`: text and the
    tool calls (`message.tool_calls[].function.name/arguments`; arguments
    may arrive as an object or as a JSON string)."""
    try:
        response_json = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProviderError("Ollama hat keine gültige JSON-Antwort geliefert.") from exc
    if isinstance(response_json, dict) and "error" in response_json:
        raise _ollama_error(str(response_json["error"]))
    message = response_json.get("message") if isinstance(response_json, dict) else None
    if not isinstance(message, dict):
        raise ProviderError("Ollama-Antwort enthält kein Feld `message`.")
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ProviderError("Ollama-Antwort enthält kein gültiges Feld `message.content`.")
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ProviderError("Ollama-Antwort enthält kein gültiges Feld `message.tool_calls`.")
    calls = []
    for item in raw_calls:
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            raise ProviderError("Ein Werkzeugaufruf von Ollama hat keinen Namen.")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"Die Argumente des Werkzeugaufrufs {name} sind kein gültiges JSON."
                ) from exc
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ProviderError(f"Die Argumente des Werkzeugaufrufs {name} sind kein JSON-Objekt.")
        calls.append(ToolCallRequest(name=name, arguments=arguments))
    assistant: dict[str, Any] = {"role": "assistant", "content": content}
    if raw_calls:
        assistant["tool_calls"] = raw_calls
    return ToolTurn(text=content, calls=calls, message=assistant)


def parse_ollama_models(raw: bytes) -> list[str]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderError("Ollama hat keine gültige Modellliste geliefert.") from exc
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise ProviderError("Ollama hat keine gültige Modellliste geliefert.")
    names = []
    for item in models:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            raise ProviderError("Ollama hat keine gültige Modellliste geliefert.")
        names.append(name)
    return names


def parse_ollama_embeddings(raw: bytes, expected: int) -> list[list[float]]:
    invalid = ProviderError("Ollama hat keine gültigen Embedding-Vektoren geliefert.")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise invalid from exc
    if isinstance(data, dict) and "error" in data:
        raise ProviderError(str(data["error"]))
    vectors = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise invalid
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise invalid
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in vector):
            raise invalid
    return [[float(item) for item in vector] for vector in vectors]


class OllamaProvider:
    name = "ollama"
    display_name = "Ollama"
    default_endpoint = DEFAULT_OLLAMA_ENDPOINT
    default_model = DEFAULT_OLLAMA_MODEL

    def stream_chat(
        self,
        endpoint: str,
        model: str,
        messages: list[Message],
    ) -> Iterator[str]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        body = json.dumps(payload).encode("utf-8")
        chat_request = request.Request(
            f"{endpoint}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        try:
            with request.urlopen(chat_request, timeout=provider_timeout_seconds()) as response:
                yield from iter_ollama_stream_chunks(response)
            _log_completed("stream", endpoint, model, started)
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            message = f"Ollama hat einen HTTP-Fehler gemeldet: {exc.code} {exc.reason}"
            if details:
                message = f"{message}\n{details}"
            raise _failure(message, endpoint, model) from exc
        except error.URLError as exc:
            raise _failure(f"Ollama-Endpunkt konnte nicht erreicht werden: {exc.reason}", endpoint, model) from exc
        except TimeoutError as exc:
            raise _failure("Die Anfrage an Ollama hat zu lange gedauert.", endpoint, model) from exc

    def generate_structured(
        self,
        endpoint: str,
        model: str,
        messages: list[Message],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        body = json.dumps(payload).encode("utf-8")
        chat_request = request.Request(
            f"{endpoint}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        try:
            with request.urlopen(chat_request, timeout=provider_timeout_seconds()) as response:
                result = parse_ollama_structured_response(response.read())
            _log_completed("structured", endpoint, model, started)
            return result
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            message = (
                f"Ollama hat einen HTTP-Fehler gemeldet: {exc.code} {exc.reason}"
            )
            if details:
                message = f"{message}\n{details}"
            raise _failure(message, endpoint, model) from exc
        except error.URLError as exc:
            raise _failure(
                f"Ollama-Endpunkt konnte nicht erreicht werden: {exc.reason}",
                endpoint,
                model,
            ) from exc
        except TimeoutError as exc:
            raise _failure("Die Anfrage an Ollama hat zu lange gedauert.", endpoint, model) from exc

    def chat_with_tools(
        self,
        endpoint: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ToolTurn:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0},
        }
        chat_request = request.Request(
            f"{endpoint}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with request.urlopen(chat_request, timeout=provider_timeout_seconds()) as response:
                turn = parse_ollama_tool_turn(response.read())
            _log_completed("tools", endpoint, model, started)
            return turn
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if _NO_TOOL_SUPPORT in details.lower():
                logger.warning("ollama_model_without_tools endpoint=%s model=%s", endpoint, model)
                raise _ollama_error(details) from exc
            message = f"Ollama hat einen HTTP-Fehler gemeldet: {exc.code} {exc.reason}"
            if details:
                message = f"{message}\n{details}"
            raise _failure(message, endpoint, model) from exc
        except error.URLError as exc:
            raise _failure(
                f"Ollama-Endpunkt konnte nicht erreicht werden: {exc.reason}", endpoint, model
            ) from exc
        except TimeoutError as exc:
            raise _failure("Die Anfrage an Ollama hat zu lange gedauert.", endpoint, model) from exc

    def list_models(self, endpoint: str) -> list[str]:
        tags_request = request.Request(f"{endpoint}/api/tags", method="GET")
        started = time.monotonic()
        try:
            with request.urlopen(tags_request, timeout=5) as response:
                models = parse_ollama_models(response.read())
            _log_completed("models", endpoint, None, started)
            return models
        except error.HTTPError as exc:
            raise _failure(
                f"Ollama hat einen HTTP-Fehler gemeldet: {exc.code} {exc.reason}",
                endpoint,
            ) from exc
        except error.URLError as exc:
            raise _failure(f"Ollama-Endpunkt konnte nicht erreicht werden: {exc.reason}", endpoint) from exc
        except TimeoutError as exc:
            raise _failure("Die Anfrage an Ollama hat zu lange gedauert.", endpoint) from exc

    def rerank(self, endpoint: str, model: str, query: str, texts: list[str]) -> list[float]:
        """Ollama serves no cross-encoder endpoint, so the configured model
        scores each passage for the query as structured output at
        temperature 0. Scores are 0 to 1, one per text."""
        schema = {
            "type": "object",
            "properties": {
                "scores": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": len(texts),
                    "maxItems": len(texts),
                }
            },
            "required": ["scores"],
            "additionalProperties": False,
        }
        messages: list[Message] = [
            {
                "role": "system",
                "content": (
                    "Du bewertest, wie gut jeder nummerierte Textabschnitt eine Suchanfrage "
                    "beantwortet. Gib je Abschnitt eine Zahl von 0 (irrelevant) bis 1 (beantwortet "
                    "die Anfrage direkt) in derselben Reihenfolge als JSON gemäß Schema zurück."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"query": query, "passages": [{"index": i, "text": t} for i, t in enumerate(texts)]},
                    ensure_ascii=False,
                ),
            },
        ]
        data = self.generate_structured(endpoint, model, messages, schema)
        scores = data.get("scores") if isinstance(data, dict) else None
        if (
            not isinstance(scores, list)
            or len(scores) != len(texts)
            or not all(isinstance(item, (int, float)) for item in scores)
        ):
            raise _failure("Der Reranker hat keine gültige Bewertungsliste geliefert.", endpoint, model)
        return [float(item) for item in scores]

    def embed(self, endpoint: str, model: str, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"model": model, "input": texts}).encode("utf-8")
        embed_request = request.Request(
            f"{endpoint}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with request.urlopen(embed_request, timeout=provider_timeout_seconds()) as response:
                vectors = parse_ollama_embeddings(response.read(), len(texts))
            _log_completed("embed", endpoint, model, started)
            return vectors
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            message = f"Ollama hat einen HTTP-Fehler gemeldet: {exc.code} {exc.reason}"
            if details:
                message = f"{message}\n{details}"
            raise _failure(message, endpoint, model) from exc
        except error.URLError as exc:
            raise _failure(
                f"Ollama-Endpunkt konnte nicht erreicht werden: {exc.reason}",
                endpoint,
                model,
            ) from exc
        except TimeoutError as exc:
            raise _failure("Die Anfrage an Ollama hat zu lange gedauert.", endpoint, model) from exc
