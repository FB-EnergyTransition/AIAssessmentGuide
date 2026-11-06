import pytest

from guideos.providers.base import ProviderError
from guideos.providers.ollama import (
    OllamaProvider,
    iter_ollama_stream_chunks,
    parse_ollama_embeddings,
    parse_ollama_structured_response,
)


def test_iter_ollama_stream_chunks_extracts_message_content():
    lines = [
        b'{"message":{"role":"assistant","content":"Hel"},"done":false}\n',
        b'{"message":{"role":"assistant","content":"lo"},"done":false}\n',
        b'{"done":true}\n',
    ]

    assert list(iter_ollama_stream_chunks(lines)) == ["Hel", "lo"]


def test_iter_ollama_stream_chunks_ignores_empty_lines_and_done_without_message():
    lines = [
        b"\n",
        b'{"message":{"role":"assistant","content":""},"done":false}\n',
        b'{"done":true}\n',
    ]

    assert list(iter_ollama_stream_chunks(lines)) == [""]


def test_iter_ollama_stream_chunks_maps_ollama_error_to_provider_error():
    lines = [b'{"error":"model not found"}\n']

    with pytest.raises(ProviderError, match="model not found"):
        list(iter_ollama_stream_chunks(lines))


def test_iter_ollama_stream_chunks_rejects_malformed_json():
    lines = [b"{not-json}\n"]

    with pytest.raises(ProviderError, match="JSON"):
        list(iter_ollama_stream_chunks(lines))


def test_iter_ollama_stream_chunks_rejects_missing_message_content():
    lines = [b'{"message":{"role":"assistant"},"done":false}\n']

    with pytest.raises(ProviderError, match="message.content"):
        list(iter_ollama_stream_chunks(lines))


def test_parse_ollama_structured_response_extracts_json_content():
    payload = b'{"message":{"role":"assistant","content":"{\\"decision\\":\\"valid\\"}"}}'

    assert parse_ollama_structured_response(payload) == {"decision": "valid"}


def test_parse_ollama_structured_response_rejects_invalid_content():
    payload = b'{"message":{"role":"assistant","content":"not-json"}}'

    with pytest.raises(ProviderError, match="strukturiertes JSON"):
        parse_ollama_structured_response(payload)


def test_generate_structured_sends_schema_and_disables_streaming(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"message":{"content":"{\\"decision\\":\\"valid\\"}"}}'

    def fake_urlopen(chat_request, timeout):
        captured["body"] = chat_request.data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)
    schema = {"type": "object"}

    result = OllamaProvider().generate_structured(
        "http://localhost:11434",
        "llama3.2",
        [{"role": "user", "content": "Hallo"}],
        schema,
    )

    import json

    body = json.loads(captured["body"])
    assert body["stream"] is False
    assert body["format"] == schema
    assert result == {"decision": "valid"}


def test_generate_structured_uses_temperature_zero(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"message":{"content":"{\\"decision\\":\\"valid\\"}"}}'

    def fake_urlopen(chat_request, timeout):
        captured["body"] = chat_request.data
        return FakeResponse()

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    OllamaProvider().generate_structured(
        "http://localhost:11434",
        "llama3.2",
        [{"role": "user", "content": "Hallo"}],
        {"type": "object"},
    )

    import json

    body = json.loads(captured["body"])
    assert body["options"] == {"temperature": 0}


def test_list_models_reads_the_tags_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"models":[{"name":"gpt-oss:20b","size":1},{"name":"qwen3:8b"}]}'

    def fake_urlopen(tags_request, timeout):
        captured["url"] = tags_request.full_url
        captured["method"] = tags_request.get_method()
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    models = OllamaProvider().list_models("http://localhost:11434")

    assert models == ["gpt-oss:20b", "qwen3:8b"]
    assert captured["url"] == "http://localhost:11434/api/tags"
    assert captured["method"] == "GET"
    assert captured["timeout"] <= 10


def test_list_models_maps_connection_errors_to_provider_error(monkeypatch):
    from urllib import error

    def fake_urlopen(tags_request, timeout):
        raise error.URLError("connection refused")

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    with pytest.raises(ProviderError):
        OllamaProvider().list_models("http://localhost:11434")


@pytest.mark.parametrize("payload", [b"not json", b'{"models": "x"}', b'{"models": [{"size": 1}]}'])
def test_parse_ollama_models_rejects_malformed_payloads(payload):
    from guideos.providers.ollama import parse_ollama_models

    with pytest.raises(ProviderError):
        parse_ollama_models(payload)


def test_embed_sends_all_texts_and_returns_one_vector_per_text(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"embeddings": [[0.1, 0.2], [0.3, 0.4]]}'

    def fake_urlopen(embed_request, timeout):
        captured["url"] = embed_request.full_url
        captured["body"] = embed_request.data
        return FakeResponse()

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)

    vectors = OllamaProvider().embed("http://localhost:11434", "bge-m3", ["eins", "zwei"])

    import json

    assert captured["url"] == "http://localhost:11434/api/embed"
    assert json.loads(captured["body"]) == {"model": "bge-m3", "input": ["eins", "zwei"]}
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.parametrize(
    "payload",
    [
        b"kein json",
        b'{"embeddings": [[0.1]]}',
        b'{"embeddings": [[0.1], ["x"]]}',
        b'{"embeddings": [[0.1], []]}',
        b'{"vectors": []}',
    ],
)
def test_parse_ollama_embeddings_rejects_malformed_payloads(payload):
    with pytest.raises(ProviderError, match="Embedding"):
        parse_ollama_embeddings(payload, 2)


