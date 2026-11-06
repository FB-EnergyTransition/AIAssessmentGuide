import pytest

from guideos.providers.registry import get_provider, list_provider_names


def test_list_provider_names_includes_ollama():
    assert list_provider_names() == ["ollama"]


def test_get_provider_returns_ollama_provider():
    provider = get_provider("ollama")

    assert provider.name == "ollama"
    assert provider.display_name == "Ollama"


def test_get_provider_rejects_unknown_provider():
    with pytest.raises(KeyError):
        get_provider("unknown")


def test_default_ollama_endpoint_is_local_without_environment(monkeypatch):
    import importlib

    import guideos.config as config

    monkeypatch.delenv("GUIDEOS_OLLAMA_ENDPOINT", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.DEFAULT_OLLAMA_ENDPOINT == "http://localhost:11434"
    finally:
        importlib.reload(config)
