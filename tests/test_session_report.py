
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.models import Conversation, ConversationMessage
from guideos.domains.loader import load_domain_pack
from guideos.pilot.session_report import render_session_report


def _completed_session(tmp_path):
    pack = load_domain_pack()
    orchestrator = AssessmentOrchestrator(pack, LocalAssessmentRepository(tmp_path / "a.json"))
    response = orchestrator.start()
    for question_id, value in [
        ("club_type", "sports"), ("founded_year", 1980), ("members_count", 120),
        ("funding_source", "public_grants"), ("volunteer_situation", "tight"), ("annual_budget_eur", None),
    ]:
        orchestrator.submit(response.session_id, question_id, value)
    orchestrator.complete(response.session_id)
    session = orchestrator.repository.get(response.session_id)
    session.participant_id = "teilnehmer-a"
    return pack, session


def test_session_report_contains_answers_result_chats_and_feedback(tmp_path):
    pack, session = _completed_session(tmp_path)
    conversation = Conversation.create(
        domain_id=pack.domain_id, title="Ergebnis", participant_id="teilnehmer-a",
        profile_id="prof_1",
    )
    conversation.messages.append(ConversationMessage(role="user", content="Was empfiehlst du?"))
    conversation.messages.append(ConversationMessage(role="assistant", content="Zuerst das Ehrenamt sichern."))

    report = render_session_report(
        session, [conversation], {"rating": 4, "comment": "Verständlich"}, pack=pack
    )

    assert report.startswith(f"# Session {session.session_id}")
    assert "teilnehmer-a" in report and "verein-demo" in report and "completed" in report
    assert "| Welche Art von Verein ist es? | Sportverein |" in report
    assert "| Wie hoch sind die jährlichen Ausgaben des Vereins in Euro? | Unbekannt |" in report
    assert "Score" not in report and "MISSING_BUDGET" in report
    assert "## Beratungschats" in report and "Was empfiehlst du?" in report
    assert "Bewertung: 4/5" in report and "Verständlich" in report


def test_session_report_without_pack_falls_back_to_field_keys(tmp_path):
    _, session = _completed_session(tmp_path)

    report = render_session_report(session, [], None, pack=None)

    assert "| club_type | sports |" in report
    assert "## Feedback" in report and "kein Feedback" in report
    assert "## Beratungschats" in report and "keine Beratungschats" in report
