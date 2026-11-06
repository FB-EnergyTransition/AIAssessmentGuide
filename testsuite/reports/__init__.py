import html
import json

from testsuite.models import LLM_ACTIONS


def format_value(answer: dict) -> str:
    return (f"{json.dumps(answer.get('normalized_value'), ensure_ascii=False)}"
            f" (Confidence {answer.get('confidence')})")


def llm_text_lines(event: dict) -> list[str]:
    """Zeilen mit Eingabe und Modelltext eines LLM-Schritts für den Report."""
    request = event.get("request_payload") or {}
    if event.get("action") == "complete":
        # Nur ein Abschluss mit knowledge_recommendations: true ist ein Modellschritt.
        if request.get("knowledge_recommendations") is not True:
            return []
        result = ((event.get("response_payload") or {}).get("result") or {}) if isinstance(
            event.get("response_payload"), dict) else {}
        return [f"  - Empfehlung: {item.get('title')}: {item.get('rationale')}"
                for item in result.get("knowledge_recommendations") or []]
    if event.get("action") not in LLM_ACTIONS:
        return []
    lines = []
    if request.get("text"):
        lines.append(f"  - Eingabe: {request['text']}")
    response = event.get("response_payload")
    if not isinstance(response, dict):
        return lines
    if event["action"] == "submit_text_answer":
        answer = response.get("accepted_answer")
        if isinstance(answer, dict):
            lines.append(f"  - Normalisiert: {format_value(answer)}")
        else:
            lines.extend(f"  - Rückfrage ({issue.get('code')}): {issue.get('message')}"
                         for issue in response.get("validation_errors") or [])
    elif event["action"] == "question_help":
        # Die Antwort enthält den gesamten Chatverlauf; nur die neueste Assistant-Nachricht gehört zum Schritt.
        replies = [message for message in response.get("chat_messages") or []
                   if message.get("role") == "assistant" and message.get("scope") == "question_help"]
        if replies:
            lines.append(f"  - Antwort: {replies[-1].get('content')}")
            if isinstance(replies[-1].get("proposed_answer"), dict):
                lines.append(f"  - Vorschlag: {format_value(replies[-1]['proposed_answer'])}")
    elif event["action"] == "send_conversation_message":
        replies = [message for message in (response.get("conversation") or {}).get("messages") or []
                   if message.get("role") == "assistant"]
        if replies:
            lines.append(f"  - Antwort: {replies[-1].get('content')}")
    else:
        lines.extend(f"  - Empfehlung: {item.get('title')}: {item.get('rationale')}"
                     for item in response.get("recommendations") or [])
    return lines


ASSESSMENT_ACTIONS = {"start_assessment", "submit_answer", "submit_text_answer", "revise_answer", "complete",
                      "accept_proposed_answer", "discard_proposed_answer"}


def question_line(question: dict) -> str:
    labels = [option.get("label") for option in question.get("options") or []]
    return f"- GuideOS: {question.get('text')}" + (f" [{' | '.join(labels)}]" if labels else "")


def dialog_lines(event: dict) -> list[str]:
    """Chronologische Dialogzeilen eines Schritts: Nutzereingabe, Rückmeldung, Ergebnis."""
    action = event.get("action")
    request = event.get("request_payload") or {}
    response = event.get("response_payload")
    lines = []
    if action in ("submit_text_answer", "question_help", "send_conversation_message"):
        lines.append(f"- Nutzer: {request.get('text')}")
    elif action in ("submit_answer", "revise_answer"):
        lines.append(f"- Nutzer: {request.get('question_id')} = {json.dumps(request.get('value'), ensure_ascii=False)}")
    elif action == "complete":
        lines.append("- Nutzer: (Abschluss)")
    elif action == "llm_recommendations":
        lines.append("- Nutzer: (LLM-Empfehlungen angefordert)")
    if response is None:
        lines.append(f"- Fehler: {action} ohne Antwort ({event.get('error_code')})")
        return lines
    if not isinstance(response, dict):
        return lines
    lines.extend(f"- GuideOS: Fehler {error.get('code')}: {error.get('message')}"
                 for error in response.get("errors") or [])
    issues = [issue.get("message") for issue in response.get("validation_errors") or []]
    lines.extend(f"- GuideOS: {message}" for message in issues)
    if action == "question_help":
        replies = [message for message in response.get("chat_messages") or []
                   if message.get("role") == "assistant" and message.get("scope") == "question_help"]
        if replies:
            lines.append(f"- GuideOS: {replies[-1].get('content')}")
    elif action in ASSESSMENT_ACTIONS:
        question = response.get("current_question")
        message = response.get("assistant_message")
        if isinstance(question, dict) and question.get("text") == message:
            lines.append(question_line(question))
        elif message and message not in issues:
            lines.append(f"- GuideOS: {message}")
        result = response.get("result")
        if action == "complete" and isinstance(result, dict):
            summary = f"- Ergebnis: Reason-Codes {', '.join(result.get('reason_codes') or [])}"
            recommendations = [f"{item.get('recommendation_id')} ({item.get('source')})"
                               for item in result.get("recommendations") or []]
            if recommendations:
                summary += f", Empfehlungen {', '.join(recommendations)}"
            lines.append(summary)
    elif action == "create_conversation":
        title = (response.get("conversation") or {}).get("title") or request.get("title")
        lines.append(f"- Konversation \"{title}\" gestartet")
    elif action == "send_conversation_message":
        replies = [message for message in (response.get("conversation") or {}).get("messages") or []
                   if message.get("role") == "assistant"]
        if replies:
            lines.append(f"- GuideOS: {replies[-1].get('content')}")
    elif action == "llm_recommendations":
        lines.extend(f"- GuideOS: {item.get('title')}: {item.get('rationale')}"
                     for item in response.get("recommendations") or [])
    return lines


