import sys

from streamlit.testing.v1 import AppTest

import guideos.providers.registry as registry
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.adapters.streamlit import build_assessment, build_demo_assessment
from guideos.conversations import Conversation
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import ConversationService
from guideos.domains.catalog import LocalCatalogStore
from guideos.domains.loader import load_domain_pack, validate_domain_pack_data
from guideos.knowledge.factory import knowledge_service_from_env
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore
from guideos.profiles.repository import LocalProfileRepository
from tests.pack_fixtures import PERSONAS, install_tools_pack, tools_pack_data

DEMO_DOMAIN_LABEL = "Vereins-Check Demo"  # the bundled demo pack verein-demo


def _select_domain(app, prefix):
    domain_select = next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Assessment-Domain"
    )
    option = next(o for o in domain_select.options if o.startswith(prefix))
    domain_select.set_value(option).run()
    return app


def _open_demo_domain(app):
    """Open the assessment mode on the demo pack; app.py has no default
    domain, so the alphabetically first active pack would be shown."""
    app.sidebar.radio[0].set_value("Assessment").run()
    return _select_domain(app, DEMO_DOMAIN_LABEL)


def _install_pack(tmp_path, monkeypatch, pack):
    """Make an inline pack an active version for app.py through a temporary
    catalog (like tests.pack_fixtures.install_tools_pack); returns the stored
    pack, whose content hash sessions must carry to be listed."""
    from guideos.domains import catalog
    from guideos.domains.catalog import activate_domain_version, save_domain_pack_draft

    store = LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json")
    monkeypatch.setattr(catalog, "catalog_store_from_env", lambda **kwargs: store)
    save_domain_pack_draft(pack, store=store)
    return activate_domain_version(pack.domain_id, pack.version, store=store)


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def list_models(self, endpoint):
        return ["fake", "fake-large"]

    def generate_structured(self, endpoint, model, messages, schema):
        return {
            "decision": "valid",
            "normalized_value": "sports",
            "confidence": 0.95,
            "reason_code": None,
            "follow_up": None,
        }


def test_build_demo_assessment_wires_interpreter_and_repository(tmp_path):
    orchestrator = build_demo_assessment(
        FakeProvider(),
        tmp_path / "assessments.json",
    )
    response = orchestrator.start()

    response = orchestrator.submit_text(
        response.session_id,
        "club_type",
        "ein Sportverein",
        "http://fake",
        "fake",
    )

    assert response.accepted_answer.normalized_value == "sports"
    assert response.current_question.field_key == "founded_year"


def test_app_can_open_assessment_and_start_demo_session(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GUIDEOS_ASSESSMENT_PATH",
        str(tmp_path / "assessments.json"),
    )
    app = AppTest.from_file("app.py").run()

    _open_demo_domain(app)

    assert any(title.value == "Vereins-Check Demo" for title in app.title)
    start_button = next(
        button
        for button in app.button
        if button.label == "Neues Assessment starten"
    )
    start_button.click().run()
    assert any(
        "Art von Verein" in item.value
        for item in [*app.subheader, *app.markdown]
    )
    assert len(app.chat_input) == 1
    assert all(item.label != "Antwort" for item in app.selectbox)
    assert all(item.label != "Antwort" for item in app.number_input)
    # The pack help_text feeds the LLM only; the chat shows just the hint line.
    assert not any("Statuten" in item.value for item in app.caption)
    assert any(item.value.startswith("Mögliche Antworten") for item in app.caption)


class FakeChatProvider(FakeProvider):
    def __init__(self, result):
        self.result = result

    def generate_structured(self, endpoint, model, messages, schema):
        return self.result


def _start_demo_session(app):
    _open_demo_domain(app)
    start_button = next(
        button
        for button in app.button
        if button.label == "Neues Assessment starten"
    )
    start_button.click().run()
    return app


