import argparse
import csv
import io

import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from testsuite.cli import cleanup
from testsuite.models import Scenario
from testsuite.repository import JsonlRepository
from testsuite.review import (current_ratings, new_rating, persona_profiles, ratings_csv, review_cases,
                              review_summary)
from testsuite.runner import execute_run


REVIEW = {"persona": "oel", "persona_label": "Pensionistenpaar", "kind": "advisory",
          "check": "Keine erfundenen Zahlen"}
ADVISORY_STEPS = [{"id": "start", "action": "start_assessment"},
                  {"id": "create", "action": "create_conversation"},
                  {"id": "ask", "action": "send_conversation_message", "payload": {"text": "Was spart das?"}}]


def scenario_data(**changes):
    return {"schema_version": 1, "id": "adv", "version": "1", "domain_id": "verein-demo",
            "domain_version": "1.0.0", "persist_raw": True, "steps": ADVISORY_STEPS, "review": REVIEW, **changes}


def test_scenario_accepts_review_block():
    scenario = Scenario.model_validate(scenario_data())
    assert scenario.review.kind == "advisory"
    assert scenario.model_dump(mode="json", exclude_unset=True)["review"] == REVIEW


@pytest.mark.parametrize("changes", [
    {"persist_raw": False},
    {"review": {**REVIEW, "kind": "llm_recommendations"}},
    {"review": {**REVIEW, "kind": "unknown"}},
    {"review": {**REVIEW, "check": ""}},
    {"review": {**REVIEW, "persona": "mit leerzeichen"}},
    {"steps": [{"id": "start", "action": "start_assessment"}, {"id": "recs", "action": "llm_recommendations"}]},
])
def test_invalid_review_blocks_rejected(changes):
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario_data(**changes))


class RecordingClient:
    def request(self, method, path, payload=None, *, llm=False, request_id=None):
        if path == "/api/v1/domains":
            body = [{"domain_id": "verein-demo", "version": "1.0.0"}]
        else:
            body = {"ok": True, "session_id": "sess_1", "conversation": {"conversation_id": "conv_1", "messages": []}}
        return {"http_status": 200, "response_payload": body, "duration_ms": 1.0}


def test_runner_copies_review_block_to_case_and_counts_review_cases(tmp_path):
    repo = JsonlRepository(tmp_path)
    plain = Scenario.model_validate(scenario_data(id="plain", review=None, steps=ADVISORY_STEPS[:1]))
    run = execute_run([Scenario.model_validate(scenario_data()), plain], RecordingClient(), repo, include_llm=True)
    data = repo.read(run["id"])
    assert data["runs"][0]["review_cases"] == 1
    assert data["cases"][0]["review"] == REVIEW
    assert "review" not in data["cases"][1]


QUESTION = {"question_id": "funding_source", "field_key": "funding_source", "text": "Woher stammen die Einnahmen?",
            "options": [{"value": "donations", "label": "Spenden und Sponsoring"}, {"value": "public_grants", "label": "Öffentliche Förderungen"}]}
AREA = {"question_id": "living_area_sqm", "field_key": "living_area_sqm", "text": "Wie groß ist die Wohnfläche?",
        "options": []}
DOCUMENT = {"kind": "document", "title": "Energiesparen", "locator": "Seite 25", "excerpt": "Zwei Grad weniger …"}
RULE = {"kind": "rule", "title": "Raumtemperatur senken", "locator": None, "excerpt": None}
LLM = {"kind": "llm", "title": None, "locator": None, "excerpt": None}


