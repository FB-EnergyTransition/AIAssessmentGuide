"""Beratungstest: Auswertung der Laufbelege für die fachliche Bewertung und die Bewertungen selbst (ohne UI)."""
from __future__ import annotations
from testsuite.models import RECOMMENDATION_ACTIONS

import csv
import io
from datetime import datetime, timezone
from statistics import median
from uuid import uuid4


VERDICT_LABELS = {"gut": "Passt", "teils": "Teilweise", "schlecht": "Mangelhaft"}
KIND_LABELS = {"advisory": "Fachfrage", "edge": "Grenzfall", "llm_recommendations": "KI-Empfehlungen"}
SOURCE_LABELS = {"document": "Dokument", "web": "Web", "rule": "Regel", "user_context": "Angabe",
                 "llm": "KI-Fachwissen"}
RECOMMENDATIONS_TITLE = "Zusätzliche KI-Empfehlungen zum Ergebnis"
FEEDBACK_LABELS = {"accepted": "vom Nutzer befürwortet", "rejected": "vom Nutzer abgelehnt"}


def _events_by_case(data: dict) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for event in sorted(data["events"], key=lambda event: event.get("sequence_no", 0)):
        events.setdefault(event.get("case_id"), []).append(event)
    return events


def _questions(events: list[dict]) -> dict[str, dict]:
    """Fragen eines Falls nach field_key, so wie die API sie während des Assessments geliefert hat."""
    questions = {}
    for event in events:
        response = event.get("response_payload")
        question = response.get("current_question") if isinstance(response, dict) else None
        if isinstance(question, dict) and question.get("field_key"):
            questions.setdefault(question["field_key"], question)
    return questions


