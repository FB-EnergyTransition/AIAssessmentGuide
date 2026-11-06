from guideos.providers.base import LLMProvider
from guideos.providers.ollama import OllamaProvider


_PROVIDERS: dict[str, LLMProvider] = {
    OllamaProvider.name: OllamaProvider(),
}


def list_provider_names() -> list[str]:
    return list(_PROVIDERS)


def get_provider(name: str) -> LLMProvider:
    return _PROVIDERS[name]
