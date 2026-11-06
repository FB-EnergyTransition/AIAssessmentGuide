import ipaddress
import math
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def validate_target(base_url: str, manifest: dict | None, allowed_hosts: list[str], allow_production: bool) -> None:
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("API-Ziel muss eine HTTP(S)-Origin ohne Zugangsdaten sein.")
    if not manifest or manifest.get("base_url", "").rstrip("/") != base_url.rstrip("/"):
        raise ValueError("Ein zum API-Ziel passendes Testziel-Manifest ist erforderlich.")
    if manifest.get("environment") not in {"test", "production"}:
        raise ValueError("Das Zielmanifest muss environment=test oder production deklarieren.")
    if manifest["environment"] == "production" and not allow_production:
        raise ValueError("Produktionsziel erfordert --allow-production.")
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if not local and parsed.hostname not in allowed_hosts:
        raise ValueError("Nicht lokales API-Ziel erfordert --allow-host.")


class GuideOSClient:
    def __init__(self, base_url: str, *, connect_timeout: float = 5, read_timeout: float = 30,
                 llm_timeout: float = 150, transport=None):
        if any(not math.isfinite(value) or value <= 0 for value in (connect_timeout, read_timeout, llm_timeout)):
            raise ValueError("Timeouts müssen endlich und positiv sein.")
        self.read_timeout = read_timeout
        self.llm_timeout = llm_timeout
        self.connect_timeout = connect_timeout
        self.client = httpx.Client(base_url=base_url, follow_redirects=False, trust_env=False, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, method: str, path: str, payload=None, *, llm: bool = False, request_id: str | None = None,
                participant: str | None = None) -> dict:
        request_id = request_id or uuid4().hex
        headers = {"X-Request-ID": request_id}
        if participant:
            # Request scoping of the GuideOS API: every case acts as its own participant.
            headers["X-GuideOS-Participant"] = participant
        started = monotonic()
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(
                    method, path, json=payload, headers=headers,
                    timeout=httpx.Timeout(self.llm_timeout if llm else self.read_timeout,
                                          connect=self.connect_timeout),
                )
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt == attempts - 1:
                    raise
        try:
            body = response.json()
        except ValueError:
            body = {"non_json_body": response.text}
        return {"request_id": request_id, "method": method, "path": path,
                "request_payload": payload, "response_payload": body,
                "http_status": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000, 3), "attempts": attempt + 1}