def assessment_events(case_id, start=0):
    answers = {"funding_source": {"field_key": "funding_source", "normalized_value": "donations"},
               "living_area_sqm": {"field_key": "living_area_sqm", "normalized_value": None}}
    result = {"reason_codes": ["OLD_HEATING"],
              "recommendations": [{"recommendation_id": "heating-check", "title": "Heizung überprüfen lassen"}]}
    payloads = [("start_assessment", None, {"ok": True, "current_question": QUESTION}),
                ("submit_answer", {"question_id": "funding_source", "value": "donations"},
                 {"ok": True, "current_question": AREA}),
                ("submit_answer", {"question_id": "living_area_sqm", "value": None},
                 {"ok": True, "current_question": None}),
                ("complete", None, {"ok": True, "answers": answers, "result": result})]
    return [{"id": f"{case_id}_e{start + index}", "run_id": "run1", "case_id": case_id, "sequence_no": start + index,
             "action": action, "request_payload": request, "response_payload": response, "duration_ms": 5.0}
            for index, (action, request, response) in enumerate(payloads, 1)]


def store_run(repo):
    """Schreibt einen Lauf mit einer Fachfrage, KI-Empfehlungen, einem Fehlerfall und einem normalen Fall."""
    repo.write("runs", {"id": "run1", "run_id": "run1", "status": "passed", "review_cases": 3,
                        "started_at": "2026-10-05T12:48:58+00:00", "finished_at": "2026-10-05T13:06:10+00:00",
                        "metadata": {"target": {"provider": {"model": "testmodell"},
                                                "git": {"commit": "60927a94d686673e5e6aac8d0dfe503dbe29eb25"},
                                                "domain_packs": [{"domain_id": "verein-demo", "version": "1.0.0"}]}}})
    cases = [("c_adv", "adv", REVIEW), ("c_recs", "recs", {**REVIEW, "kind": "llm_recommendations",
                                                          "check": "Neu gegenüber den Regeln?"}),
             ("c_err", "edge", {**REVIEW, "persona": "foerderverein", "persona_label": "Familie in Miete", "kind": "edge"}),
             ("c_plain", "plain", None)]
    for index, (case_id, scenario_id, review) in enumerate(cases):
        case = {"id": case_id, "run_id": "run1", "scenario_id": scenario_id, "attempt": 1, "sequence_no": index,
                "status": "passed", "scenario_snapshot": {}}
        if review:
            case["review"] = review
        repo.write("cases", case)
        for event in assessment_events(case_id):
            repo.write("events", event)
    answer = {"role": "assistant", "content": "Rund sechs Prozent.", "sections": [
        {"text": "Rund **12 %** weniger Heizenergie.", "sources": [RULE, DOCUMENT], "unverified_values": ["12"]},
        {"text": "Mehr dazu bei der Energieberatung.", "sources": [LLM], "unverified_values": []}]}
    repo.write("events", {"id": "c_adv_ask", "run_id": "run1", "case_id": "c_adv", "sequence_no": 9,
                          "action": "send_conversation_message", "request_payload": {"text": "Was spart das?"},
                          "duration_ms": 17925.4, "response_payload": {"ok": True, "conversation": {"messages": [
                              {"role": "user", "content": "Was spart das?"}, answer]}}})
    repo.write("events", {"id": "c_recs_recs", "run_id": "run1", "case_id": "c_recs", "sequence_no": 9,
                          "action": "llm_recommendations", "request_payload": None, "duration_ms": 41000.0,
                          "response_payload": {"ok": True, "recommendations": [
                              {"title": "Heizkörper entlüften", "message": "Regelmäßig entlüften.",
                               "rationale": "Alte Ölheizung.", "evidence_fields": ["funding_source", "unknown_field"],
                               "sources": [LLM], "unverified_values": []}]}})
    repo.write("events", {"id": "c_err_ask", "run_id": "run1", "case_id": "c_err", "sequence_no": 9,
                          "action": "send_conversation_message", "request_payload": {"text": "Wer wird Meister?"},
                          "duration_ms": 30000.0, "error_code": "ReadTimeout", "response_payload": None})