def test_app_question_chat_accepts_answer_and_shows_next_question(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setitem(
        registry._PROVIDERS,
        "ollama",
        FakeChatProvider(
            {
                "message": "Alles klar, Sportverein.",
                "proposes_answer": True,
                "normalized_value": "sports",
                "confidence": 0.9,
            }
        ),
    )
    app = _start_demo_session(AppTest.from_file("app.py").run())

    app.chat_input[0].set_value("Es ist ein Sportverein").run()

    markdown = [item.value for item in app.markdown]
    assert not any("Antwort übernommen" in item for item in markdown)
    assert "Alles klar, Sportverein." not in markdown
    assert "Welche Art von Verein ist es?" not in [s.value for s in app.subheader]
    assert "In welchem Jahr wurde der Verein gegründet?" in [s.value for s in app.subheader]
    previous = next(item for item in app.expander if item.label.startswith("Bisherige Angaben"))
    assert any("Sportverein" in item.value for item in previous.markdown)
    assert not app.error


def test_app_question_chat_shows_reply_without_advancing(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setitem(
        registry._PROVIDERS,
        "ollama",
        FakeChatProvider(
            {
                "message": "Gemeint ist der Hauptzweck des Vereins.",
                "proposes_answer": False,
                "normalized_value": None,
                "confidence": 0.0,
            }
        ),
    )
    app = _start_demo_session(AppTest.from_file("app.py").run())

    app.chat_input[0].set_value("Was ist gemeint?").run()

    markdown = [item.value for item in app.markdown]
    assert "Gemeint ist der Hauptzweck des Vereins." in markdown
    assert "Welche Art von Verein ist es?" in [s.value for s in app.subheader]
    assert "In welchem Jahr wurde der Verein gegründet?" not in [s.value for s in app.subheader]


def test_app_shows_completed_assessment_as_single_chat(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    response = _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    assert response.status == "completed"
    assert any(
        "completed" in option
        for selectbox in app.sidebar.selectbox
        for option in selectbox.options
    )
    transcript = [m.markdown[0].value for m in app.chat_message if m.markdown]
    assert any(text.startswith("Onboarding ist abgeschlossen") for text in transcript)
    assert not any("75/100" in text for text in transcript)
    assert any("Ehrenamt sichern" in text and "GuideOS-Regel" in text for text in transcript)
    assert any("Empfehlungen aus KI-Fachwissen" in text for text in transcript)
    assert all(item.label != "KI-Empfehlungen erzeugen" for item in app.button)
    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    assert len(chats.options) == 1 and "Ergebnis" in chats.options[0]
    assert all(item.value != "Rückfragen zum Ergebnis" for item in app.subheader)
    assert all(item.label != "Frage" for item in app.text_input)
    assert len(app.chat_input) == 1
    assert all("Bericht" not in item.label for item in app.expander)
    assert all("Bericht" not in item.label for item in app.button)


def test_app_shows_report_only_with_cli_flag(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(),
        LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(sys, "argv", ["app.py", "--show-report"])

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    assert not app.error
    assert any(item.label == "Bericht (Markdown)" for item in app.expander)
    assert len(app.chat_input) == 1


def test_app_can_select_a_second_domain_and_start_session(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GUIDEOS_ASSESSMENT_PATH",
        str(tmp_path / "assessments.json"),
    )
    _install_typed_pack(tmp_path, monkeypatch)
    app = AppTest.from_file("app.py").run()

    app.sidebar.radio[0].set_value("Assessment").run()
    domain_select = next(
        selectbox for selectbox in app.selectbox if selectbox.label == "Assessment-Domain"
    )
    second_option = next(
        option
        for option in domain_select.options
        if option.startswith("Fragetypen-Test")
    )
    domain_select.set_value(second_option).run()

    assert any(
        title.value == "Fragetypen-Test"
        for title in app.title
    )
    start_button = next(
        button for button in app.button if button.label == "Neues Assessment starten"
    )
    start_button.click().run()
    assert any(
        "Wie heißt dein Betrieb?" in item.value
        for item in [*app.subheader, *app.markdown]
    )


def test_admin_mode_is_hidden_without_env_flag(monkeypatch):
    monkeypatch.delenv("GUIDEOS_ADMIN_ENABLED", raising=False)

    app = AppTest.from_file("app.py").run()

    assert "Domain-Admin" not in app.sidebar.radio[0].options


def test_admin_mode_is_visible_with_env_flag(monkeypatch):
    monkeypatch.setenv("GUIDEOS_ADMIN_ENABLED", "1")

    app = AppTest.from_file("app.py").run()

    assert "Domain-Admin" in app.sidebar.radio[0].options


def test_pilot_mode_requires_a_configured_test_password(monkeypatch):
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.delenv("GUIDEOS_TEST_PASSWORD", raising=False)

    app = AppTest.from_file("app.py").run()

    assert any(
        "GUIDEOS_TEST_PASSWORD" in error.value
        for error in app.error
    )


def _completed_response(orchestrator):
    response = orchestrator.start()
    for question_id, value in [
        ("club_type", "sports"),
        ("founded_year", 1980),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", None),
    ]:
        response = orchestrator.submit(response.session_id, question_id, value)
    return orchestrator.complete(response.session_id)


def test_sessions_are_filtered_by_domain_pack(tmp_path):
    repository_path = tmp_path / "assessments.json"
    demo = build_demo_assessment(FakeProvider(), repository_path)
    other = build_assessment(_typed_pack(), FakeProvider(), repository_path)

    demo_response = demo.start()
    other_response = other.start()

    demo_sessions = [
        session for session in demo.repository.list()
        if session.domain_id == demo.pack.domain_id
        and session.domain_version == demo.pack.version
        and session.domain_content_hash == demo.pack.content_hash
    ]

    assert [session.session_id for session in demo_sessions] == [
        demo_response.session_id
    ]
    assert other_response.session_id not in [
        session.session_id for session in demo_sessions
    ]


class FakeAdvisoryProvider(FakeProvider):
    def __init__(self):
        self.calls = []

    def generate_structured(self, endpoint, model, messages, schema):
        self.calls.append(messages)
        return {"sections": [{"text": "Beratungsantwort", "source_ids": self.source_ids}]}

    source_ids: list[str] = []


ADVISORY_ANSWER = "Beratungsantwort  \n_Quelle: KI-Fachwissen_"


def test_app_first_advisory_message_creates_chat_automatically(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    conversations_path = tmp_path / "conversations.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    response = _completed_response(orchestrator)
    provider = FakeAdvisoryProvider()
    monkeypatch.setitem(registry._PROVIDERS, "ollama", provider)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(conversations_path))

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    app.chat_input[0].set_value("Was empfiehlst du?").run()

    stored = LocalConversationRepository(conversations_path).list()
    profile = LocalProfileRepository(path.parent / "profiles.json").get("verein-demo", "local")
    assert [c.profile_id for c in stored] == [profile.profile_id]
    assert profile.source_session_id == response.session_id
    assert [m.role for m in stored[0].messages] == ["assistant", "assistant", "assistant", "user", "assistant"]
    assert [m.content for m in stored[0].messages][3:] == ["Was empfiehlst du?", ADVISORY_ANSWER]
    transcript = [(m.name, m.markdown[0].value) for m in app.chat_message if m.markdown]
    assert transcript[-2:] == [("user", "Was empfiehlst du?"), ("assistant", ADVISORY_ANSWER)]
    assert "verified_user_context" in provider.calls[0][1]["content"]
    assert not any("verified_user_context" in item.value for item in app.markdown)
    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    assert len(chats.options) == 1


def test_app_shows_excerpts_of_cited_knowledge_documents(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    _completed_response(orchestrator)
    provider = FakeAdvisoryProvider()
    provider.source_ids = ["D1"]
    monkeypatch.setitem(registry._PROVIDERS, "ollama", provider)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    knowledge_service_from_env(provider).add_document(
        "verein-demo", "leitfaden.md",
        "# Förderung\n\nDie Vereinsausstattung wird gefördert.".encode(), "http://fake",
    )

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    assert [item.label for item in app.expander] == ["Profil"]
    app.chat_input[0].set_value("Wird die Vereinsausstattung gefördert?").run()

    answer = [m for m in app.chat_message if m.markdown][-1]
    assert answer.markdown[0].value == (
        "Beratungsantwort  \n_Quelle: Dokument „Förderung“, Förderung_"
    )
    [excerpts] = [item for item in app.expander if item.label == "Quellenauszüge"]
    assert excerpts.markdown[0].value == "**Dokument „Förderung“, Förderung**"
    assert "Die Vereinsausstattung wird gefördert." in excerpts.caption[0].value


def test_app_sidebar_switches_between_isolated_advisory_chats(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    conversations_path = tmp_path / "conversations.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    _completed_response(orchestrator)
    provider = FakeAdvisoryProvider()
    monkeypatch.setitem(registry._PROVIDERS, "ollama", provider)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(conversations_path))
    service = ConversationService(
        LocalConversationRepository(conversations_path),
        orchestrator.profiles,
        provider,
    )
    first = service.create("verein-demo", title="Finanzen")
    second = service.create("verein-demo", title="Förderung")

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    assert any("Finanzen" in option for option in chats.options)
    chats.select(first.conversation_id).run()
    app.chat_input[0].set_value("Wie stehen die Finanzen?").run()

    assert [m.content for m in service.get(first.conversation_id).messages] == [
        "Wie stehen die Finanzen?",
        ADVISORY_ANSWER,
    ]
    assert service.get(second.conversation_id).messages == []

    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    chats.select(second.conversation_id).run()
    transcript = [m.markdown[0].value for m in app.chat_message if m.markdown]
    assert not any("Wie stehen die Finanzen?" in text for text in transcript)

    new_chat = next(button for button in app.sidebar.button if button.label == "Neuer Chat")
    new_chat.click().run()
    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    # Finanzen, Förderung, the profile's result chat and the new one.
    assert len(chats.options) == 4
    assert [m for m in app.chat_message if m.markdown] == []
    assert not any("Onboarding ist abgeschlossen" in item.value for item in app.markdown)


class FakeRecommendationProvider(FakeProvider):
    def generate_structured(self, endpoint, model, messages, schema):
        return {
            "recommendations": [
                {
                    "recommendation_id": "fee-review",
                    "title": "Mitgliedsbeiträge prüfen",
                    "message": "Verringert bei geförderten Vereinen oft die Abhängigkeit.",
                    "rationale": "Förderabhängiger Verein.",
                    "evidence_fields": ["funding_source"],
                    "confidence": 0.7,
                }
            ]
        }


def test_app_generates_llm_recommendations_into_the_chat(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    _completed_response(orchestrator)
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeRecommendationProvider())
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    assert not any("Mitgliedsbeiträge prüfen" in item.value for item in app.markdown)
    assert all(item.label != "KI-Empfehlungen erzeugen" for item in app.button)
    app.chat_input[0].set_value("ja").run()

    transcript = [m.markdown[0].value for m in app.chat_message if m.markdown]
    assert any(
        "Mitgliedsbeiträge prüfen" in text and "keine GuideOS-Regel" in text
        for text in transcript
    )


class FakeStreamingProvider(FakeProvider):
    def stream_chat(self, endpoint, model, messages):
        yield "Geheime Antwort"


def test_chat_mode_does_not_print_prompt_or_answer_to_stdout(monkeypatch, capsys):
    import guideos.storage as storage
    from guideos.chat import create_initial_chat_state

    monkeypatch.setattr(storage, "load_chat_state", lambda *args, **kwargs: create_initial_chat_state())
    monkeypatch.setattr(storage, "save_chat_state", lambda *args, **kwargs: None)
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeStreamingProvider())

    app = AppTest.from_file("app.py").run()
    app.chat_input[0].set_value("Geheimer Prompt").run()

    captured = capsys.readouterr().out
    assert "Geheimer Prompt" not in captured
    assert "Geheime Antwort" not in captured
    assert "Prompt:" not in captured
    assert any("Geheime Antwort" in m.markdown[0].value for m in app.chat_message if m.markdown)


def test_deleting_an_older_assessment_keeps_the_profile_chats(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    conversations_path = tmp_path / "conversations.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    older = _completed_response(orchestrator)
    newer = _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(conversations_path))
    service = ConversationService(
        LocalConversationRepository(conversations_path), orchestrator.profiles, FakeProvider()
    )
    chat = service.create("verein-demo", title="Bleibt")

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    sessions = next(item for item in app.sidebar.selectbox if item.label == "Gespeicherte Assessments")
    sessions.select(older.session_id).run()
    delete = next(button for button in app.sidebar.button if button.label == "Ausgewähltes Assessment löschen")
    delete.click().run()

    assert [s.session_id for s in orchestrator.repository.list()] == [newer.session_id]
    remaining = {c.conversation_id: c.title for c in service.list()}
    assert remaining[chat.conversation_id] == "Bleibt"
    assert sorted(remaining.values()) == ["Bleibt", "Ergebnis"]
    assert orchestrator.profiles.get("verein-demo", "local").source_session_id == newer.session_id


def test_pilot_mode_scopes_advisory_chats_to_participant(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter

    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", "geheim")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://unused")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    monkeypatch.setattr(streamlit_adapter, "assessment_repository_from_env", lambda path: assessments)
    monkeypatch.setattr(streamlit_adapter, "conversation_repository_from_env", lambda path: conversations)
    monkeypatch.setattr(streamlit_adapter, "profile_repository_from_env", lambda: profiles)
    monkeypatch.setattr(streamlit_adapter, "PostgresPilotFeedbackRepository", lambda url: None)
    monkeypatch.setattr(
        streamlit_adapter,
        "knowledge_service_from_env",
        lambda provider: KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json")),
    )
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeAdvisoryProvider())

    app = AppTest.from_file("app.py").run()
    app.text_input[0].set_value("test")
    app.text_input[1].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    app = app.run()
    _select_domain(app, DEMO_DOMAIN_LABEL)
    next(button for button in app.button if button.label == "Neues Assessment starten").click().run()
    session = assessments.list()[0]
    participant = session.participant_id
    assert participant
    orchestrator = AssessmentOrchestrator(load_domain_pack(), assessments, profiles=profiles)
    for question_id, value in [
        ("club_type", "sports"), ("founded_year", 1980), ("members_count", 120),
        ("funding_source", "public_grants"), ("volunteer_situation", "tight"), ("annual_budget_eur", None),
    ]:
        orchestrator.submit(session.session_id, question_id, value)
    orchestrator.complete(session.session_id)
    foreign = Conversation.create(
        domain_id="verein-demo", title="Fremd", participant_id="jemand-anderes",
        profile_id="prof_fremd",
    )
    conversations.save(foreign)

    app = app.run()
    app.chat_input[0].set_value("Was empfiehlst du?").run()

    stored = {c.conversation_id: c for c in conversations.list()}
    own = [c for c in stored.values() if c.participant_id == participant]
    assert len(own) == 1 and [m.content for m in own[0].messages][3:4] == ["Was empfiehlst du?"]
    chats = next(item for item in app.sidebar.selectbox if item.label == "Beratungschats")
    assert len(chats.options) == 1 and "Fremd" not in chats.options[0]


class FakeFeedbackRepository:
    def get(self, session_id, participant_id):
        return None

    def save(self, session_id, participant_id, rating, comment):
        raise AssertionError("save darf in diesem Test nicht aufgerufen werden")


def test_pilot_mode_shows_feedback_form_expanded_after_completion(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter

    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", "geheim")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://unused")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    monkeypatch.setattr(streamlit_adapter, "assessment_repository_from_env", lambda path: assessments)
    monkeypatch.setattr(streamlit_adapter, "conversation_repository_from_env", lambda path: conversations)
    monkeypatch.setattr(streamlit_adapter, "profile_repository_from_env", lambda: profiles)
    monkeypatch.setattr(
        streamlit_adapter, "PostgresPilotFeedbackRepository", lambda url: FakeFeedbackRepository()
    )
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeAdvisoryProvider())

    app = AppTest.from_file("app.py").run()
    app.text_input[0].set_value("test")
    app.text_input[1].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    app = app.run()
    _select_domain(app, DEMO_DOMAIN_LABEL)
    next(button for button in app.button if button.label == "Neues Assessment starten").click().run()
    session = assessments.list()[0]
    orchestrator = AssessmentOrchestrator(load_domain_pack(), assessments, profiles=profiles)
    for question_id, value in [
        ("club_type", "sports"), ("founded_year", 1980), ("members_count", 120),
        ("funding_source", "public_grants"), ("volunteer_situation", "tight"), ("annual_budget_eur", None),
    ]:
        orchestrator.submit(session.session_id, question_id, value)
    orchestrator.complete(session.session_id)

    app = app.run()

    feedback = next(item for item in app.expander if item.label == "Feedback zum Pilot")
    assert feedback.proto.expanded is True
    assert any(button.label == "Feedback senden" for button in app.button)



def test_assessment_sidebar_lists_available_models_in_a_dropdown(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeProvider())

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    models = next(item for item in app.sidebar.selectbox if item.label == "Assessment-Modell")
    assert models.options == ["fake", "fake-large"]
    assert models.value == "fake"
    assert all(item.label != "Assessment-Modell" for item in app.sidebar.text_input)


def test_assessment_sidebar_falls_back_to_text_input_without_model_list(tmp_path, monkeypatch):
    from guideos.providers.base import ProviderError

    class OfflineProvider(FakeProvider):
        def list_models(self, endpoint):
            raise ProviderError("Ollama-Endpunkt konnte nicht erreicht werden.")

    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setitem(registry._PROVIDERS, "ollama", OfflineProvider())

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    field = next(item for item in app.sidebar.text_input if item.label == "Assessment-Modell")
    assert field.value == "fake"
    assert all(item.label != "Assessment-Modell" for item in app.sidebar.selectbox)
    assert any("Modellliste" in item.value for item in app.sidebar.caption)


def test_chat_mode_lists_available_models_in_a_dropdown(monkeypatch):
    import guideos.storage as storage
    from guideos.chat import create_initial_chat_state

    monkeypatch.setattr(storage, "load_chat_state", lambda *args, **kwargs: create_initial_chat_state())
    monkeypatch.setattr(storage, "save_chat_state", lambda *args, **kwargs: None)
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeProvider())

    app = AppTest.from_file("app.py").run()

    models = next(item for item in app.selectbox if item.label == "Model")
    assert models.options == ["fake", "fake-large"]
    assert models.value == "fake"
    assert all(item.label != "Model" for item in app.text_input)


def _demo_question(field_type, **overrides):
    from guideos.domains.models import QuestionDefinition

    data = {
        "question_id": "q",
        "field_key": "q",
        "text": "Frage?",
        "field_type": field_type,
        "required": True,
        "options": [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Beta"}]
        if field_type in ("select", "multiselect")
        else [],
    }
    data.update(overrides)
    return QuestionDefinition.model_validate(data)


def test_question_hint_describes_each_field_type():
    from guideos.adapters.streamlit import _question_hint

    assert _question_hint(_demo_question("select")) == "Mögliche Antworten: Alpha, Beta"
    assert _question_hint(_demo_question("multiselect")) == (
        "Mehrfachauswahl möglich: Alpha, Beta"
    )
    assert _question_hint(
        _demo_question("multiselect", validation={"min_count": 1, "max_count": 2})
    ) == "Mehrfachauswahl möglich (mindestens 1, höchstens 2): Alpha, Beta"
    assert _question_hint(_demo_question("text")) == "Freitext"
    assert _question_hint(
        _demo_question("text", validation={"min_length": 2, "max_length": 80})
    ) == "Freitext (2 bis 80 Zeichen)"
    assert _question_hint(_demo_question("text", validation={"max_length": 500})) == (
        "Freitext (höchstens 500 Zeichen)"
    )
    assert _question_hint(_demo_question("boolean")) == "Antwort mit Ja oder Nein"
    assert _question_hint(_demo_question("date")) == "Datum im Format TT.MM.JJJJ"
    assert _question_hint(
        _demo_question("date", validation={"min_date": "1900-01-01", "max_date": "2026-12-31"})
    ) == "Datum im Format TT.MM.JJJJ zwischen 01.01.1900 und 31.12.2026"
    assert _question_hint(_demo_question("integer", validation={"minimum": 0})) == (
        "Ganze Zahl ab 0"
    )
    assert _question_hint(_demo_question("number", required=False)).endswith(
        "Du kannst auch 'unbekannt' antworten."
    )


# An inline pack with every field type and widget (text input/textarea,
# select radio/dropdown, multiselect checkboxes/dropdown, integer, number,
# boolean, date, file), installed through a temporary catalog when a test
# opens it in the app.
TYPED_PACK_DATA = {
    "domain_id": "fragetypen-test", "version": "1.0.0", "name": "Fragetypen-Test",
    "description": "Testpack mit allen Fragetypen.", "default_locale": "de",
    "questions": [
        {"question_id": "company_name", "field_key": "company_name", "text": "Wie heißt dein Betrieb?",
         "field_type": "text", "required": True, "validation": {"min_length": 2, "max_length": 80}},
        {"question_id": "legal_form", "field_key": "legal_form", "text": "Welche Rechtsform hat der Betrieb?",
         "field_type": "select", "widget": "radio", "required": True,
         "options": [{"value": "einzelunternehmen", "label": "Einzelunternehmen"}, {"value": "gmbh", "label": "GmbH"},
                     {"value": "verein", "label": "Verein"}, {"value": "sonstige", "label": "Sonstige"}]},
        {"question_id": "industry", "field_key": "industry", "text": "In welcher Branche ist der Betrieb tätig?",
         "field_type": "select", "widget": "dropdown", "required": True,
         "options": [{"value": "handwerk", "label": "Handwerk"}, {"value": "handel", "label": "Handel"},
                     {"value": "dienstleistung", "label": "Dienstleistung"}]},
        {"question_id": "channels", "field_key": "channels",
         "text": "Über welche Kanäle erreichen dich Kundinnen und Kunden?", "field_type": "multiselect",
         "required": True, "validation": {"min_count": 1, "max_count": 3},
         "options": [{"value": "email", "label": "E-Mail"}, {"value": "telefon", "label": "Telefon"},
                     {"value": "website", "label": "Website"}, {"value": "social_media", "label": "Soziale Medien"},
                     {"value": "vor_ort", "label": "Vor Ort"}]},
        {"question_id": "tools", "field_key": "tools", "text": "Welche digitalen Werkzeuge nutzt der Betrieb bereits?",
         "field_type": "multiselect", "widget": "dropdown", "required": False,
         "options": [{"value": "buchhaltung", "label": "Buchhaltungssoftware"},
                     {"value": "crm", "label": "Kundenverwaltung (CRM)"}, {"value": "shop", "label": "Online-Shop"}]},
        {"question_id": "employees", "field_key": "employees", "text": "Wie viele Personen arbeiten im Betrieb?",
         "field_type": "integer", "required": True, "validation": {"minimum": 0, "maximum": 10000}},
        {"question_id": "online_revenue_share", "field_key": "online_revenue_share",
         "text": "Wie viel Prozent des Umsatzes entfallen auf den Online-Verkauf?", "field_type": "number",
         "required": True, "allow_unknown": True, "validation": {"minimum": 0, "maximum": 100},
         "visible_if": {"field_key": "channels", "operator": "contains", "value": "website"}},
        {"question_id": "has_website", "field_key": "has_website", "text": "Hat der Betrieb eine eigene Website?",
         "field_type": "boolean", "required": True},
        {"question_id": "website_url", "field_key": "website_url", "text": "Wie lautet die Adresse der Website?",
         "field_type": "text", "required": True, "validation": {"pattern": "^https?://\\S+$"},
         "visible_if": {"field_key": "has_website", "operator": "equals", "value": True}},
        {"question_id": "founded_on", "field_key": "founded_on", "text": "Wann wurde der Betrieb gegründet?",
         "field_type": "date", "required": True, "allow_unknown": True,
         "validation": {"min_date": "1900-01-01", "max_date": "2026-12-31"}},
        {"question_id": "summary", "field_key": "summary", "text": "Beschreibe kurz, was der Betrieb anbietet.",
         "field_type": "text", "widget": "textarea", "required": False, "validation": {"max_length": 500}},
        {"question_id": "attachment", "field_key": "attachment",
         "text": "Möchtest du eine Beispieldatei zum Betrieb hochladen?", "field_type": "file", "required": False,
         "upload": {"extensions": ["csv", "txt"]}},
    ],
    "rules": [
        {"rule_id": "no-website", "field_key": "has_website", "operator": "equals", "value": False,
         "reason_code": "NO_WEBSITE", "recommendation_ids": ["build-website"]},
    ],
    "recommendations": [
        {"recommendation_id": "build-website", "priority": 1, "title": "Eigene Website aufbauen",
         "message": "Eine eigene Website macht den Betrieb auffindbar."},
    ],
    "tools": {"enabled": False},
}


def _typed_pack():
    return validate_domain_pack_data(TYPED_PACK_DATA)


def _install_typed_pack(tmp_path, monkeypatch):
    return _install_pack(tmp_path, monkeypatch, _typed_pack())


def _review_response_for_demo(path, pack):
    orchestrator = AssessmentOrchestrator(pack, LocalAssessmentRepository(path))
    response = orchestrator.start()
    for question_id, value in [
        ("company_name", "Bäckerei Huber"),
        ("legal_form", "gmbh"),
        ("industry", "handwerk"),
        ("channels", ["email", "website"]),
        ("employees", 4),
        ("online_revenue_share", 5),
        ("has_website", True),
        ("website_url", "https://huber.example"),
        ("founded_on", "1998-03-12"),
        ("tools", ["crm"]),
        ("summary", "Brot und Gebäck."),
        ("attachment", None),
    ]:
        response = orchestrator.submit(response.session_id, question_id, value)
    assert response.status == "review"
    return orchestrator, response


def _open_typed_domain(app):
    app.sidebar.radio[0].set_value("Assessment").run()
    return _select_domain(app, "Fragetypen-Test")


def test_app_shows_text_hint_for_first_demo_question(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    _install_typed_pack(tmp_path, monkeypatch)
    app = _open_typed_domain(AppTest.from_file("app.py").run())

    next(b for b in app.button if b.label == "Neues Assessment starten").click().run()

    assert not app.error
    assert any(item.value == "Freitext (2 bis 80 Zeichen)" for item in app.caption)
    # The question chat input renders inline under the question, not pinned to the bottom.
    assert [item.placeholder for item in app.main.get("chat_input")] == [
        "Antwort oder Rückfrage eingeben"
    ]


def test_review_forms_use_type_specific_widgets(tmp_path, monkeypatch):
    import datetime

    path = tmp_path / "assessments.json"
    orchestrator, response = _review_response_for_demo(path, _install_typed_pack(tmp_path, monkeypatch))
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    app = _open_typed_domain(AppTest.from_file("app.py").run())

    assert not app.error
    session_id = response.session_id
    radios = {item.form_id: item for item in app.radio if item.label == "Antwort"}
    assert f"revise_{session_id}_legal_form" in radios
    assert radios[f"revise_{session_id}_legal_form"].value == "gmbh"
    assert radios[f"revise_{session_id}_has_website"].value is True
    checkboxes = {item.label: item for item in app.checkbox
                  if item.form_id == f"revise_{session_id}_channels"}
    assert checkboxes["E-Mail"].value is True and checkboxes["Telefon"].value is False
    multiselect = next(item for item in app.multiselect
                       if item.form_id == f"revise_{session_id}_tools")
    assert multiselect.value == ["crm"]
    text_inputs = {item.form_id: item for item in app.text_input if item.label == "Antwort"}
    assert text_inputs[f"revise_{session_id}_company_name"].value == "Bäckerei Huber"
    text_area = next(item for item in app.text_area if item.label == "Antwort")
    assert text_area.value == "Brot und Gebäck."
    date_input = next(item for item in app.date_input if item.label == "Antwort")
    assert date_input.value == datetime.date(1998, 3, 12)

    date_input.set_value(datetime.date(2001, 5, 6))
    save = next(b for b in app.button
                if b.label == "Änderung speichern" and b.form_id == f"revise_{session_id}_founded_on")
    save.click().run()

    stored = orchestrator.get(session_id)
    assert stored.answers["founded_on"].normalized_value == "2001-05-06"


def test_app_single_choice_question_uses_radio_and_save_button(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    app = _start_demo_session(AppTest.from_file("app.py").run())

    radio = next(item for item in app.radio if item.label == "Antwort")
    assert radio.options == [o.label for o in load_domain_pack().questions[0].options]
    assert radio.value is None
    assert all(item.label != "Antwort" for item in app.selectbox)
    assert app.chat_input[0].placeholder == "Rückfrage zur Frage eingeben"

    next(b for b in app.button if b.label == "Antwort speichern").click().run()
    assert any("erforderlich" in item.value for item in app.error)

    radio = next(item for item in app.radio if item.label == "Antwort")
    radio.set_value("sports")
    next(b for b in app.button if b.label == "Antwort speichern").click().run()

    assert not app.error
    assert "In welchem Jahr wurde der Verein gegründet?" in [s.value for s in app.subheader]
    assert app.chat_input[0].placeholder == "Antwort oder Rückfrage eingeben"
    assert all(item.label != "Antwort" for item in app.radio)


def test_app_multiple_choice_question_uses_checkboxes(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    pack = _install_typed_pack(tmp_path, monkeypatch)
    orchestrator = AssessmentOrchestrator(pack, LocalAssessmentRepository(path))
    response = orchestrator.start()
    for question_id, value in [
        ("company_name", "Bäckerei Huber"), ("legal_form", "gmbh"), ("industry", "handwerk"),
    ]:
        response = orchestrator.submit(response.session_id, question_id, value)
    assert response.current_question.field_key == "channels"
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    app = _open_typed_domain(AppTest.from_file("app.py").run())

    boxes = {item.label: item for item in app.checkbox}
    assert {"E-Mail", "Telefon", "Website", "Soziale Medien", "Vor Ort"} <= set(boxes)
    boxes["E-Mail"].set_value(True)
    boxes["Website"].set_value(True)
    next(b for b in app.button if b.label == "Antwort speichern").click().run()

    assert not app.error
    stored = orchestrator.get(response.session_id)
    assert stored.answers["channels"].normalized_value == ["email", "website"]
    assert "Wie viele Personen arbeiten im Betrieb?" in [s.value for s in app.subheader]
    assert all(item.label != "Antwort" for item in app.number_input)


def test_original_link_points_to_the_cited_page():
    from guideos.adapters.streamlit import original_link

    assert original_link("/media/abc.pdf", "Seite 25") == "media/abc.pdf#page=25"
    assert original_link("/media/abc.pdf", "Förderung") == "media/abc.pdf"
    assert original_link("/media/abc.txt", None) == "media/abc.txt"


def test_app_links_cited_passages_to_the_original_document(tmp_path, monkeypatch):
    from tests.knowledge_fixtures import make_pdf

    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(path.parent / "profiles.json"),
    )
    _completed_response(orchestrator)
    provider = FakeAdvisoryProvider()
    provider.source_ids = ["D1"]
    monkeypatch.setitem(registry._PROVIDERS, "ollama", provider)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    raw = make_pdf(["Deckblatt", "Die Vereinsausstattung wird gefoerdert."])
    knowledge_service_from_env(provider).add_document(
        "verein-demo", "leitfaden.pdf", raw, "http://fake", title="Leitfaden"
    )
    registered = []

    def register(data, mimetype, coordinates):
        registered.append((data, mimetype))
        return "/media/abc.pdf"

    monkeypatch.setattr("guideos.adapters.streamlit._register_media", register)

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    app.chat_input[0].set_value("Wird die Vereinsausstattung gefoerdert?").run()

    [excerpts] = [item for item in app.expander if item.label == "Quellenauszüge"]
    assert [item.value for item in excerpts.markdown] == [
        "**Dokument „Leitfaden“, Seite 2**",
        "[Im Dokument öffnen (Seite 2)](media/abc.pdf#page=2)",
    ]
    assert registered[-1] == (raw, "application/pdf")


def test_app_shows_profile_after_completion_and_hints_at_newer_profile(tmp_path, monkeypatch):
    from guideos.profiles.repository import LocalProfileRepository

    path = tmp_path / "assessments.json"
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(load_domain_pack(), LocalAssessmentRepository(path), profiles=profiles)
    older = _completed_response(orchestrator)
    newer = _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(sys, "argv", ["app.py", "--show-report"])

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    # The newest completed session is selected by default and is the profile's source.
    assert not app.error
    profile = next(item for item in app.expander if item.label == "Profil")
    texts = " ".join(item.value for item in profile.markdown)
    assert "Woher stammt der Großteil der Einnahmen?" in texts and "Öffentliche Förderungen" in texts
    assert "Ehrenamt sichern" in texts
    assert not any("neueren Assessment" in item.value for item in app.info)
    assert any(item.label == "Bericht (Markdown)" for item in app.expander)

    sessions = next(item for item in app.sidebar.selectbox if item.label == "Gespeicherte Assessments")
    sessions.set_value(older.session_id).run()

    assert any("neueren Assessment" in item.value for item in app.info)
    assert all(item.label != "Profil" for item in app.expander)
    assert any(item.label == "Bericht (Markdown)" for item in app.expander)
    assert newer.session_id != older.session_id


def test_profile_expander_offers_feedback_buttons_and_shows_open_questions(tmp_path, monkeypatch):
    from guideos.profiles.repository import LocalProfileRepository
    from guideos.profiles.service import ProfileService

    path = tmp_path / "assessments.json"
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(load_domain_pack(), LocalAssessmentRepository(path), profiles=profiles)
    _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    reject = next(b for b in app.button if b.key == "feedback_rejected_volunteer-recruiting")
    reject.click().run()

    profile = profiles.get("verein-demo", "local")
    assert profile.feedback["volunteer-recruiting"].stance == "rejected"
    assert profile.feedback["volunteer-recruiting"].origin == "button"
    expander = next(item for item in app.expander if item.label == "Profil")
    assert any("Vom Nutzer abgelehnt" in item.value for item in expander.markdown)

    ProfileService(profiles).update_fact("verein-demo", "local", "funding_source", "membership_fees")
    app = app.run()
    expander = next(item for item in app.expander if item.label == "Profil")
    texts = " ".join(item.value for item in expander.markdown)
    assert "Stand vor der Änderung" not in texts  # nothing generated, nothing stale
    assert "Mitgliedsbeiträge" in texts


def test_profile_expander_lets_the_participant_delete_the_profile(tmp_path, monkeypatch):
    from guideos.profiles.repository import LocalProfileRepository

    path = tmp_path / "assessments.json"
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    repository = LocalAssessmentRepository(path)
    orchestrator = AssessmentOrchestrator(load_domain_pack(), repository, profiles=profiles)
    _completed_response(orchestrator)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))

    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)

    delete = next(b for b in app.button if b.key == "profile_delete_verein-demo")
    assert delete.disabled
    app.checkbox(key="profile_delete_confirm_verein-demo").check().run()
    next(b for b in app.button if b.key == "profile_delete_verein-demo").click().run()
    # AppTest keeps the elements of the aborted run next to the rerun's; one more run shows the page as a browser does.
    app = app.run()

    assert not app.error
    assert profiles.list() == []
    assert repository.list() == []
    assert all(item.label != "Profil" for item in app.expander)
    assert any(b.label == "Neues Assessment starten" for b in app.button)


# --- File questions (the tools pack of tests.pack_fixtures) ---


TOOLS_DOMAIN_LABEL = "Vereins-Check mit Kassabuch"
TOOLS_UPLOAD_NAME = "Kassabuch-AT123456789012.csv"  # the long id is masked in the stored name
TOOLS_UPLOAD_NAME_MASKED = "Kassabuch-AT….csv"


def _tools_pack_tools(tmp_path, pack):
    from tests.test_file_questions import _tools

    return _tools(tmp_path, pack)


def _open_tools_domain(app):
    app.sidebar.radio[0].set_value("Assessment").run()
    return _select_domain(app, TOOLS_DOMAIN_LABEL)


def _tools_session_at_file_question(path, tools, pack):
    """The tools pack asks for the file first: after "yes" to the gate
    question the file question is the current one."""
    from guideos.tools.datasets import DatasetImporter

    orchestrator = AssessmentOrchestrator(
        pack, LocalAssessmentRepository(path), profiles=LocalProfileRepository(path.parent / "profiles.json"),
        dataset_importer=DatasetImporter(tools),
    )
    response = orchestrator.start()
    response = orchestrator.submit(response.session_id, "dataset_available", "yes")
    assert response.current_question.question_id == "dataset_file"
    return orchestrator, response


def _answer_remaining(orchestrator, session_id):
    """The persona's answers for the questions still open after an upload;
    the optional second file is skipped."""
    response = orchestrator.get(session_id)
    for question_id, value in [*PERSONAS["sportverein_gefoerdert"], ("second_file", None)]:
        if question_id in response.answers:
            continue
        response = orchestrator.submit(session_id, question_id, value)
    return response


def test_app_file_question_uploads_through_the_dataset_tool(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter
    from tests.test_file_questions import CSV

    path = tmp_path / "assessments.json"
    pack = install_tools_pack(tmp_path, monkeypatch)
    tools, client = _tools_pack_tools(tmp_path, pack)
    orchestrator, response = _tools_session_at_file_question(path, tools, pack)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(path.parent / "profiles.json"))
    monkeypatch.setattr(streamlit_adapter, "tool_service_from_env", lambda: tools)
    app = _open_tools_domain(AppTest.from_file("app.py").run())

    assert "Lade dein Kassabuch als CSV-Datei hoch." in [s.value for s in app.subheader]
    assert any(c.value.startswith("Datei hochladen (.csv, .txt)") for c in app.caption)
    assert not app.chat_input  # no model interpretation for a file
    uploader = next(item for item in app.file_uploader if item.label == "Datei auswählen")
    formats = next(item for item in app.selectbox if item.label == "Dateiformat")
    assert formats.options == ["auto", "csv"]  # from the tool's input schema

    next(b for b in app.button if b.label == "Antwort speichern").click().run()
    assert any("Bitte zuerst eine Datei auswählen" in item.value for item in app.error)

    uploader = next(item for item in app.file_uploader if item.label == "Datei auswählen")
    uploader.upload(TOOLS_UPLOAD_NAME, CSV.encode("utf-8"), "text/csv")
    next(b for b in app.button if b.label == "Antwort speichern").click().run()
    app.run()

    assert not app.error
    assert client.calls[0][0] == "import_dataset"
    assert client.calls[0][1]["filename"] == TOOLS_UPLOAD_NAME_MASKED
    stored = orchestrator.get(response.session_id)
    assert stored.answers["dataset_file"].normalized_value["dataset_id"] == "ds_1"
    # The first analysis ran (its tools are not registered on this fake server, so
    # every step failed and is recorded); the record quality is derived anyway.
    session = orchestrator.repository.get(response.session_id)
    assert [e.payload["tool"] for e in session.events if e.event_type == "analysis_failed"] == [
        "analyze_tiers", "analyze_windows", "analyze_characteristics"]
    assert stored.answers["records_kept"].normalized_value == "complete"
    assert stored.answers["records_kept"].origin == "measurement"
    assert stored.status == "in_progress" and stored.current_question.question_id == "club_type"
    assert any(TOOLS_UPLOAD_NAME_MASKED in m.value for m in app.markdown)
    assert any("aus den Messdaten abgeleitet" in m.value for m in app.markdown)


def test_app_file_question_shows_tool_errors_and_can_be_skipped(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter
    from guideos.tools.models import ToolError
    from tests.test_file_questions import CSV

    path = tmp_path / "assessments.json"
    pack = install_tools_pack(tmp_path, monkeypatch)
    tools, client = _tools_pack_tools(tmp_path, pack)
    orchestrator, response = _tools_session_at_file_question(path, tools, pack)
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(path.parent / "profiles.json"))
    monkeypatch.setattr(streamlit_adapter, "tool_service_from_env", lambda: tools)
    client.fail_with = ToolError("tool_error", "Die Kopfzeile wurde nicht erkannt.", {"code": "invalid_format"})
    app = _open_tools_domain(AppTest.from_file("app.py").run())

    uploader = next(item for item in app.file_uploader if item.label == "Datei auswählen")
    uploader.upload("export.csv", CSV.encode("utf-8"), "text/csv")
    next(b for b in app.button if b.label == "Antwort speichern").click().run()

    assert any("Kopfzeile" in item.value for item in app.error)
    assert "dataset_file" not in orchestrator.get(response.session_id).answers

    next(b for b in app.button if b.label == "Unbekannt / überspringen").click().run()
    app.run()
    stored = orchestrator.get(response.session_id)
    assert stored.answers["dataset_file"].normalized_value is None
    assert stored.status == "in_progress" and stored.current_question.question_id == "records_kept"


def test_app_deleting_an_assessment_with_measurement_data_asks_and_forgets_them(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter
    from guideos.conversations.service import DATASET_DELETED_REPLY
    from guideos.tools.models import DatasetRef
    from tests.test_file_questions import UPLOAD

    path = tmp_path / "assessments.json"
    conversations_path = tmp_path / "conversations.json"
    pack = install_tools_pack(tmp_path, monkeypatch)
    tools, client = _tools_pack_tools(tmp_path, pack)
    orchestrator, response = _tools_session_at_file_question(path, tools, pack)
    response = orchestrator.submit(response.session_id, "dataset_file", UPLOAD)
    response = _answer_remaining(orchestrator, response.session_id)
    assert response.status == "review"
    orchestrator.complete(response.session_id)
    profile = orchestrator.profiles.get(pack.domain_id)
    assert profile.dataset.dataset_id == "ds_1"
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(path.parent / "profiles.json"))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(conversations_path))
    monkeypatch.setattr(streamlit_adapter, "tool_service_from_env", lambda: tools)
    app = _open_tools_domain(AppTest.from_file("app.py").run())

    delete = next(button for button in app.sidebar.button if button.label == "Ausgewähltes Assessment löschen")
    assert delete.disabled is True
    confirm = next(item for item in app.sidebar.checkbox if item.label.startswith("Auch die hochgeladenen Messdaten"))
    confirm.check().run()
    next(button for button in app.sidebar.button if button.label == "Ausgewähltes Assessment löschen").click().run()
    app.run()

    assert orchestrator.repository.list() == []
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})
    profile = orchestrator.profiles.get(pack.domain_id, "local")
    assert profile.dataset is None and profile.answers["dataset_file"].normalized_value is None
    service = ConversationService(LocalConversationRepository(conversations_path), orchestrator.profiles, FakeProvider())
    result_chat = next(c for c in service.list() if c.title == "Ergebnis")
    assert result_chat.messages[-1].content == DATASET_DELETED_REPLY
    assert isinstance(DatasetRef, type)


