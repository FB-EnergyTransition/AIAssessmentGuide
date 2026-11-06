from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx

from testsuite.redaction import redact


ROOT = Path(__file__).resolve().parents[1]


def git_metadata() -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True, check=True)
        return result.stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.CalledProcessError):
        build = ROOT / "testsuite-build.json"
        return json.loads(build.read_text()) if build.exists() else {"commit": None, "branch": None, "dirty": None}


def seed_knowledge(path: Path, directory: Path) -> dict[str, list[dict]]:
    """Index the documents under directory/<domain_id>/ into the local
    knowledge store at path, lexically (no embedding model), and return what
    was indexed per domain for the target manifest. The temporary API reads
    the same file through GUIDEOS_KNOWLEDGE_PATH."""
    from guideos.knowledge.service import KnowledgeService
    from guideos.knowledge.store import LocalKnowledgeStore

    if not directory.is_dir():
        raise ValueError(f"Wissensverzeichnis {directory} wurde nicht gefunden.")
    stray = [item.name for item in directory.iterdir() if item.is_file() and not item.name.startswith(".")]
    if stray:
        raise ValueError(
            "Dokumente gehören in ein Domain-Verzeichnis (<knowledge-dir>/<domain_id>/), "
            f"nicht direkt in {directory}: {', '.join(sorted(stray))}."
        )
    service = KnowledgeService(LocalKnowledgeStore(path))
    seeded: dict[str, list[dict]] = {}
    for domain_dir in sorted(item for item in directory.iterdir() if item.is_dir()):
        for file in sorted(item for item in domain_dir.iterdir() if item.is_file()):
            if file.name.startswith("."):
                continue
            document = service.add_document(domain_dir.name, file.name, file.read_bytes(), "")
            seeded.setdefault(domain_dir.name, []).append(
                {"title": document.title, "filename": file.name, "content_hash": document.content_hash}
            )
    return seeded


def domain_metadata() -> list[dict]:
    # Bootstrap metadata only; scenario execution always uses the public REST API.
    from guideos.domains.loader import load_domain_pack
    return [{"domain_id": pack.domain_id, "version": pack.version, "content_hash": pack.content_hash}
            for path in sorted((ROOT / "domain_packs").glob("*/*/pack.json"))
            for pack in [load_domain_pack(path)]]


def domains_with_tools() -> list[str]:
    """Domains whose latest bundled pack enables MCP tools (the isolated API
    has no catalog manifest, so the latest bundled version is active); they
    get the test run's tool server registered."""
    from guideos.domains.loader import load_domain_pack
    latest: dict[str, tuple[tuple[int, ...], object]] = {}
    for path in sorted((ROOT / "domain_packs").glob("*/*/pack.json")):
        pack = load_domain_pack(path)
        key = tuple(int(part) for part in pack.version.split("."))
        if pack.domain_id not in latest or key > latest[pack.domain_id][0]:
            latest[pack.domain_id] = (key, pack)
    return [domain_id for domain_id, (_, pack) in sorted(latest.items())
            if pack.tools is not None and pack.tools.enabled]


TOOL_SERVER_NAME = "testsuite"
TOOL_SERVER_DIRECTORY = ROOT / "testsuite" / "tool_servers"
TOOL_SERVER_CONFIG_KEYS = ("module", "token_env", "environment")


def tool_server_configs(domains: list[str] | None = None, directory: Path = TOOL_SERVER_DIRECTORY) -> dict[str, dict]:
    """How the test run starts a tool server for each domain with tools:
    testsuite/tool_servers/<domain_id>.json names the Python module that is
    run as `python -m <module> --host --port` (HTTP under /mcp, health under
    /health), the environment variable that carries its token, fixed
    environment variables, and optionally `in_process`
    ("package.module:function") for tests that start the server in the test
    process. A domain with tools but without such a file is an error."""
    configs: dict[str, dict] = {}
    for domain_id in (domains_with_tools() if domains is None else domains):
        path = directory / f"{domain_id}.json"
        if not path.is_file():
            raise RuntimeError(f"Für die Domäne {domain_id} mit Werkzeugen fehlt die Server-Konfiguration {path}.")
        config = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in TOOL_SERVER_CONFIG_KEYS if key not in config]
        if missing or not isinstance(config["environment"], dict):
            raise RuntimeError(f"Server-Konfiguration {path} braucht die Schlüssel {', '.join(missing or ['environment'])}.")
        configs[domain_id] = config
    return configs