def _answer_sections(kind: str, payload: dict, questions: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Liefert die Abschnitte zur Anzeige und die Quellen, die GuideOS den Aussagen zugeordnet hat."""
    if kind != "llm_recommendations":
        messages = (payload.get("conversation") or {}).get("messages") or []
        answer = next((message for message in reversed(messages) if message.get("role") == "assistant"), None)
        if answer is None:
            return [], []
        sections = [{"text": section.get("text", ""), "sources": section.get("sources") or [],
                     "unverified_values": section.get("unverified_values") or []}
                    for section in answer.get("sections") or []]
        sections = sections or [{"text": answer.get("content", ""), "sources": [], "unverified_values": []}]
        return sections, [source for section in sections for source in section["sources"]]
    sections, attributed = [], []
    for item in payload.get("recommendations") or []:
        text = f"**{item.get('title', '')}**\n{item.get('message', '')}"
        if item.get("rationale"):
            text += f"\nBegründung: {item['rationale']}"
        fields = [{"kind": "user_context", "title": (questions.get(field) or {}).get("text") or field}
                  for field in item.get("evidence_fields") or []]
        sources = item.get("sources") or []
        sections.append({"text": text, "sources": fields + sources,
                         "unverified_values": item.get("unverified_values") or []})
        attributed.extend(sources)
    return sections, attributed


def review_cases(data: dict) -> list[dict]:
    """Je Fall mit review-Block: Frage, Prüfkriterium, Antwortabschnitte mit Quellen und Kennzahlen."""
    events = _events_by_case(data)
    result = []
    for case in data["cases"]:
        review = case.get("review")
        if not review:
            continue
        steps = events.get(case["id"], [])
        recommendations = review["kind"] == "llm_recommendations"
        actions = RECOMMENDATION_ACTIONS if recommendations else {"send_conversation_message"}
        event = next((step for step in reversed(steps) if step.get("action") in actions), None)
        payload = event.get("response_payload") if event else None
        sections, attributed, error = [], [], None
        if event is None:
            error = case.get("error_code") or "Schritt wurde nicht ausgeführt"
        elif not isinstance(payload, dict):
            error = event.get("error_code") or "keine Antwort"
        elif not payload.get("ok"):
            error = "; ".join(f"{item.get('code')}: {item.get('message')}"
                              for item in payload.get("errors") or []) or "Antwort ohne Erfolg"
        else:
            sections, attributed = _answer_sections(review["kind"], payload, _questions(steps))
        kinds = {source.get("kind", "llm") for source in attributed}
        history = [] if recommendations else _dialog_before_answer(payload)
        question = RECOMMENDATIONS_TITLE if recommendations else ((event or {}).get("request_payload") or {}).get("text")
        duration = (event or {}).get("duration_ms")
        result.append({
            "case_id": case["id"], "scenario_id": case["scenario_id"], "attempt": case.get("attempt", 1),
            "status": case.get("status"), "persona": review["persona"], "persona_label": review["persona_label"],
            "kind": review["kind"], "check": review["check"], "question": question or case["scenario_id"],
            "seconds": round(duration / 1000) if event and duration is not None else None, "error": error,
            "sections": sections, "source_kinds": [kind for kind in SOURCE_LABELS if kind in kinds],
            "unverified": sum(len(section["unverified_values"]) for section in sections), "history": history})
    return result


def _dialog_before_answer(payload) -> list[tuple[str, str]]:
    """Alle Nachrichten der Konversation vor der bewerteten Antwort (Rolle, Text), damit Änderungen,
    Rückfragen und Rückmeldungen früherer Schritte sichtbar sind; die Belege enthalten sie bereits."""
    if not isinstance(payload, dict):
        return []
    messages = (payload.get("conversation") or {}).get("messages") or []
    last = next((index for index in range(len(messages) - 1, -1, -1)
                 if messages[index].get("role") == "assistant"), None)
    return [(message.get("role", ""), message.get("content", "")) for message in messages[:last]]


def review_summary(cases: list[dict]) -> dict:
    answered = [case for case in cases if case["error"] is None]
    seconds = [case["seconds"] for case in cases if case["seconds"] is not None]
    return {"cases": len(cases), "answered": len(answered),
            "with_document": sum("document" in case["source_kinds"] for case in answered),
            "llm_only": sum(set(case["source_kinds"]) <= {"llm"} for case in answered),
            "with_unverified": sum(case["unverified"] > 0 for case in answered),
            "median_seconds": round(median(seconds)) if seconds else None,
            "max_seconds": max(seconds) if seconds else None}


def _display_value(question: dict, value) -> str:
    if value is None:
        return "unbekannt / übersprungen"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    labels = {option.get("value"): option.get("label") for option in question.get("options") or []}
    if isinstance(value, list):
        return ", ".join(str(labels.get(item, item)) for item in value)
    if isinstance(value, float):
        return str(value).replace(".", ",")
    return str(labels.get(value, value))


def _with_feedback(item: dict, feedback: dict) -> str:
    entry = feedback.get(item.get("recommendation_id")) if isinstance(feedback, dict) else None
    label = FEEDBACK_LABELS.get((entry or {}).get("stance"))
    return f"{item.get('title')} ({label})" if label else item.get("title")


def persona_profiles(data: dict) -> dict[str, dict]:
    """Je Persona die Angaben und das deterministische Ergebnis aus dem ersten abgeschlossenen Fall.

    Liefert der Fall später ein Profil (get_profile, Faktenänderung, Rückmeldung), zählt dessen
    letzter Stand (`current` True): Änderungen im Chat sind so sichtbar, statt die Angaben vom
    Abschluss zu zeigen, denen der Dialog danach widersprechen kann."""
    events = _events_by_case(data)
    profiles: dict[str, dict] = {}
    for case in data["cases"]:
        review = case.get("review")
        if not review:
            continue
        profile = profiles.setdefault(review["persona"], {"label": review["persona_label"], "reason_codes": [],
                                                         "recommendations": [], "answers": [], "current": False})
        if profile["answers"]:
            continue
        steps = events.get(case["id"], [])
        completed = next((step["response_payload"] for step in steps if step.get("action") == "complete"
                          and isinstance(step.get("response_payload"), dict)), None)
        latest = next((step["response_payload"]["profile"] for step in reversed(steps)
                       if isinstance(step.get("response_payload"), dict)
                       and isinstance(step["response_payload"].get("profile"), dict)
                       and isinstance(step["response_payload"]["profile"].get("answers"), dict)), None)
        state = latest or completed
        result = (state or {}).get("result")
        if not isinstance(result, dict):
            continue
        questions = _questions(steps)
        feedback = (latest or {}).get("feedback") or {}
        profile.update(
            current=latest is not None,
            reason_codes=result.get("reason_codes") or [],
            recommendations=[_with_feedback(item, feedback) for item in result.get("recommendations") or []],
            answers=[((questions.get(key) or {}).get("text") or key,
                      _display_value(questions.get(key) or {}, answer.get("normalized_value"))
                      + (" (im Chat geändert)" if answer.get("origin") == "chat_update" else ""))
                     for key, answer in (state.get("answers") or {}).items()])
    return profiles


def new_rating(run_id: str, case_id: str, reviewer: str, verdict: str | None, note: str) -> dict:
    """Eine Bewertung ist ein neuer Datensatz; frühere Bewertungen derselben Person bleiben als Verlauf erhalten."""
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("Bitte trage zuerst deinen Namen ein, damit die Bewertung zugeordnet werden kann.")
    if verdict is not None and verdict not in VERDICT_LABELS:
        raise ValueError("Unbekannte Bewertung.")
    return {"id": uuid4().hex, "run_id": run_id, "case_id": case_id, "reviewer": reviewer, "verdict": verdict,
            "note": note.strip(), "created_at": datetime.now(timezone.utc).isoformat()}


def current_ratings(ratings: list[dict]) -> dict[str, dict[str, dict]]:
    """Fall-ID -> Bewerter -> jüngste Bewertung."""
    current: dict[str, dict[str, dict]] = {}
    for rating in sorted(ratings, key=lambda rating: rating["created_at"]):
        current.setdefault(rating["case_id"], {})[rating["reviewer"]] = rating
    return current


def ratings_csv(cases: list[dict], ratings: list[dict]) -> str:
    """Alle gespeicherten Bewertungen samt Verlauf; `aktuell` markiert die jüngste je Fall und Bewerter."""
    current = {rating["id"] for by_reviewer in current_ratings(ratings).values() for rating in by_reviewer.values()}
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(["lauf", "szenario", "versuch", "persona", "art", "frage", "bewerter", "bewertung", "notiz",
                     "zeitpunkt", "aktuell"])
    for case in cases:
        for rating in sorted((r for r in ratings if r["case_id"] == case["case_id"]), key=lambda r: r["created_at"]):
            writer.writerow([rating["run_id"], case["scenario_id"], case["attempt"], case["persona"], case["kind"],
                             case["question"], rating["reviewer"], rating["verdict"] or "", rating["note"],
                             rating["created_at"], "ja" if rating["id"] in current else "nein"])
    return output.getvalue()