# The characteristics analysis of the proposal test: the volunteer situation
# read from the cash book with medium confidence, plus the annual expenses.
PROPOSAL_CHARACTERISTICS = {
    "dataset_id": "ds_1",
    "characteristics": {
        "totals": {"annual_eur": "27850.40"},
        "volunteers": {"situation": "tight", "confidence": "medium"},
    },
    "provenance": {"calculation_id": "calc-char-1", "core_version": "0.1.0", "assumptions": [], "limitations": []},
}
PROPOSAL_DERIVATION = {
    "field_key": "volunteer_situation", "tool": "analyze_characteristics", "mode": "propose",
    "pointer": "/characteristics/volunteers/situation",
    "confidence_pointer": "/characteristics/volunteers/confidence",
    "note": "Wenige Helferinnen und Helfer im Kassabuch.",
}


def test_app_shows_a_measurement_proposal_and_takes_it_over_on_click(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter
    from guideos.tools.datasets import DatasetImporter
    from tests.test_file_questions import UPLOAD
    from tests.test_measurement_derivations import AnalysisToolClient, analysis_tools

    path = tmp_path / "assessments.json"
    tools_block = {**tools_pack_data()["tools"]}
    tools_block["derive"] = [*tools_block["derive"], PROPOSAL_DERIVATION]
    pack = install_tools_pack(tmp_path, monkeypatch, tools=tools_block)
    tools, client = analysis_tools(tmp_path, pack, AnalysisToolClient(characteristics=PROPOSAL_CHARACTERISTICS))
    orchestrator = AssessmentOrchestrator(
        pack, LocalAssessmentRepository(path), profiles=LocalProfileRepository(path.parent / "profiles.json"),
        dataset_importer=DatasetImporter(tools),
    )
    response = orchestrator.start()
    orchestrator.submit(response.session_id, "dataset_available", "yes")
    response = orchestrator.submit(response.session_id, "dataset_file", UPLOAD)
    # Derived as answers: the record quality and the annual expenses.
    assert response.current_question.question_id == "club_type"
    for question_id, value in PERSONAS["sportverein_gefoerdert"][:4]:
        response = orchestrator.submit(response.session_id, question_id, value)
    assert response.current_question.question_id == "volunteer_situation"
    assert response.proposal.field_key == "volunteer_situation"  # confidence "medium" in the fake analysis
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(path))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(path.parent / "profiles.json"))
    monkeypatch.setattr(streamlit_adapter, "tool_service_from_env", lambda: tools)
    app = _open_tools_domain(AppTest.from_file("app.py").run())

    info = next(item for item in app.info if item.value.startswith("Aus deiner hochgeladenen Datei"))
    assert "**Knapp**" in info.value and "Konfidenz mittel" in info.value and "Helferinnen und Helfer" in info.value
    assert any("aus den Messdaten abgeleitet" in m.value for m in app.markdown)
    next(b for b in app.button if b.label == streamlit_adapter.PROPOSAL_BUTTON).click().run()
    app.run()

    stored = orchestrator.get(response.session_id)
    assert stored.answers["volunteer_situation"].normalized_value == "tight"
    assert stored.answers["volunteer_situation"].origin == "user"
    assert stored.answers["annual_budget_eur"].normalized_value == 27850
    assert stored.answers["annual_budget_eur"].origin == "measurement"
    assert stored.current_question.question_id == "second_file"
    assert not any(item.value.startswith("Aus deiner hochgeladenen Datei") for item in app.info)