def _environment_suffix(domain_id: str) -> str:
    return domain_id.upper().replace("-", "_")


def external_tool_servers(environment, domains: list[str]) -> dict[str, dict] | None:
    """Externally running tool servers (the Docker stack) from the
    environment: GUIDEOS_TESTSUITE_MCP_URL/_TOKEN apply to every domain with
    tools, GUIDEOS_TESTSUITE_MCP_URL__<DOMAIN_ID>/_TOKEN__<DOMAIN_ID> (upper
    case, "-" as "_") to one domain and win over the shared value. None when
    nothing is configured."""
    servers: dict[str, dict] = {}
    for domain_id in domains:
        suffix = _environment_suffix(domain_id)
        url = environment.get(f"GUIDEOS_TESTSUITE_MCP_URL__{suffix}") or environment.get("GUIDEOS_TESTSUITE_MCP_URL")
        if not url:
            continue
        token = (environment.get(f"GUIDEOS_TESTSUITE_MCP_TOKEN__{suffix}")
                 if environment.get(f"GUIDEOS_TESTSUITE_MCP_URL__{suffix}")
                 else environment.get("GUIDEOS_TESTSUITE_MCP_TOKEN"))
        servers[domain_id] = {"url": url, "token": token or ""}
    return servers or None


def register_tool_servers(path: Path, servers: dict[str, dict]) -> dict[str, list[str]]:
    """Register each domain's tool server ({domain_id: {url, token}}) in the
    local tool store the isolated API reads, fetch its tools and allow them
    all. Returns the allowed tool names per domain for the manifest."""
    from guideos.tools.client import ToolClient
    from guideos.tools.service import ToolService
    from guideos.tools.store import LocalToolServerStore

    service = ToolService(LocalToolServerStore(path), ToolClient())
    allowed: dict[str, list[str]] = {}
    for domain_id, server in servers.items():
        service.register(domain_id, TOOL_SERVER_NAME, server["url"], server["token"])
        registered = service.refresh_tools(domain_id, TOOL_SERVER_NAME)
        names = [spec.name for spec in registered.tool_specs]
        service.set_allowed_tools(domain_id, TOOL_SERVER_NAME, names)
        allowed[domain_id] = names
    return allowed


def _stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@contextmanager
def managed_tool_servers(environment: dict, directory: str, configs: dict[str, dict]):
    """The tool servers of the configured domains as subprocesses, one per
    distinct module (domains sharing a module share the server), for runs
    without a Docker stack. Yields {domain_id: {url, token}}."""
    processes: list[subprocess.Popen] = []
    servers: dict[str, dict] = {}
    by_module: dict[str, dict] = {}
    try:
        for domain_id, config in configs.items():
            if config["module"] not in by_module:
                token = f"testsuite-{uuid4().hex}"
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                server_environment = {**environment, **config["environment"], config["token_env"]: token}
                process = subprocess.Popen(
                    [sys.executable, "-m", config["module"], "--host", "127.0.0.1", "--port", str(port)],
                    cwd=directory, env=server_environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                processes.append(process)
                _wait_for_health(process, f"http://127.0.0.1:{port}/health", config["module"])
                by_module[config["module"]] = {"url": f"http://127.0.0.1:{port}/mcp", "token": token}
            servers[domain_id] = by_module[config["module"]]
        yield servers
    finally:
        for process in processes:
            _stop(process)


def _wait_for_health(process: subprocess.Popen, url: str, module: str) -> None:
    deadline = monotonic() + 30
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"Der Werkzeug-Server {module} konnte nicht gestartet werden.")
            try:
                if client.get(url).json().get("status") == "ok":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if monotonic() >= deadline:
                raise RuntimeError(f"Zeitlimit beim Start des Werkzeug-Servers {module}.")
            sleep(0.05)


