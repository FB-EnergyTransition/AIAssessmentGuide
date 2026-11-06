"""Render pilot sessions from PostgreSQL as Markdown reports for evaluation."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from guideos.assessment.models import AssessmentSession
from guideos.conversations.models import Conversation
from guideos.domains.catalog import DomainCatalogError, load_domain_pack_version
from guideos.domains.loader import DomainPackError
from guideos.domains.models import DomainPack, display_value


def render_session_report(
    session: AssessmentSession,
    conversations: list[Conversation],
    feedback: dict[str, Any] | None,
    *,
    pack: DomainPack | None,
) -> str:
    questions = {q.field_key: q for q in pack.questions} if pack else {}
    lines = [
        f"# Session {session.session_id}",
        "",
        f"- Teilnehmer: {session.participant_id or '-'}",
        f"- Domain: {session.domain_id} {session.domain_version}",
        f"- Status: {session.status}",
        f"- Erstellt: {session.created_at.isoformat()}",
        f"- Abgeschlossen: {session.completed_at.isoformat() if session.completed_at else '-'}",
        "",
        "## Angaben",
        "",
        "| Frage | Antwort | Rohtext | Confidence |",
        "|---|---|---|---|",
    ]
    ordered = [q.field_key for q in pack.questions] if pack else list(session.answers)
    for field_key in [*ordered, *[k for k in session.answers if k not in ordered]]:
        answer = session.answers.get(field_key)
        if answer is None:
            continue
        question = questions.get(field_key)
        label = question.text if question else field_key
        value = display_value(question, answer.normalized_value) if question else _plain(answer.normalized_value)
        lines.append(f"| {label} | {value} | {_cell(answer.raw_answer)} | {answer.confidence:.2f} |")

    lines += ["", "## Dialog", ""]
    if not session.chat_messages:
        lines.append("- keine Chat-Nachrichten")
    for message in session.chat_messages:
        who = "Nutzer" if message.role == "user" else "Assistent"
        suffix = f" ({message.question_id})" if message.question_id else ""
        lines.append(f"- **{who}**{suffix}: {_cell(message.content)}")
        if message.proposed_answer is not None:
            lines.append(
                f"  - Vorschlag: {_plain(message.proposed_answer.normalized_value)}"
                f" (Confidence {message.proposed_answer.confidence:.2f})"
            )

    lines += ["", "## Ereignisse", ""]
    for event in session.events:
        payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
        lines.append(f"- {event.created_at.isoformat()} {event.event_type} {payload}")

    lines += ["", "## Ergebnis", ""]
    if session.result is None:
        lines.append("- noch kein Ergebnis")
    else:
        lines.append(f"- Reason-Codes: {', '.join(session.result.reason_codes) or '-'}")
        for rec in session.result.recommendations:
            lines.append(f"- Empfehlung ({rec.source}, Priorität {rec.priority}): {rec.title}")

    lines += ["", "## Beratungschats", ""]
    if not conversations:
        lines.append("- keine Beratungschats")
    for conversation in conversations:
        lines += [f"### {conversation.title}", ""]
        for message in conversation.messages:
            who = "Nutzer" if message.role == "user" else "Assistent"
            lines.append(f"- **{who}**: {_cell(message.content)}")
        lines.append("")

    lines += ["## Feedback", ""]
    if feedback is None:
        lines.append("- kein Feedback")
    else:
        lines.append(f"- Bewertung: {feedback.get('rating')}/5")
        lines.append(f"- Kommentar: {_cell(feedback.get('comment')) or '-'}")
    return "\n".join(lines).rstrip() + "\n"


def _plain(value: Any) -> str:
    return "Unbekannt" if value is None else str(value)


def _cell(text: Any) -> str:
    return str(text or "").replace("\n", " ").replace("|", "\\|")


def _load_pack(session: AssessmentSession) -> DomainPack | None:
    try:
        return load_domain_pack_version(session.domain_id, session.domain_version)
    except (DomainCatalogError, DomainPackError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export GuideOS pilot sessions from PostgreSQL as Markdown reports."
    )
    parser.add_argument("--database-url", default=os.environ.get("GUIDEOS_DATABASE_URL"))
    parser.add_argument("--participant", help="Nur Sessions dieses Teilnehmer-Schlüssels")
    parser.add_argument("--output-dir", help="Eine Datei je Session statt Ausgabe auf stdout")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or GUIDEOS_DATABASE_URL is required")

    from guideos.assessment.postgres_repository import PostgresAssessmentRepository
    from guideos.conversations.postgres_repository import PostgresConversationRepository
    from guideos.pilot.feedback import PostgresPilotFeedbackRepository
    from guideos.profiles.postgres_repository import PostgresProfileRepository

    sessions = PostgresAssessmentRepository(args.database_url).list(args.participant)
    conversations = PostgresConversationRepository(args.database_url).list(args.participant)
    # Conversations hang on the profile; they belong to the session the profile currently stems from.
    profile_sources = {
        profile.profile_id: profile.source_session_id
        for profile in PostgresProfileRepository(args.database_url).list(args.participant)
    }
    feedback_rows = {
        row["session_id"]: dict(row)
        for row in PostgresPilotFeedbackRepository(args.database_url).export_rows()
    }
    reports = []
    for session in sessions:
        related = [
            c for c in conversations if profile_sources.get(c.profile_id) == session.session_id
        ]
        reports.append((session, render_session_report(
            session, related, feedback_rows.get(session.session_id), pack=_load_pack(session)
        )))
    if args.output_dir:
        directory = Path(args.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for session, report in reports:
            (directory / f"{session.session_id}.md").write_text(report, encoding="utf-8")
        print(f"{len(reports)} Berichte unter {directory}", file=sys.stderr)
    else:
        sys.stdout.write("\n---\n\n".join(report for _, report in reports))


if __name__ == "__main__":
    main()