# --- GUIDEOS_DOMAINS: the pilot offers only the listed domains ---


def test_allowed_domains_from_env_parses_a_comma_separated_list():
    from guideos.config import allowed_domains_from_env

    assert allowed_domains_from_env(None) is None
    assert allowed_domains_from_env("") is None
    assert allowed_domains_from_env("  ,  ") is None
    assert allowed_domains_from_env("verein-demo") == {"verein-demo"}
    assert allowed_domains_from_env(" verein-demo, andere-demo ,") == {"verein-demo", "andere-demo"}


def test_app_offers_only_the_domains_listed_in_guideos_domains(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setenv("GUIDEOS_DOMAINS", "fragetypen-test")
    _install_typed_pack(tmp_path, monkeypatch)
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Assessment").run()

    domain_select = next(item for item in app.selectbox if item.label == "Assessment-Domain")
    assert domain_select.options == ["Fragetypen-Test (1.0.0)"]
    assert not app.error

    monkeypatch.setenv("GUIDEOS_DOMAINS", "gibt-es-nicht")
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Assessment").run()
    assert any("GUIDEOS_DOMAINS" in item.value and "gibt-es-nicht" in item.value for item in app.error)
    assert all(item.label != "Assessment-Domain" for item in app.selectbox)


def _assessment_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    app = AppTest.from_file("app.py").run()
    _open_demo_domain(app)
    return app


def test_app_sidebar_scopes_assessments_to_the_chosen_user(tmp_path, monkeypatch):
    app = _assessment_app(tmp_path, monkeypatch)
    users = next(item for item in app.sidebar.selectbox if item.label == "Benutzer")
    assert users.options == ["local"] and users.value == "local"

    app.sidebar.text_input(key="assessment_new_user").set_value("  anna ")
    next(button for button in app.button if button.label == "Benutzer anlegen").click().run()
    app = app.run()
    users = next(item for item in app.sidebar.selectbox if item.label == "Benutzer")
    assert users.options == ["local", "anna"] and users.value == "anna"

    next(button for button in app.button if button.label == "Neues Assessment starten").click().run()
    app = app.run()
    sessions = LocalAssessmentRepository(tmp_path / "assessments.json").list()
    assert [session.participant_id for session in sessions] == ["anna"]
    assert any(item.label == "Gespeicherte Assessments" for item in app.sidebar.selectbox)

    next(item for item in app.sidebar.selectbox if item.label == "Benutzer").set_value("local").run()
    assert all(item.label != "Gespeicherte Assessments" for item in app.sidebar.selectbox)
    assert any("Starte ein neues Assessment" in item.value for item in app.info)


def test_app_sidebar_offers_users_known_from_stored_data(tmp_path, monkeypatch):
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    conversations.save(Conversation.create(
        domain_id="verein-demo", title="Alt", participant_id="bernd", profile_id="prof_b",
    ))
    app = _assessment_app(tmp_path, monkeypatch)
    users = next(item for item in app.sidebar.selectbox if item.label == "Benutzer")
    assert users.options == ["local", "bernd"]

    app.sidebar.text_input(key="assessment_new_user").set_value("   ")
    next(button for button in app.button if button.label == "Benutzer anlegen").click().run()
    assert any("Benutzername" in item.value for item in app.sidebar.warning)
    users = next(item for item in app.sidebar.selectbox if item.label == "Benutzer")
    assert users.value == "local"


def test_pilot_login_asks_for_a_user_name_and_scopes_data_by_it(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter

    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", "geheim")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(streamlit_adapter, "assessment_repository_from_env", lambda path: assessments)
    monkeypatch.setattr(
        streamlit_adapter, "conversation_repository_from_env",
        lambda path: LocalConversationRepository(tmp_path / "conversations.json"),
    )
    monkeypatch.setattr(
        streamlit_adapter, "profile_repository_from_env",
        lambda: LocalProfileRepository(tmp_path / "profiles.json"),
    )
    monkeypatch.setattr(streamlit_adapter, "PostgresPilotFeedbackRepository", lambda url: None)
    monkeypatch.setattr(
        streamlit_adapter, "knowledge_service_from_env",
        lambda provider: KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json")),
    )
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    monkeypatch.setitem(registry._PROVIDERS, "ollama", FakeAdvisoryProvider())

    app = AppTest.from_file("app.py").run()
    assert [item.label for item in app.text_input] == ["Benutzername", "Testpasswort"]

    app.text_input[1].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    assert any("Benutzername" in error.value for error in app.error)

    app.text_input[0].set_value(" test ")
    app.text_input[1].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    app = app.run()
    next(button for button in app.button if button.label == "Neues Assessment starten").click().run()
    assert assessments.list()[0].participant_id == "test"


def test_pilot_mode_has_no_user_selection(tmp_path, monkeypatch):
    import guideos.adapters.streamlit as streamlit_adapter

    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", "geheim")
    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        streamlit_adapter, "assessment_repository_from_env",
        lambda path: LocalAssessmentRepository(tmp_path / "assessments.json"),
    )
    monkeypatch.setattr(
        streamlit_adapter, "conversation_repository_from_env",
        lambda path: LocalConversationRepository(tmp_path / "conversations.json"),
    )
    monkeypatch.setattr(
        streamlit_adapter, "profile_repository_from_env",
        lambda: LocalProfileRepository(tmp_path / "profiles.json"),
    )
    monkeypatch.setattr(streamlit_adapter, "PostgresPilotFeedbackRepository", lambda url: None)
    monkeypatch.setattr(
        streamlit_adapter, "knowledge_service_from_env",
        lambda provider: KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json")),
    )
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    app = AppTest.from_file("app.py").run()
    app.text_input[0].set_value("test")
    app.text_input[1].set_value("geheim")
    next(button for button in app.button if button.label == "Anmelden").click().run()
    app = app.run()
    assert all(item.label != "Benutzer" for item in app.sidebar.selectbox)
    assert all(button.label != "Benutzer anlegen" for button in app.button)


def test_app_assigns_records_without_participant_to_the_default_user(tmp_path, monkeypatch):
    path = tmp_path / "assessments.json"
    orchestrator = AssessmentOrchestrator(
        load_domain_pack(), LocalAssessmentRepository(path),
        profiles=LocalProfileRepository(tmp_path / "profiles.json"),
    )
    response = _completed_response(orchestrator)
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    conversations.save(Conversation.create(domain_id="verein-demo", title="Alt", profile_id="p"))
    assert LocalAssessmentRepository(path).get(response.session_id).participant_id is None

    app = _assessment_app(tmp_path, monkeypatch)

    assert LocalAssessmentRepository(path).get(response.session_id).participant_id == "local"
    assert LocalProfileRepository(tmp_path / "profiles.json").get("verein-demo", "local")
    assert {c.title: c.participant_id for c in conversations.list()}["Alt"] == "local"
    users = next(item for item in app.sidebar.selectbox if item.label == "Benutzer")
    assert users.options == ["local"]
    assert any(item.label == "Gespeicherte Assessments" for item in app.sidebar.selectbox)