def test_parse_ollama_embeddings_reports_ollama_errors():
    with pytest.raises(ProviderError, match="does not support embeddings"):
        parse_ollama_embeddings(b'{"error": "this model does not support embeddings"}', 1)


def test_parse_ollama_tool_turn_extracts_calls_text_and_the_assistant_message():
    from guideos.providers.ollama import parse_ollama_tool_turn

    payload = (
        b'{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":'
        b'"analyze_dataset","arguments":{"dataset_id":"ds_1"}}},{"function":{"name":"echo",'
        b'"arguments":"{\\"text\\":\\"hallo\\"}"}}]},"done":true}'
    )
    turn = parse_ollama_tool_turn(payload)
    assert turn.text == ""
    assert [(c.name, c.arguments) for c in turn.calls] == [
        ("analyze_dataset", {"dataset_id": "ds_1"}), ("echo", {"text": "hallo"}),
    ]
    assert turn.message["role"] == "assistant" and len(turn.message["tool_calls"]) == 2

    text_only = parse_ollama_tool_turn(b'{"message":{"role":"assistant","content":"Fertig."}}')
    assert text_only.text == "Fertig." and text_only.calls == []
    assert text_only.message == {"role": "assistant", "content": "Fertig."}


@pytest.mark.parametrize("payload", [
    b"{not-json}",
    b'{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":"x","arguments":"nicht json"}}]}}',
    b'{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"arguments":{}}}]}}',
    b'{"message":"kein Objekt"}',
])
def test_parse_ollama_tool_turn_rejects_malformed_payloads(payload):
    from guideos.providers.ollama import parse_ollama_tool_turn

    with pytest.raises(ProviderError):
        parse_ollama_tool_turn(payload)


def test_parse_ollama_tool_turn_maps_models_without_tool_support():
    from guideos.providers.base import ModelWithoutToolsError
    from guideos.providers.ollama import parse_ollama_tool_turn

    with pytest.raises(ModelWithoutToolsError):
        parse_ollama_tool_turn(b'{"error":"registry.ollama.ai/library/llama2:latest does not support tools"}')
    with pytest.raises(ProviderError, match="anderer Fehler"):
        parse_ollama_tool_turn(b'{"error":"anderer Fehler"}')


def test_chat_with_tools_sends_tools_without_streaming_at_temperature_zero(monkeypatch):
    import json as _json
    from guideos.providers import ollama as ollama_module

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (b'{"message":{"role":"assistant","content":"","tool_calls":[{"function":'
                    b'{"name":"echo","arguments":{"text":"hi"}}}]}}')

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = _json.loads(req.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ollama_module.request, "urlopen", fake_urlopen)
    tools = [{"type": "function", "function": {"name": "echo", "description": "x",
                                                "parameters": {"type": "object", "properties": {}}}}]
    turn = OllamaProvider().chat_with_tools(
        "http://ollama:11434", "qwen3", [{"role": "user", "content": "Hallo"}], tools
    )

    assert captured["url"] == "http://ollama:11434/api/chat"
    assert captured["body"]["stream"] is False
    assert captured["body"]["tools"] == tools
    assert captured["body"]["options"] == {"temperature": 0}
    assert "format" not in captured["body"]
    assert turn.calls[0].name == "echo" and turn.calls[0].arguments == {"text": "hi"}


def test_chat_with_tools_maps_http_400_without_tool_support(monkeypatch):
    import io
    from urllib import error as urllib_error
    from guideos.providers import ollama as ollama_module
    from guideos.providers.base import ModelWithoutToolsError

    def fake_urlopen(req, timeout):
        raise urllib_error.HTTPError(
            req.full_url, 400, "Bad Request", hdrs=None,
            fp=io.BytesIO(b'{"error":"llama2 does not support tools"}'),
        )

    monkeypatch.setattr(ollama_module.request, "urlopen", fake_urlopen)
    with pytest.raises(ModelWithoutToolsError) as excinfo:
        OllamaProvider().chat_with_tools("http://ollama:11434", "llama2", [], [])
    assert excinfo.value.code == "model_without_tools"


def test_request_timeout_comes_from_the_environment(monkeypatch):
    """GUIDEOS_PROVIDER_TIMEOUT_SECONDS bounds every model request (default
    120 s); large models with tool results in the prompt need more."""
    from guideos.config import provider_timeout_seconds

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"message":{"content":"{}"}}'

    def fake_urlopen(chat_request, timeout):
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("guideos.providers.ollama.request.urlopen", fake_urlopen)
    monkeypatch.delenv("GUIDEOS_PROVIDER_TIMEOUT_SECONDS", raising=False)
    assert provider_timeout_seconds() == 120
    OllamaProvider().generate_structured("http://localhost:11434", "m", [{"role": "user", "content": "x"}], {"type": "object"})
    assert captured["timeout"] == 120

    monkeypatch.setenv("GUIDEOS_PROVIDER_TIMEOUT_SECONDS", "300")
    assert provider_timeout_seconds() == 300
    OllamaProvider().generate_structured("http://localhost:11434", "m", [{"role": "user", "content": "x"}], {"type": "object"})
    assert captured["timeout"] == 300

    monkeypatch.setenv("GUIDEOS_PROVIDER_TIMEOUT_SECONDS", "abc")
    assert provider_timeout_seconds() == 120
