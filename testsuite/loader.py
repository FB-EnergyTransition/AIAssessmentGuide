import hashlib
import json
from pathlib import Path

from testsuite.models import Scenario


SCENARIO_DIRECTORY = Path(__file__).with_name("scenarios")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load_scenarios(directory: Path = SCENARIO_DIRECTORY) -> list[Scenario]:
    scenarios = []
    seen = set()
    for path in sorted(directory.rglob("*.json")):
        scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        if scenario.id in seen:
            raise ValueError(f"Doppelte Szenario-ID: {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    if not scenarios:
        raise ValueError("Keine Szenarien gefunden.")
    return scenarios
