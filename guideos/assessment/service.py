from typing import Any

from pydantic import ValidationError

from guideos.assessment.contract import (
    AssessmentContractError,
    AssessmentContractResponse,
    parse_assessment_input,
)
from guideos.assessment.orchestrator import AssessmentOrchestrator


def handle_assessment_payload(
    orchestrator: AssessmentOrchestrator,
    payload: dict[str, Any],
) -> AssessmentContractResponse:
    try:
        assessment_input = parse_assessment_input(payload)
    except ValidationError as exc:
        return AssessmentContractResponse(
            ok=False,
            session_id=_string_or_none(payload.get("session_id")),
            assistant_message="Der Assessment-Request ist ungültig.",
            errors=[
                AssessmentContractError(
                    code="invalid_input",
                    message=str(exc),
                    question_id=_string_or_none(payload.get("question_id")),
                )
            ],
        )
    return orchestrator.handle_contract(assessment_input)


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None
