import re
from urllib.parse import urlsplit, urlunsplit


SECRET_KEY = re.compile(r"password|secret|token|authorization|cookie|api[_-]?key|credential|database_url", re.I)
RAW_KEYS = {"raw_answer", "text", "content", "assistant_message", "message", "prompt", "follow_up", "non_json_body", "error_message", "excerpt"}
URL = re.compile(r"(?:https?|postgres(?:ql)?)://[^\s\"<>]+")


def _clean_url(match):
    try:
        url = urlsplit(match.group())
        host = url.netloc.rsplit("@", 1)[-1]
        return urlunsplit((url.scheme, host, url.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"


def _redact_strings(value):
    if isinstance(value, str):
        return "[REDACTED]"
    if isinstance(value, list):
        return [_redact_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    return value


def redact(value, *, persist_raw: bool = False, secrets=()):
    """Apply before every sink, including errors, snapshots and report artifacts."""
    if isinstance(value, dict):
        if (not persist_raw and "path" in value and "op" in value
                and any(part in RAW_KEYS for part in value["path"].split("/"))):
            value = dict(value)
            for field in ("expected", "actual"):
                if field in value:
                    value[field] = _redact_strings(value[field])
        return {key: "[REDACTED]" if SECRET_KEY.search(key) or (not persist_raw and key in RAW_KEYS)
                else redact(item, persist_raw=persist_raw, secrets=secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, persist_raw=persist_raw, secrets=secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = URL.sub(_clean_url, value)
        value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)\b(password|token|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    return value
