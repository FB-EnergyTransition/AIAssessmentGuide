import pytest


@pytest.fixture(autouse=True)
def isolated_local_storage(tmp_path, monkeypatch):
    """Keep adapter tests away from a developer's local knowledge base and
    profiles; the app reads both paths from the environment."""
    monkeypatch.setenv("GUIDEOS_KNOWLEDGE_PATH", str(tmp_path / "knowledge.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("GUIDEOS_TOOL_SERVERS_PATH", str(tmp_path / "mcp_servers.json"))
    monkeypatch.delenv("GUIDEOS_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("GUIDEOS_MODEL_SELECTION", raising=False)
