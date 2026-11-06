"""Streamlit-Ansicht „Beratungstest“: Antworten eines Testlaufs lesen und fachlich bewerten."""
from __future__ import annotations

import os
from datetime import datetime

import streamlit as st

from testsuite.repository import repository_from_env
from testsuite.review import (KIND_LABELS, SOURCE_LABELS, VERDICT_LABELS, current_ratings, new_rating,
                              persona_profiles, ratings_csv, review_cases, review_summary)


VERDICT_OPTIONS = ["offen", *VERDICT_LABELS]
FILTERS = {"alle": "Alle", "offen": "Offen", "advisory": "Fachfragen", "edge": "Grenzfälle",
           "llm_recommendations": "KI-Empfehlungen", "teils": "Teilweise", "schlecht": "Mangelhaft"}
BADGE_COLORS = {"document": "blue", "web": "blue", "rule": "green", "user_context": "violet", "llm": "orange"}


def _plain(text) -> str:
    """Modell- und Quellentext ist Fremdtext: keine LaTeX-Auswertung, Zeilenumbrüche bleiben sichtbar."""
    return str(text).replace("$", "\\$").replace("\n", "  \n")


def _badge(text: str, color: str) -> str:
    cleaned = " ".join(str(text).split()).replace("[", "(").replace("]", ")").replace("$", "\\$")
    return f":{color}-badge[{cleaned}]"


def _source_text(source: dict) -> str:
    kind = source.get("kind", "llm")
    if kind == "llm":
        return SOURCE_LABELS["llm"]
    detail = source.get("title") or source.get("url") or ""
    if source.get("locator"):
        detail += f", {source['locator']}"
    return f"{SOURCE_LABELS.get(kind, kind)}: {detail}"


def _verdict_text(rating: dict) -> str:
    text = f"{rating['reviewer']}: {VERDICT_LABELS.get(rating['verdict'], 'offen')}"
    return f"{text} – {rating['note']}" if rating["note"] else text


def _timestamp(value: str | None) -> str:
    return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M UTC") if value else "unbekannt"


def _run_details(run: dict) -> dict[str, str]:
    target = (run.get("metadata") or {}).get("target") or {}
    packs = ", ".join(f"{pack.get('domain_id')} {pack.get('version')}" for pack in target.get("domain_packs") or [])
    return {"Pack": packs or "unbekannt", "Modell": (target.get("provider") or {}).get("model") or "unbekannt",
            "Code-Stand": ((target.get("git") or {}).get("commit") or "unbekannt")[:7],
            "Gestartet": _timestamp(run.get("started_at")), "Lauf-ID": run["id"]}


def _run_label(run: dict) -> str:
    details = _run_details(run)
    return f"{details['Gestartet']} · {details['Pack']} · {details['Modell']} · {run['review_cases']} Fälle"


def _storage_hint() -> str:
    if os.environ.get("GUIDEOS_TESTSUITE_DATABASE_URL"):
        return "in PostgreSQL (Schema `testsuite`)"
    return f"in der Belegdatei des Laufs unter `{os.environ.get('GUIDEOS_TESTSUITE_RESULTS_DIR', '.guideos/testsuite')}`"


def _render_section(section: dict) -> None:
    st.markdown(_plain(section["text"]))
    badges = [_badge(_source_text(source), BADGE_COLORS.get(source.get("kind", "llm"), "gray"))
              for source in section["sources"]]
    if section["unverified_values"]:
        badges.append(_badge("Ungeprüft: " + ", ".join(map(str, section["unverified_values"])), "red"))
    if badges:
        st.markdown(" ".join(badges))
    excerpts = [source for source in section["sources"] if source.get("excerpt")]
    if excerpts:
        with st.expander(f"Quellenauszug ({len(excerpts)})"):
            for source in excerpts:
                st.markdown(f"**{_plain(_source_text(source))}**")
                st.caption(_plain(source["excerpt"]))