@contextmanager
def managed_api(*, ollama_endpoint=None, ollama_model=None, bind_host="127.0.0.1", port=0, public_url=None,
                knowledge_dir=None, tool_servers=None, tool_server=None):
    """The isolated API with its own JSON stores. tool_servers: None starts
    the configured tool servers (testsuite/tool_servers/, nothing when no
    bundled pack enables tools) as subprocesses and registers them for their
    domains; a dict {domain_id: {url, token}} registers external servers (the
    Docker stack); False registers nothing. tool_server is the older form of
    one external server {url, token} for every domain with tools."""
    if tool_server is not None and tool_servers is None:
        tool_servers = {domain_id: tool_server for domain_id in domains_with_tools()} if tool_server else False
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GUIDEOS_")}
    environment.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
                       GUIDEOS_OLLAMA_ENDPOINT=ollama_endpoint or "http://127.0.0.1:9",
                       GUIDEOS_OLLAMA_MODEL=ollama_model or "gpt-oss:20b")
    with tempfile.TemporaryDirectory(prefix="guideos-testsuite-") as directory, ExitStack() as stack:
        environment["GUIDEOS_ASSESSMENT_PATH"] = str(Path(directory) / "assessments.json")
        environment["GUIDEOS_CONVERSATION_PATH"] = str(Path(directory) / "conversations.json")
        environment["GUIDEOS_KNOWLEDGE_PATH"] = str(Path(directory) / "knowledge.json")
        environment["GUIDEOS_PROFILE_PATH"] = str(Path(directory) / "profiles.json")
        environment["GUIDEOS_TOOL_SERVERS_PATH"] = str(Path(directory) / "mcp_servers.json")
        knowledge = seed_knowledge(Path(directory) / "knowledge.json", Path(knowledge_dir)) if knowledge_dir else {}
        tools: dict = {}
        if tool_servers is None:
            configs = tool_server_configs()
            tool_servers = stack.enter_context(managed_tool_servers(environment, directory, configs)) if configs else {}
        if tool_servers:
            allowed = register_tool_servers(Path(directory) / "mcp_servers.json", tool_servers)
            tools = {"servers": {domain_id: server["url"] for domain_id, server in tool_servers.items()},
                     "allowed": allowed}
        with socket.socket() as listener:
            listener.bind((bind_host, port))
            listener.listen()
            local_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            base_url = public_url or local_url
            manifest = {"environment": "test", "base_url": base_url, "source": "managed",
                        "git": git_metadata(), "domain_packs": domain_metadata(),
                        "provider": {"name": "ollama", "endpoint": environment["GUIDEOS_OLLAMA_ENDPOINT"],
                                     "model": environment["GUIDEOS_OLLAMA_MODEL"]},
                        "configuration": {"storage": "isolated_json", "pilot_mode": False,
                                          "knowledge": knowledge, "tools": tools}}
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "guideos.adapters.fastapi:create_app", "--factory",
                 "--fd", str(listener.fileno()), "--no-access-log", "--log-level", "error"],
                cwd=directory, env=environment, pass_fds=(listener.fileno(),),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                deadline = monotonic() + 20
                with httpx.Client(trust_env=False, timeout=0.5) as client:
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Die isolierte API konnte nicht gestartet werden.")
                        try:
                            if client.get(local_url + "/api/v1/health").json() == {"ok": True}:
                                break
                        except (httpx.HTTPError, ValueError):
                            pass
                        if monotonic() >= deadline:
                            raise RuntimeError("Zeitlimit beim Start der isolierten API.")
                        sleep(0.05)
                yield {"base_url": base_url, "manifest": manifest, "working_directory": directory, "process": process}
            finally:
                _stop(process)


def serve():
    """Container API entrypoint; publish provenance before becoming healthy."""
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    manifest_path = Path(os.environ.get("GUIDEOS_TESTSUITE_MANIFEST", "/manifest/target.json"))
    try:
        external = external_tool_servers(os.environ, domains_with_tools())
        with managed_api(ollama_endpoint=os.environ.get("GUIDEOS_OLLAMA_ENDPOINT"),
                         ollama_model=os.environ.get("GUIDEOS_OLLAMA_MODEL"), bind_host="0.0.0.0", port=8000,
                         public_url="http://api:8000", tool_servers=external) as target:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = manifest_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(redact(target["manifest"], persist_raw=True)), encoding="utf-8")
            temporary.replace(manifest_path)
            target["process"].wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve()
