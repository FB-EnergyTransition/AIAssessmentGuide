from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

from testsuite import __version__
from testsuite.evaluator import evaluate, resolve_pointer
from testsuite.loader import content_hash
from testsuite.models import Assertion, Scenario
from testsuite.redaction import redact
from testsuite.repository import RunRepository


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_exit_code(run: dict) -> int:
    return {"passed": 0, "failed": 1, "error": 2, "cancelled": 130}[run["status"]]


def rerun_scenarios(data: dict) -> list[Scenario]:
    scenarios = []
    seen = set()
    for case in data["cases"]:
        snapshot = case["scenario_snapshot"]
        if content_hash(snapshot) != case["scenario_hash"]:
            raise ValueError("Snapshot wurde redigiert oder verändert; exakte Wiederholung nicht möglich.")
        if case["scenario_id"] in seen:
            continue
        seen.add(case["scenario_id"])
        scenarios.append(Scenario.model_validate(snapshot))
    if not scenarios:
        raise ValueError("Keine Szenarien zur Wiederholung vorhanden.")
    return scenarios


CONVERSATION_ACTIONS = {"get_conversation", "send_conversation_message", "delete_conversation"}


DATASET_FIXTURE_DIRECTORY = Path(__file__).with_name("fixtures") / "datasets"


def read_dataset_fixture(name: str) -> str:
    """The text of a measurement fixture by file name (no paths), UTF-8 with
    BOM removed or ISO-8859-1 like older spreadsheet exports."""
    if Path(name).name != name or name.startswith("."):
        raise ValueError("fixture muss ein Dateiname ohne Pfad sein.")
    path = DATASET_FIXTURE_DIRECTORY / name
    if not path.is_file():
        raise ValueError(f"Messdaten-Fixture {name} wurde nicht gefunden.")
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("iso-8859-1")


def action_request(scenario, step, session_id, responses, conversation_id=None):
    payload = dict(step.payload)
    for field, reference in step.references.items():
        source, pointer = reference.split("#", 1)
        payload[field] = resolve_pointer(responses[source], pointer)
    if step.action == "start_assessment":
        return "POST", "/api/v1/assessment-sessions", {"domain_id": scenario.domain_id, **payload}
    if step.action == "list_conversations":
        return "GET", "/api/v1/conversations", None
    if step.action == "get_profile":
        return "GET", f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}", None
    if step.action == "recommendation_feedback":
        recommendation = quote(str(payload.pop("recommendation_id")), safe="")
        return ("POST",
                f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}/recommendations/{recommendation}/feedback",
                payload)
    if step.action == "update_profile_fact":
        return "POST", f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}/facts", payload
    if step.action == "profile_recommendations":
        return "POST", f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}/knowledge-recommendations", None
    if step.action in ("get_dataset", "delete_dataset"):
        method = "GET" if step.action == "get_dataset" else "DELETE"
        return method, f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}/dataset", None
    if step.action == "upload_dataset":
        fixture = str(payload.pop("fixture"))
        return ("POST", f"/api/v1/profiles/{quote(scenario.domain_id, safe='')}/dataset",
                {"filename": fixture, "content": read_dataset_fixture(fixture), **payload})
    if step.action in CONVERSATION_ACTIONS:
        target = payload.pop("conversation_id", None) or conversation_id
        if not isinstance(target, str) or not target:
            raise ValueError("Die API hat keine Konversations-ID geliefert.")
        conversation = f"/api/v1/conversations/{quote(target, safe='')}"
        if step.action == "get_conversation":
            return "GET", conversation, None
        if step.action == "delete_conversation":
            return "DELETE", conversation, None
        return "POST", f"{conversation}/messages", payload
    if not session_id:
        raise ValueError("Die API hat keine Session-ID geliefert.")
    base = f"/api/v1/assessment-sessions/{quote(session_id, safe='')}"
    if step.action == "get_session":
        return "GET", base, None
    if step.action == "get_report":
        return "GET", f"{base}/report", None
    if step.action == "llm_recommendations":
        return "POST", f"{base}/llm-recommendations", None
    if step.action == "create_conversation":
        return "POST", "/api/v1/conversations", {"domain_id": scenario.domain_id, **payload}
    if step.action == "question_help":
        return "POST", f"{base}/chat", {"scope": "question_help", **payload}
    if step.action == "submit_file_answer":
        fixture = str(payload.pop("fixture"))
        value = {"filename": fixture, "content": read_dataset_fixture(fixture),
                 "format_hint": payload.pop("format_hint", "auto")}
        return "POST", f"{base}/inputs", {"kind": "submit_answer", "value": value, **payload}
    return "POST", f"{base}/inputs", {"kind": step.action, **payload}


