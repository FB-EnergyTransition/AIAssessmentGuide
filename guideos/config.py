import logging
import os


DEFAULT_PROVIDER = "ollama"
DEFAULT_OLLAMA_ENDPOINT = os.environ.get(
    "GUIDEOS_OLLAMA_ENDPOINT", "http://localhost:11434"
)
DEFAULT_OLLAMA_MODEL = os.environ.get("GUIDEOS_OLLAMA_MODEL", "gpt-oss:20b")
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 120


def provider_timeout_seconds() -> int:
    """Seconds a single model request may take (GUIDEOS_PROVIDER_TIMEOUT_SECONDS,
    default 120; read per call so operators can change it without a code
    change). Large models with tool results in the prompt need several minutes."""
    raw = os.environ.get("GUIDEOS_PROVIDER_TIMEOUT_SECONDS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PROVIDER_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_PROVIDER_TIMEOUT_SECONDS
DATABASE_URL = os.environ.get("GUIDEOS_DATABASE_URL")
PILOT_MODE = os.environ.get("GUIDEOS_PILOT_MODE") == "1"
TEST_PASSWORD = os.environ.get("GUIDEOS_TEST_PASSWORD")


def allowed_domains_from_env(value: str | None = None) -> set[str] | None:
    """The domain ids the assessment offers, from GUIDEOS_DOMAINS (comma
    separated); None means every active domain. Packs of other domains stay
    on disk so stored sessions keep loading."""
    raw = value if value is not None else os.environ.get("GUIDEOS_DOMAINS")
    if raw is None:
        return None
    domains = {part.strip() for part in raw.split(",") if part.strip()}
    return domains or None


def configure_logging(level: str | None = None) -> None:
    """Richtet das Logging für einen GuideOS-Prozess einmalig ein.

    Die Stufe kommt aus `level`, sonst aus `GUIDEOS_LOG_LEVEL` (Standard INFO).
    Meldungen sind `ereignis schlüssel=wert ...`; Antwortwerte und Chatinhalte
    werden nie protokolliert.
    """
    resolved = (level or os.environ.get("GUIDEOS_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    if not any(handler.name == "guideos" for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.set_name("guideos")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(handler)
    logging.getLogger("guideos").setLevel(resolved)
