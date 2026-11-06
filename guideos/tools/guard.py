"""Guard rails around model-chosen tool calls: only allowed tools, arguments
checked against the input schema, owner set by GuideOS, outputs checked
against the output schema, and a budget of calls and seconds per message."""

import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from guideos.tools.models import OWNER_FIELD, ToolError, ToolSpec

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
_TYPE_LABELS = {"string": "Text", "integer": "Ganzzahl", "number": "Zahl", "boolean": "Ja/Nein",
                "array": "Liste", "object": "Objekt", "null": "null"}


def validate_against_schema(schema: dict[str, Any], value: Any, path: str = "Wert") -> list[str]:
    """German problem descriptions for a value against a JSON-schema subset:
    type, required, properties, additionalProperties, enum, const, bounds,
    lengths, items, anyOf/oneOf and the date format. Unknown keywords are
    ignored, so a schema never blocks a call for features this check does not
    know."""
    errors: list[str] = []
    for branches_key in ("anyOf", "oneOf"):
        branches = schema.get(branches_key)
        if isinstance(branches, list) and branches:
            branch_errors = [validate_against_schema(branch, value, path) for branch in branches]
            if any(not errors for errors in branch_errors):
                return []
            # Report the branch whose type fits (a nullable date names the date problem).
            fitting = [errors for branch, errors in zip(branches, branch_errors)
                       if not errors or not errors[0].endswith("erwartet.")
                       or branch.get("type") not in (None, "null")]
            fitting = [errors for errors in fitting if "null" not in errors[0]]
            if len(fitting) == 1:
                return fitting[0]
            return [f"{path}: passt zu keiner der erlaubten Formen."]
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_TYPE_CHECKS.get(item, lambda v: True)(value) for item in types):
            labels = " oder ".join(_TYPE_LABELS.get(item, item) for item in types)
            return [f"{path}: {labels} erwartet."]
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        errors.append(f"{path}: erlaubt sind nur {allowed}.")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: muss {schema['const']!r} sein.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: mindestens {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: maximal {schema['maximum']}.")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: größer als {schema['exclusiveMinimum']}.")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: kleiner als {schema['exclusiveMaximum']}.")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: mindestens {schema['minLength']} Zeichen.")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: höchstens {schema['maxLength']} Zeichen.")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: entspricht nicht dem erwarteten Muster.")
            except re.error:
                pass
        if schema.get("format") == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}: Datum im Format JJJJ-MM-TT erwartet.")
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate_against_schema(items, item, f"{path}[{index}]"))
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: mindestens {schema['minItems']} Einträge.")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: höchstens {schema['maxItems']} Einträge.")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{_join(path, name)}: erforderlich, aber nicht angegeben.")
        for name, item in value.items():
            if name in properties:
                errors.extend(validate_against_schema(properties[name], item, _join(path, name)))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{_join(path, name)}: nicht vorgesehen.")
    return errors


def _join(path: str, name: str) -> str:
    return name if path == "Wert" else f"{path}.{name}"


class ToolBudget:
    def __init__(self, max_calls: int = 6, max_seconds: float = 90.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self._clock = clock
        self.started = clock()
        self.calls_made = 0

    def check(self) -> None:
        if self.calls_made >= self.max_calls:
            raise ToolError("tool_budget_exceeded",
                            f"Mehr als {self.max_calls} Werkzeugaufrufe je Nachricht sind nicht erlaubt.",
                            {"max_calls": self.max_calls})
        elapsed = self._clock() - self.started
        if elapsed > self.max_seconds:
            raise ToolError("tool_budget_exceeded",
                            f"Die Werkzeugaufrufe haben länger als {self.max_seconds:g} Sekunden gedauert.",
                            {"max_seconds": self.max_seconds, "elapsed": round(elapsed, 1)})

    def record(self) -> None:
        self.calls_made += 1


class ToolGuard:
    def __init__(self, specs: list[ToolSpec], participant_id: str, max_calls: int = 6,
                 max_seconds: float = 90.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.specs = list(specs)
        self.participant_id = participant_id
        self.budget = ToolBudget(max_calls, max_seconds, clock)

    def spec_for(self, name: str) -> ToolSpec:
        for spec in self.specs:
            if spec.name == name:
                return spec
        raise ToolError("tool_not_allowed", f"Das Werkzeug {name} ist nicht freigegeben.", {"tool": name})

    def check_arguments(self, spec: ToolSpec, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolError("tool_arguments_invalid", f"Die Argumente für {spec.name} müssen ein Objekt sein.",
                            {"tool": spec.name})
        if OWNER_FIELD in arguments:
            raise ToolError("tool_arguments_invalid",
                            f"Das Feld {OWNER_FIELD} wird von GuideOS gesetzt und darf nicht übergeben werden.",
                            {"tool": spec.name, "errors": [f"{OWNER_FIELD}: nicht erlaubt."]})
        errors = validate_against_schema(spec.parameters_for_model(), arguments)
        if errors:
            raise ToolError("tool_arguments_invalid",
                            f"Die Argumente für {spec.name} sind ungültig: " + " ".join(errors),
                            {"tool": spec.name, "errors": errors})
        return dict(arguments)

    def with_owner(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {**arguments, OWNER_FIELD: self.participant_id}

    def check_output(self, spec: ToolSpec, structured_content: Any) -> None:
        if spec.output_schema is None:
            return
        if structured_content is None:
            raise ToolError("tool_output_invalid",
                            f"Das Werkzeug {spec.name} hat keine strukturierte Ausgabe geliefert.",
                            {"tool": spec.name})
        errors = validate_against_schema(spec.output_schema, structured_content)
        if errors:
            raise ToolError("tool_output_invalid",
                            f"Die Ausgabe des Werkzeugs {spec.name} entspricht nicht ihrem Schema: "
                            + " ".join(errors), {"tool": spec.name, "errors": errors})
