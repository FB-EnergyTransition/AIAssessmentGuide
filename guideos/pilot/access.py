from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Iterable


AUTHENTICATED_KEY = "pilot_authenticated"
PARTICIPANT_KEY = "pilot_participant_id"


def pilot_mode_enabled(value: str | None) -> bool:
    return value == "1"


def password_is_configured(password: str | None) -> bool:
    return bool(password)


def password_matches(provided: str, configured: str | None) -> bool:
    if not configured:
        return False
    return hmac.compare_digest(provided.encode(), configured.encode())


def authenticate(session_state, username: str | None, provided: str, configured: str | None) -> bool:
    """Pilot login with a user name and the shared test password. The stripped
    user name becomes the participant key, so the same name finds its sessions,
    profile and chats again after a new login (since 2026-10-22; before, every
    browser session got a random ``participant_<hex>``)."""
    participant = normalize_participant_name(username)
    if participant is None or not password_matches(provided, configured):
        return False
    session_state[AUTHENTICATED_KEY] = True
    session_state[PARTICIPANT_KEY] = participant
    return True


def participant_id(session_state) -> str | None:
    if not session_state.get(AUTHENTICATED_KEY):
        return None
    value = session_state.get(PARTICIPANT_KEY)
    return value if isinstance(value, str) else None


def logout(session_state) -> None:
    session_state.clear()


def local_participant_id() -> str:
    """Participant key for local processes without pilot login (PostgreSQL scoping)."""
    value = (os.environ.get("GUIDEOS_PARTICIPANT_ID") or "").strip()
    return value or "local"


def normalize_participant_name(value: str | None) -> str | None:
    """A user name typed in the sidebar as participant key: stripped, or None
    when nothing remains."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def participant_choices(known: Iterable[str | None], default: str) -> list[str]:
    """The user names offered in the sidebar: the default first, then every
    other known participant once in alphabetical order."""
    others = {name for name in known if isinstance(name, str) and name and name != default}
    return [default, *sorted(others)]


SESSION_COOKIE = "guideos_session"
SESSION_MAX_AGE = 24 * 3600


def sign_session(participant: str, key: str, issued_at: int | None = None) -> str:
    """A session token for the REST login: base64(JSON{p, t}) + "." + HMAC-SHA256.
    GuideOS signs it, the browser only carries it (HttpOnly cookie)."""
    payload = json.dumps({"p": participant, "t": int(issued_at if issued_at is not None else time.time())},
                         separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_signature(body, key)}"


def session_participant(token: str | None, key: str, now: float | None = None,
                        max_age: int = SESSION_MAX_AGE) -> str | None:
    """The participant of a valid, unexpired token; None for anything else."""
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    if not hmac.compare_digest(signature, _signature(body, key)):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        participant, issued = payload["p"], int(payload["t"])
    except (ValueError, KeyError, TypeError):
        return None
    current = now if now is not None else time.time()
    if not isinstance(participant, str) or not participant or current - issued > max_age or issued > current + 60:
        return None
    return participant


def _signature(body: str, key: str) -> str:
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


class LoginThrottle:
    """Locks a source (client address) for lock_seconds after max_failures
    failed logins within window_seconds; a successful login clears it. In
    memory per process, enough for the pilot behind the reverse proxy."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, lock_seconds: int = 300,
                 clock=time.monotonic) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self.clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def allowed(self, source: str) -> bool:
        with self._lock:
            until = self._locked_until.get(source)
            if until is not None and self.clock() < until:
                return False
            if until is not None:
                del self._locked_until[source]
                self._failures.pop(source, None)
            return True

    def record_failure(self, source: str) -> None:
        now = self.clock()
        with self._lock:
            recent = [stamp for stamp in self._failures.get(source, []) if now - stamp < self.window_seconds]
            recent.append(now)
            self._failures[source] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[source] = now + self.lock_seconds
                self._failures.pop(source, None)

    def record_success(self, source: str) -> None:
        with self._lock:
            self._failures.pop(source, None)
            self._locked_until.pop(source, None)