def _render_case(repository, run_id: str, case: dict, reviewer: str, ratings: dict[str, dict]) -> None:
    case_id = case["case_id"]
    mine = ratings.get(reviewer) or {}
    with st.container(border=True):
        duration = f" · {case['seconds']} s" if case["seconds"] is not None else ""
        st.caption(f"{KIND_LABELS[case['kind']]}{duration} · {case['scenario_id']}")
        st.subheader(case["question"])
        st.caption(f"Worauf achten: {case['check']}")
        if case["history"]:
            with st.expander(f"Verlauf davor ({len(case['history'])} Nachrichten)"):
                st.markdown("\n\n".join(f"**{'Nutzer' if role == 'user' else 'GuideOS'}:** {_plain(content)}"
                                        for role, content in case["history"]))
        if case["error"]:
            st.error(f"Keine Antwort: {case['error']}")
        elif not case["sections"]:
            st.info("Das Modell hat keine zusätzlichen Empfehlungen geliefert.")
        for section in case["sections"]:
            _render_section(section)
        # Der gespeicherte Stand füllt die Eingaben vor; ungespeicherte Eingaben bleiben im Sitzungszustand.
        st.session_state.setdefault(f"verdict_{case_id}", mine.get("verdict") or "offen")
        st.session_state.setdefault(f"note_{case_id}", mine.get("note") or "")
        with st.form(f"rate_{case_id}", border=False):
            verdict_column, note_column, save_column = st.columns([3, 4, 1], vertical_alignment="bottom")
            verdict = verdict_column.radio("Bewertung", VERDICT_OPTIONS, horizontal=True, key=f"verdict_{case_id}",
                                           format_func=lambda value: VERDICT_LABELS.get(value, "Offen"))
            note = note_column.text_input("Notiz", key=f"note_{case_id}")
            submitted = save_column.form_submit_button("Speichern", key=f"save_{case_id}")
        if submitted:
            try:
                repository.write("ratings", new_rating(run_id, case_id, reviewer,
                                                       None if verdict == "offen" else verdict, note))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.review_flash = f"Bewertung gespeichert: {case['question']}"
                st.rerun()
        others = [_verdict_text(rating) for name, rating in ratings.items() if name != reviewer]
        if others:
            st.caption("Weitere Bewertungen: " + " · ".join(others))


def _matches(case: dict, selected: str, reviewer: str, ratings: dict[str, dict]) -> bool:
    if selected == "alle":
        return True
    if selected == "offen":
        return not (ratings.get(reviewer) or {}).get("verdict")
    if selected in KIND_LABELS:
        return case["kind"] == selected
    return any(rating["verdict"] == selected for rating in ratings.values())