def execute_run(scenarios: list[Scenario], client, repository: RunRepository, *, include_llm=False,
                metadata=None, parent_run_id=None, secrets=(), repeat=1) -> dict:
    if not scenarios or len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("Szenarioauswahl ist leer oder enthält doppelte IDs.")
    if not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat muss mindestens 1 sein.")
    plan = [(scenario, attempt) for scenario in scenarios for attempt in range(1, repeat + 1)]
    run_id = uuid4().hex
    run = {"id": run_id, "run_id": run_id, "status": "running", "started_at": now(),
           "finished_at": None, "runner_version": __version__, "parent_run_id": parent_run_id,
           "review_cases": sum(scenario.review is not None for scenario, _ in plan),
           "metadata": metadata or {}}
    def save(kind, record, raw=True):
        repository.write(kind, redact(record, persist_raw=raw, secrets=secrets))
    save("runs", run)
    cases = []
    for index, (scenario, attempt) in enumerate(plan):
        snapshot = scenario.model_dump(mode="json", exclude_unset=True)
        case_id = uuid4().hex
        case = {"id": case_id, "run_id": run_id, "scenario_id": scenario.id, "attempt": attempt,
                "participant_id": case_id,
                "sequence_no": index, "scenario_version": scenario.version, "scenario_hash": content_hash(snapshot),
                "scenario_snapshot": snapshot, "status": "running", "session_id": None,
                "started_at": None, "finished_at": None}
        if scenario.review:
            # The review view reads this copy; the snapshot stays the hashed scenario definition.
            case["review"] = scenario.review.model_dump()
        cases.append(case)
        save("cases", case, scenario.persist_raw)
    scenarios = [scenario for scenario, _ in plan]
    try:
        for scenario, case in zip(scenarios, cases):
            case["started_at"] = now()
            if scenario.requires_llm and not include_llm:
                case.update(status="skipped", error_code="llm_disabled", finished_at=now())
                save("cases", case, scenario.persist_raw)
                continue
            save("cases", case, scenario.persist_raw)
            responses = {}
            conversation_id = None
            sequence = 0

            def request(action, method, path, payload=None, llm=False):
                nonlocal sequence
                sequence += 1
                event = {"id": uuid4().hex, "run_id": run_id, "case_id": case["id"],
                         "sequence_no": sequence, "action": action, "created_at": now(), "request_id": uuid4().hex,
                         "method": method, "path": path, "request_payload": payload,
                         "response_payload": None, "http_status": None, "duration_ms": 0}
                save("events", event, scenario.persist_raw)
                started = monotonic()
                try:
                    event.update(client.request(method, path, payload, llm=llm, request_id=event["request_id"],
                                                participant=case["participant_id"]))
                except BaseException as exc:
                    event.update(duration_ms=(monotonic() - started) * 1000, error_code=type(exc).__name__)
                    save("events", event, scenario.persist_raw)
                    raise
                save("events", event, scenario.persist_raw)
                return event

            def assertions(checks, body, step_id):
                passed = True
                for check in checks:
                    result = {"id": uuid4().hex, "run_id": run_id, "case_id": case["id"],
                              "step_id": step_id, "created_at": now(), **evaluate(check, body)}
                    save("assertions", result, scenario.persist_raw)
                    passed = passed and result["passed"]
                return passed

            try:
                domains = request("domains", "GET", "/api/v1/domains")
                if domains["http_status"] != 200 or not isinstance(domains["response_payload"], list):
                    raise ValueError("Domain-Abfrage ist fehlgeschlagen.")
                matching = any(d.get("domain_id") == scenario.domain_id and d.get("version") == scenario.domain_version
                               for d in domains["response_payload"] if isinstance(d, dict))
                if not matching:
                    raise ValueError("Erwartete Domain-Version ist nicht aktiv.")
                body = None
                for step in scenario.steps:
                    method, path, payload = action_request(scenario, step, case["session_id"], responses,
                                                           conversation_id)
                    event = request(step.action, method, path, payload, step.uses_llm)
                    body = event["response_payload"]
                    responses[step.id] = body
                    checks = [Assertion(path="/http_status", op="eq", expected=step.expected_http)]
                    passed = assertions(checks, event, step.id)
                    checks = list(step.assertions)
                    if step.expected_ok is not None:
                        checks.insert(0, Assertion(path="/ok", op="eq", expected=step.expected_ok))
                    passed = assertions(checks, body, step.id) and passed
                    if step.action == "start_assessment" and isinstance(body, dict):
                        session_id = body.get("session_id")
                        if isinstance(session_id, str) and session_id:
                            case["session_id"] = session_id
                            save("cases", case, scenario.persist_raw)
                    if step.action == "create_conversation" and isinstance(body, dict):
                        created = (body.get("conversation") or {}).get("conversation_id")
                        if isinstance(created, str) and created:
                            conversation_id = created
                    if not passed:
                        case["status"] = "error" if event["http_status"] >= 500 else "failed"
                        break
                if case["status"] == "running":
                    case["status"] = "passed" if assertions(scenario.assertions, body, "final") else "failed"
            except (httpx.HTTPError, ValueError, LookupError, TypeError) as exc:
                case.update(status="error", error_code=type(exc).__name__, error_message=str(exc))
            case["finished_at"] = now()
            save("cases", case, scenario.persist_raw)
    except KeyboardInterrupt:
        for scenario, case in zip(scenarios, cases):
            if not case["finished_at"]:
                case.update(status="cancelled", finished_at=now())
                save("cases", case, scenario.persist_raw)
        run["status"] = "cancelled"
    except Exception:
        for scenario, case in zip(scenarios, cases):
            if not case["finished_at"]:
                case.update(status="error", finished_at=now(), error_code="runner_error")
                save("cases", case, scenario.persist_raw)
        # Preserve a terminal run if a sink or unexpected programming error interrupts execution.
        run.update(status="error", finished_at=now())
        save("runs", run)
        raise
    if run["status"] != "cancelled":
        states = {case["status"] for case in cases}
        run["status"] = "error" if "error" in states else "failed" if "failed" in states else "passed"
    run["finished_at"] = now()
    save("runs", run)
    return run


def main(argv=None):
    from testsuite.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
