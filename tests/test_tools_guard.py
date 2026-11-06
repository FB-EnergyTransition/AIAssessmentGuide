"""guideos.tools.guard: allowlist, argument check against input_schema, owner
injection, output check and the budget per message."""

import pytest

from guideos.tools.guard import ToolBudget, ToolGuard, validate_against_schema
from guideos.tools.models import ToolError, ToolSpec

SPEC = ToolSpec(
    name="analyze_dataset", description="Spitzen", server_name="demo",
    input_schema={
        "type": "object", "additionalProperties": False,
        "properties": {
            "owner": {"type": "string"},
            "dataset_id": {"type": "string", "minLength": 1},
            "period_from": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}],
                            "default": None},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["owner", "dataset_id"],
    },
    output_schema={"type": "object", "properties": {"months": {"type": "array"},
                                                    "dataset_id": {"type": "string"}},
                   "required": ["months"]},
)
OTHER = ToolSpec(name="delete_dataset", description="Löscht", server_name="demo",
                 input_schema={"type": "object", "properties": {"owner": {"type": "string"}},
                               "required": ["owner"]})


def test_validate_against_schema_reports_each_problem():
    schema = SPEC.input_schema
    assert validate_against_schema(schema, {"owner": "p", "dataset_id": "ds_1"}) == []
    errors = validate_against_schema(schema, {
        "owner": "p", "limit": 11, "mode": "c", "tags": ["x", 1], "unknown": 1, "period_from": "2025-13-01",
    })
    joined = " ".join(errors)
    assert "dataset_id" in joined and "erforderlich" in joined
    assert "limit" in joined and "maximal 10" in joined
    assert "mode" in joined and "a, b" in joined
    assert "tags[1]" in joined
    assert "unknown" in joined and "nicht vorgesehen" in joined
    assert "period_from" in joined and "Datum" in joined
    assert validate_against_schema(schema, {"owner": "p", "dataset_id": "ds", "period_from": None}) == []
    assert validate_against_schema(schema, {"owner": "p", "dataset_id": "ds", "period_from": "2025-01-10"}) == []
    assert validate_against_schema({"type": "integer"}, True) == ["Wert: Ganzzahl erwartet."]
    assert validate_against_schema({"type": "number"}, 2) == []
    assert validate_against_schema({"type": "string", "minLength": 2}, "a") != []


def test_guard_allows_only_listed_tools_and_valid_arguments():
    guard = ToolGuard([SPEC], participant_id="teilnehmer-1")

    spec = guard.spec_for("analyze_dataset")
    assert spec is SPEC
    with pytest.raises(ToolError) as excinfo:
        guard.spec_for("delete_dataset")
    assert excinfo.value.code == "tool_not_allowed"

    arguments = guard.check_arguments(spec, {"dataset_id": "ds_1", "limit": 3})
    assert arguments == {"dataset_id": "ds_1", "limit": 3}
    with pytest.raises(ToolError) as excinfo:
        guard.check_arguments(spec, {"dataset_id": "ds_1", "owner": "jemand"})
    assert excinfo.value.code == "tool_arguments_invalid" and "owner" in excinfo.value.message
    with pytest.raises(ToolError) as excinfo:
        guard.check_arguments(spec, {"limit": 0})
    assert excinfo.value.code == "tool_arguments_invalid"
    assert excinfo.value.details["errors"]
    with pytest.raises(ToolError):
        guard.check_arguments(spec, "keine Argumente")

    assert guard.with_owner({"dataset_id": "ds_1"}) == {"dataset_id": "ds_1", "owner": "teilnehmer-1"}


def test_guard_checks_outputs_against_the_output_schema():
    guard = ToolGuard([SPEC, OTHER], participant_id="p")
    guard.check_output(SPEC, {"months": [], "dataset_id": "ds_1"})
    with pytest.raises(ToolError) as excinfo:
        guard.check_output(SPEC, {"dataset_id": "ds_1"})
    assert excinfo.value.code == "tool_output_invalid"
    guard.check_output(OTHER, {"anything": 1})  # no output schema: nothing to check
    with pytest.raises(ToolError):
        guard.check_output(SPEC, None)


def test_budget_limits_calls_and_time():
    clock = [100.0]
    budget = ToolBudget(max_calls=2, max_seconds=10.0, clock=lambda: clock[0])
    budget.check()
    budget.record()
    budget.record()
    with pytest.raises(ToolError) as excinfo:
        budget.check()
    assert excinfo.value.code == "tool_budget_exceeded" and "Werkzeugaufrufe" in excinfo.value.message

    budget = ToolBudget(max_calls=5, max_seconds=10.0, clock=lambda: clock[0])
    clock[0] = 111.0
    with pytest.raises(ToolError) as excinfo:
        budget.check()
    assert "Sekunden" in excinfo.value.message
    assert budget.calls_made == 0