def render_review() -> None:
    st.title("Beratungstest")
    reviewer = st.sidebar.text_input("Dein Name (für Bewertungen)", key="review_reviewer").strip()
    if st.sidebar.button("Läufe neu laden"):
        st.session_state.pop("review_runs", None)
    try:
        repository = repository_from_env()
        if "review_runs" not in st.session_state:
            runs = [run for run in repository.runs() if run.get("review_cases")]
            st.session_state.review_runs = sorted(runs, key=lambda run: run.get("started_at") or "", reverse=True)
    except Exception:
        st.error("Der Ergebnisspeicher des Test-Runners ist nicht erreichbar.")
        return
    runs = {run["id"]: run for run in st.session_state.review_runs}
    if not runs:
        st.info("Noch kein Testlauf mit Bewertungsangaben vorhanden. Szenarien brauchen dafür den Block "
                "`review` (siehe `docs/test-runner.md`).")
        return
    run_id = st.sidebar.selectbox("Testlauf", list(runs), format_func=lambda id: _run_label(runs[id]))
    data = repository.read(run_id)
    cases = review_cases(data)
    ratings = current_ratings(data["ratings"])
    if st.session_state.get("review_loaded") != (run_id, reviewer):
        # Anderer Lauf oder andere Person: Eingaben aus dem gespeicherten Stand neu aufbauen.
        for key in [key for key in st.session_state if str(key).startswith(("verdict_", "note_"))]:
            del st.session_state[key]
        st.session_state.review_loaded = (run_id, reviewer)
    if st.session_state.get("review_flash"):
        st.success(st.session_state.pop("review_flash"))

    st.caption(" · ".join(f"{label}: {value}" for label, value in _run_details(runs[run_id]).items()))
    summary = review_summary(cases)
    tiles = st.columns(5)
    tiles[0].metric("Fälle mit Antwort", f"{summary['answered']} / {summary['cases']}")
    tiles[1].metric("Antworten mit Dokumentquelle", str(summary["with_document"]))
    tiles[2].metric("Antworten nur aus KI-Fachwissen", str(summary["llm_only"]))
    tiles[3].metric("Antworten mit ungeprüften Zahlen", str(summary["with_unverified"]))
    tiles[4].metric("Antwortzeit (Median)", f"{summary['median_seconds']} s" if summary["median_seconds"] is not None
                    else "–", help=f"Längste Antwort: {summary['max_seconds']} s")

    verdicts = [(ratings.get(case["case_id"], {}).get(reviewer) or {}).get("verdict") for case in cases]
    rated_by_anyone = sum(any(rating["verdict"] for rating in ratings.get(case["case_id"], {}).values())
                          for case in cases)
    st.markdown(f"**{sum(bool(verdict) for verdict in verdicts)} von {len(cases)} von dir bewertet** · "
                + " · ".join(f"{verdicts.count(value)} {label.lower()}" for value, label in VERDICT_LABELS.items())
                + f" · {rated_by_anyone} von {len(cases)} insgesamt bewertet")
    st.caption(f"Jede gespeicherte Bewertung liegt mit Name und Zeitpunkt {_storage_hint()}; frühere Fassungen "
               "bleiben als Verlauf erhalten.")
    st.download_button("Alle Bewertungen als CSV herunterladen", ratings_csv(cases, data["ratings"]).encode("utf-8-sig"),
                       file_name=f"bewertungen-{run_id}.csv", mime="text/csv")

    with st.expander("Übersicht"):
        st.caption("Die Quellenangaben sind automatisch erhobene Merkmale der Antwort und keine Bewertung: woher "
                   "GuideOS die Aussagen belegt hat und wie viele Zahlen in keiner Quelle vorkommen.")
        st.dataframe([{
            "Persona": case["persona_label"], "Frage": case["question"], "Art": KIND_LABELS[case["kind"]],
            "Quellen": ", ".join(["Fehler"] * bool(case["error"])
                                 + [SOURCE_LABELS[kind] for kind in case["source_kinds"]]
                                 + [f"{case['unverified']} ungeprüft"] * bool(case["unverified"])),
            "Sek.": case["seconds"],
            "Bewertungen": " · ".join(_verdict_text(rating) for rating in ratings.get(case["case_id"], {}).values())
                           or "offen",
        } for case in cases], width="stretch", hide_index=True)

    profiles = persona_profiles(data)
    filter_column, persona_column = st.columns([3, 1])
    selected = filter_column.radio("Anzeigen", list(FILTERS), format_func=FILTERS.get, horizontal=True,
                                   key="review_filter")
    persona = persona_column.selectbox("Persona", ["alle", *profiles], key="review_persona",
                                       format_func=lambda key: "Alle" if key == "alle" else profiles[key]["label"])
    reading_column, _ = st.columns([3, 1])
    with reading_column:
        for key, profile in profiles.items():
            visible = [case for case in cases if case["persona"] == key
                       and _matches(case, selected, reviewer, ratings.get(case["case_id"], {}))]
            if persona not in ("alle", key) or not visible:
                continue
            st.header(profile["label"])
            if profile["answers"]:
                title = ("Aktuelles Profil, Reason-Codes und Regel-Empfehlungen" if profile["current"]
                         else "Angaben beim Abschluss, Reason-Codes und Regel-Empfehlungen")
                with st.expander(title):
                    st.markdown("\n".join(f"- {_plain(question)} **{_plain(value)}**"
                                          for question, value in profile["answers"]))
                    st.caption("Reason-Codes: " + (", ".join(profile["reason_codes"]) or "keine"))
                    st.caption("Regel-Empfehlungen: " + (", ".join(map(_plain, profile["recommendations"])) or "keine"))
            for case in visible:
                _render_case(repository, run_id, case, reviewer, ratings.get(case["case_id"], {}))