def test_review_cases_are_built_from_evidence_alone(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    advisory, recommendations, failed = review_cases(repo.read("run1"))
    assert advisory["case_id"] == "c_adv" and advisory["scenario_id"] == "adv"
    assert advisory["question"] == "Was spart das?"
    assert advisory["check"] == "Keine erfundenen Zahlen"
    assert advisory["seconds"] == 18
    assert advisory["error"] is None
    assert [section["text"] for section in advisory["sections"]] == [
        "Rund **12 %** weniger Heizenergie.", "Mehr dazu bei der Energieberatung."]
    assert advisory["sections"][0]["unverified_values"] == ["12"]
    assert advisory["source_kinds"] == ["document", "rule", "llm"]
    assert advisory["unverified"] == 1
    assert recommendations["question"] == "Zusätzliche KI-Empfehlungen zum Ergebnis"
    assert recommendations["sections"][0]["text"] == (
        "**Heizkörper entlüften**\nRegelmäßig entlüften.\nBegründung: Alte Ölheizung.")
    # Belegfelder erscheinen als Angabe mit dem Fragetext aus den Belegen, unbekannte mit ihrem Schlüssel.
    assert recommendations["sections"][0]["sources"][:2] == [
        {"kind": "user_context", "title": "Woher stammen die Einnahmen?"}, {"kind": "user_context", "title": "unknown_field"}]
    assert failed["sections"] == [] and failed["error"] == "ReadTimeout"
    assert failed["kind"] == "edge" and failed["persona"] == "foerderverein"


def test_review_summary_counts_sources_and_durations(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    summary = review_summary(review_cases(repo.read("run1")))
    assert summary == {"cases": 3, "answered": 2, "with_document": 1, "llm_only": 1, "with_unverified": 1,
                       "median_seconds": 30, "max_seconds": 41}


def test_persona_profiles_use_labels_from_evidence(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    profiles = persona_profiles(repo.read("run1"))
    assert list(profiles) == ["oel", "foerderverein"]
    assert profiles["oel"] == {"label": "Pensionistenpaar", "reason_codes": ["OLD_HEATING"],
                               "recommendations": ["Heizung überprüfen lassen"], "current": False,
                               "answers": [("Woher stammen die Einnahmen?", "Spenden und Sponsoring"),
                                           ("Wie groß ist die Wohnfläche?", "unbekannt / übersprungen")]}


def test_new_rating_validates_reviewer_and_verdict():
    rating = new_rating("run1", "c_adv", "  Anna  ", "teils", " Zahl stimmt nicht ")
    assert rating["reviewer"] == "Anna" and rating["verdict"] == "teils" and rating["note"] == "Zahl stimmt nicht"
    assert rating["run_id"] == "run1" and rating["case_id"] == "c_adv" and rating["id"] and rating["created_at"]
    assert new_rating("run1", "c_adv", "Anna", None, "")["verdict"] is None
    with pytest.raises(ValueError, match="Namen"):
        new_rating("run1", "c_adv", "  ", "gut", "")
    with pytest.raises(ValueError, match="Bewertung"):
        new_rating("run1", "c_adv", "Anna", "super", "")


def rating(case_id, reviewer, verdict, created_at, note=""):
    return {"id": f"{case_id}-{reviewer}-{created_at}", "run_id": "run1", "case_id": case_id, "reviewer": reviewer,
            "verdict": verdict, "note": note, "created_at": created_at}


def test_ratings_are_appended_and_latest_per_reviewer_is_current(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    for record in (rating("c_adv", "Anna", "gut", "2026-10-06T08:00:00+00:00"),
                   rating("c_adv", "Ben", "schlecht", "2026-10-06T08:05:00+00:00", "Zahl erfunden"),
                   rating("c_adv", "Anna", "teils", "2026-10-06T09:00:00+00:00", "doch nicht ganz")):
        repo.write("ratings", record)
    ratings = repo.read("run1")["ratings"]
    assert len(ratings) == 3
    current = current_ratings(ratings)
    assert current["c_adv"]["Anna"]["verdict"] == "teils"
    assert current["c_adv"]["Ben"]["note"] == "Zahl erfunden"


def test_ratings_csv_keeps_full_history_and_marks_current(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    repo.write("ratings", rating("c_adv", "Anna", "gut", "2026-10-06T08:00:00+00:00"))
    repo.write("ratings", rating("c_adv", "Anna", "teils", "2026-10-06T09:00:00+00:00", 'Wert "12 %"; unbelegt'))
    data = repo.read("run1")
    rows = list(csv.DictReader(io.StringIO(ratings_csv(review_cases(data), data["ratings"])), delimiter=";"))
    assert [(row["szenario"], row["bewerter"], row["bewertung"], row["aktuell"]) for row in rows] == [
        ("adv", "Anna", "gut", "nein"), ("adv", "Anna", "teils", "ja")]
    assert rows[1]["notiz"] == 'Wert "12 %"; unbelegt'
    assert rows[1]["frage"] == "Was spart das?" and rows[1]["persona"] == "oel" and rows[1]["art"] == "advisory"


def cleanup_args(**changes):
    return argparse.Namespace(**{"run_id": "run1", "older_than_days": None, "confirm": True,
                                 "include_rated": False, "results_dir": None, **changes})


def test_cleanup_keeps_rated_runs_unless_explicitly_included(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    repo.write("ratings", rating("c_adv", "Anna", "gut", "2026-10-06T08:00:00+00:00"))
    summary = cleanup(cleanup_args(results_dir=tmp_path), repo)
    assert summary["run_ids"] == [] and summary["kept_rated"] == ["run1"]
    assert len(repo.read("run1")["ratings"]) == 1
    summary = cleanup(cleanup_args(results_dir=tmp_path, include_rated=True), repo)
    assert summary["run_ids"] == ["run1"] and summary["kept_rated"] == []
    assert not (tmp_path / "run1.jsonl").exists()


# --- AppTest: Modus Beratungstest ---


def review_app(monkeypatch, tmp_path):
    monkeypatch.setenv("GUIDEOS_ADMIN_ENABLED", "1")
    monkeypatch.delenv("GUIDEOS_TESTSUITE_DATABASE_URL", raising=False)
    monkeypatch.setenv("GUIDEOS_TESTSUITE_RESULTS_DIR", str(tmp_path))
    app = AppTest.from_file("app.py", default_timeout=10).run()
    return app.sidebar.radio[0].set_value("Beratungstest").run()


def page_text(app):
    return "\n".join(str(item.value) for item in [*app.title, *app.header, *app.subheader, *app.markdown,
                                                  *app.caption, *app.info, *app.error, *app.success])


def test_review_mode_is_hidden_without_admin_flag(monkeypatch):
    monkeypatch.delenv("GUIDEOS_ADMIN_ENABLED", raising=False)
    app = AppTest.from_file("app.py").run()
    assert "Beratungstest" not in app.sidebar.radio[0].options


def test_review_mode_explains_missing_runs(monkeypatch, tmp_path):
    app = review_app(monkeypatch, tmp_path)
    assert not app.exception
    assert "Noch kein Testlauf mit Bewertungsangaben" in page_text(app)


def test_review_mode_shows_answers_with_check_and_sources(monkeypatch, tmp_path):
    store_run(JsonlRepository(tmp_path))
    app = review_app(monkeypatch, tmp_path)
    assert not app.exception
    text = page_text(app)
    for expected in ("Pensionistenpaar", "Was spart das?", "Keine erfundenen Zahlen",
                     "Rund **12 %** weniger Heizenergie.", "Regel: Raumtemperatur senken",
                     "Dokument: Energiesparen, Seite 25", "Ungeprüft: 12", "KI-Fachwissen",
                     "Zusätzliche KI-Empfehlungen zum Ergebnis", "ReadTimeout", "testmodell"):
        assert expected in text, expected
    assert [metric.value for metric in app.metric][:4] == ["2 / 3", "1", "1", "1"]


def submit_rating(app, case_id, verdict, note):
    app.radio(key=f"verdict_{case_id}").set_value(verdict)
    app.text_input(key=f"note_{case_id}").set_value(note)
    return next(button for button in app.button if button.key and case_id in button.key).click().run()


def test_rating_needs_a_reviewer_name(monkeypatch, tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    app = submit_rating(review_app(monkeypatch, tmp_path), "c_adv", "teils", "Zahl unbelegt")
    assert "Bitte trage zuerst deinen Namen ein" in page_text(app)
    assert repo.read("run1")["ratings"] == []


def test_rating_is_stored_with_reviewer_and_shown_again(monkeypatch, tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    app = review_app(monkeypatch, tmp_path)
    app.sidebar.text_input(key="review_reviewer").set_value("Anna").run()
    app = submit_rating(app, "c_adv", "teils", "Zahl unbelegt")
    assert not app.exception
    stored = repo.read("run1")["ratings"]
    assert [(r["case_id"], r["reviewer"], r["verdict"], r["note"]) for r in stored] == [
        ("c_adv", "Anna", "teils", "Zahl unbelegt")]
    # Eine neue Browser-Sitzung zeigt die gespeicherte Bewertung wieder an.
    fresh = review_app(monkeypatch, tmp_path)
    fresh.sidebar.text_input(key="review_reviewer").set_value("Anna").run()
    assert fresh.radio(key="verdict_c_adv").value == "teils"
    assert fresh.text_input(key="note_c_adv").value == "Zahl unbelegt"
    # Andere Bewerter sehen sie als fremde Bewertung, ihre eigene bleibt offen.
    other = review_app(monkeypatch, tmp_path)
    other.sidebar.text_input(key="review_reviewer").set_value("Ben").run()
    assert other.radio(key="verdict_c_adv").value == "offen"
    assert "Anna: Teilweise – Zahl unbelegt" in page_text(other)


def test_review_reads_profile_recommendations_as_recommendation_step():
    from testsuite.models import Scenario
    from testsuite.review import review_cases

    Scenario.model_validate({
        "schema_version": 1, "id": "s", "version": "1.0.0", "domain_id": "verein-demo",
        "domain_version": "1.0.0", "tags": ["llm"], "data_classification": "synthetic", "persist_raw": True,
        "review": {"persona": "p", "persona_label": "P", "kind": "llm_recommendations", "check": "C"},
        "steps": [{"id": "start", "action": "start_assessment"}, {"id": "recs", "action": "profile_recommendations"}],
    })
    data = {
        "cases": [{"id": "c1", "scenario_id": "s", "status": "passed",
                   "review": {"persona": "p", "persona_label": "P", "kind": "llm_recommendations", "check": "C"}}],
        "events": [{"case_id": "c1", "sequence_no": 1, "action": "profile_recommendations", "duration_ms": 1200,
                    "request_payload": None,
                    "response_payload": {"ok": True, "recommendations": [
                        {"recommendation_id": "a", "title": "T", "message": "M", "rationale": "R",
                         "evidence_fields": [], "sources": [{"kind": "llm", "title": "KI-Fachwissen"}],
                         "unverified_values": []}]}}],
        "assertions": [], "artifacts": [], "ratings": [],
    }

    [case] = review_cases(data)

    assert case["error"] is None and len(case["sections"]) == 1


def test_persona_profiles_show_the_latest_profile_state_of_the_case(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    changed = {"funding_source": {"field_key": "funding_source", "normalized_value": "public_grants", "origin": "chat_update"},
               "living_area_sqm": {"field_key": "living_area_sqm", "normalized_value": None, "origin": "assessment"}}
    result = {"reason_codes": ["GRANTS_DEPENDENT"],
              "recommendations": [{"recommendation_id": "heating-swap", "title": "Heizungstausch prüfen"}]}
    repo.write("events", {"id": "c_adv_profile", "run_id": "run1", "case_id": "c_adv", "sequence_no": 10,
                          "action": "get_profile", "request_payload": None, "duration_ms": 3.0,
                          "response_payload": {"ok": True, "profile": {"revision": 2, "answers": changed,
                                                                       "result": result}}})
    profiles = persona_profiles(repo.read("run1"))
    assert profiles["oel"] == {"label": "Pensionistenpaar", "reason_codes": ["GRANTS_DEPENDENT"],
                               "recommendations": ["Heizungstausch prüfen"], "current": True,
                               "answers": [("Woher stammen die Einnahmen?", "Öffentliche Förderungen (im Chat geändert)"),
                                           ("Wie groß ist die Wohnfläche?", "unbekannt / übersprungen")]}
    assert profiles["foerderverein"]["current"] is False


def test_review_cases_carry_the_dialog_before_the_rated_answer(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    messages = [{"role": "user", "content": "Eine Energieberatung lehnen wir ab."},
                {"role": "assistant", "content": "Soll ich die Empfehlung als abgelehnt vermerken?"},
                {"role": "user", "content": "ja"},
                {"role": "assistant", "content": "Vermerkt."},
                {"role": "user", "content": "Was nun?"},
                {"role": "assistant", "content": "Verbrauch erfassen.", "sections": [
                    {"text": "Verbrauch erfassen.", "sources": [RULE], "unverified_values": []}]}]
    repo.write("events", {"id": "c_adv_later", "run_id": "run1", "case_id": "c_adv", "sequence_no": 11,
                          "action": "send_conversation_message", "request_payload": {"text": "Was nun?"},
                          "duration_ms": 900.0, "response_payload": {"ok": True, "conversation": {"messages": messages}}})
    case = next(case for case in review_cases(repo.read("run1")) if case["case_id"] == "c_adv")
    assert case["question"] == "Was nun?"
    assert case["sections"][0]["text"] == "Verbrauch erfassen."
    assert case["history"] == [(m["role"], m["content"]) for m in messages[:-1]]
    recs = next(case for case in review_cases(repo.read("run1")) if case["case_id"] == "c_recs")
    assert recs["history"] == []


def test_persona_profiles_mark_recommendations_with_user_feedback(tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    answers = {"funding_source": {"field_key": "funding_source", "normalized_value": "donations", "origin": "assessment"}}
    result = {"reason_codes": ["OLD_HEATING"], "recommendations": [
        {"recommendation_id": "heating-check", "title": "Heizung überprüfen lassen"},
        {"recommendation_id": "club-consultation", "title": "Vereinsberatung einholen"}]}
    feedback = {"club-consultation": {"kind": "rule", "title": "Vereinsberatung einholen", "origin": "chat",
                                        "stance": "rejected", "recommendation_id": "club-consultation"}}
    repo.write("events", {"id": "c_adv_profile", "run_id": "run1", "case_id": "c_adv", "sequence_no": 10,
                          "action": "get_profile", "request_payload": None, "duration_ms": 3.0,
                          "response_payload": {"ok": True, "profile": {"revision": 2, "answers": answers,
                                                                       "result": result, "feedback": feedback}}})
    profiles = persona_profiles(repo.read("run1"))
    assert profiles["oel"]["recommendations"] == ["Heizung überprüfen lassen",
                                                  "Vereinsberatung einholen (vom Nutzer abgelehnt)"]


def test_review_mode_shows_the_dialog_before_the_rated_answer(monkeypatch, tmp_path):
    repo = JsonlRepository(tmp_path)
    store_run(repo)
    messages = [{"role": "user", "content": "Eine Energieberatung lehnen wir ab."},
                {"role": "assistant", "content": "Soll ich die Empfehlung als abgelehnt vermerken?"},
                {"role": "user", "content": "ja"}, {"role": "assistant", "content": "Vermerkt."},
                {"role": "user", "content": "Was nun?"}, {"role": "assistant", "content": "Verbrauch erfassen."}]
    repo.write("events", {"id": "c_adv_later", "run_id": "run1", "case_id": "c_adv", "sequence_no": 11,
                          "action": "send_conversation_message", "request_payload": {"text": "Was nun?"},
                          "duration_ms": 900.0, "response_payload": {"ok": True, "conversation": {"messages": messages}}})
    app = review_app(monkeypatch, tmp_path)
    assert not app.exception
    text = page_text(app)
    assert "Verlauf davor (5 Nachrichten)" in [expander.label for expander in app.expander]
    assert "Nutzer:** Eine Energieberatung lehnen wir ab." in text
    assert "GuideOS:** Vermerkt." in text
    assert "Verbrauch erfassen." in text