def dialog_section(events: list[dict]) -> list[str]:
    ordered = sorted(events, key=lambda event: event.get("sequence_no", 0))
    dialog = [line for event in ordered for line in dialog_lines(event)]
    if not dialog:
        return []
    lines = ["", "### Dialog", "", *dialog]
    reports = [(event.get("response_payload") or {}).get("report") for event in ordered
               if isinstance(event.get("response_payload"), dict)]
    reports = [report for report in reports if isinstance(report, dict) and report.get("content")]
    if reports:
        lines.extend(["", "### Assessment-Report", "", "```markdown", reports[-1]["content"].rstrip("\n"), "```"])
    return lines


def pass_rates(cases: list[dict]) -> dict[str, dict[str, int]]:
    rates: dict[str, dict[str, int]] = {}
    for case in cases:
        entry = rates.setdefault(case["scenario_id"], {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += case["status"] == "passed"
    return rates


def build_report(data: dict, format: str = "markdown") -> str:
    run = data["runs"][0]
    rates = pass_rates(data["cases"])
    lines = [f"# GuideOS Testlauf {run['id']}", "", f"Status: {run['status']}", ""]
    if any(entry["total"] > 1 for entry in rates.values()):
        lines.extend(["## Bestehensquote", ""])
        lines.extend(f"- {scenario_id}: {entry['passed']}/{entry['total']} bestanden"
                     for scenario_id, entry in rates.items() if entry["total"] > 1)
        lines.append("")
    for case in data["cases"]:
        title = case["scenario_id"]
        if rates[title]["total"] > 1:
            title += f" (Versuch {case.get('attempt', 1)})"
        lines.extend([f"## {title}: {case['status']}", ""])
        if case.get("error_code"):
            lines.append(f"Fehler: {case['error_code']} – {case.get('error_message', '')}")
        for assertion in data["assertions"]:
            if assertion.get("case_id") == case["id"] and not assertion["passed"]:
                lines.append(f"- {assertion.get('step_id', 'final')} {assertion['path']}: "
                             f"erwartet {json.dumps(assertion.get('expected'), ensure_ascii=False)}, "
                             f"erhalten {json.dumps(assertion.get('actual'), ensure_ascii=False)}")
        case_events = [event for event in data["events"] if event.get("case_id") == case["id"]]
        for event in case_events:
            lines.append(f"- {event['action']}: HTTP {event.get('http_status')} "
                         f"({event.get('duration_ms', 0):.1f} ms)")
            lines.extend(llm_text_lines(event))
        lines.extend(dialog_section(case_events))
        lines.append("")
    lines.extend(["## Metadaten", "", "```json", json.dumps(run.get("metadata", {}), ensure_ascii=False, indent=2), "```", ""])
    markdown = "\n".join(lines)
    if format == "markdown":
        return markdown
    if format != "html":
        raise ValueError("Unbekanntes Reportformat.")
    return ('<!doctype html><html lang="de"><meta charset="utf-8"><title>GuideOS Testlauf</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><body><pre>'
            + html.escape(markdown) + '</pre></body></html>')
