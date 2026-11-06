import json
import logging

import pytest

from guideos.assessment.models import AssessmentAnswer, AssessmentSession
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.context.prompt import AdvisoryAnswer
from guideos.conversations import Conversation
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import (
    DEFAULT_CONVERSATION_TITLE,
    OFFER_DECLINED_REPLY,
    RECOMMENDATION_OFFER,
    RESULT_INTRO,
    RESULT_UPDATED_INTRO,
    TITLE_MAX_CHARS,
    ConversationError,
    ConversationService,
    derive_title,
)
from guideos.domains.loader import load_domain_pack
from guideos.profiles.models import Profile
from guideos.profiles.repository import LocalProfileRepository
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore
from guideos.providers.base import ProviderError
from guideos.rules.engine import evaluate_assessment


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, sections=None, error=None):
        self.sections = sections or [{"text": "Antwort", "source_ids": []}]
        self.error = error
        self.calls = []

    def stream_chat(self, endpoint, model, messages):
        raise AssertionError("nicht erwartet")

    def generate_structured(self, endpoint, model, messages, schema):
        if schema.get("title") == "ProfileChangeProposal":
            # Statements are checked for a changed fact first; none here.
            return {"field_key": None, "value": None}
        assert schema == AdvisoryAnswer.model_json_schema()
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"sections": self.sections}


ANSWER = "Antwort  \n_Quelle: KI-Fachwissen_"


def test_create_references_the_profile_with_default_title(tmp_path):
    service, session, profiles = _service(tmp_path)

    conversation = service.create("verein-demo")

    assert isinstance(conversation, Conversation)
    assert conversation.profile_id == profiles.get("verein-demo").profile_id
    assert conversation.result_session_id is None
    assert conversation.domain_id == "verein-demo"
    assert conversation.title == "Neue Beratung"
    assert conversation.messages == []
    assert service.get(conversation.conversation_id) == conversation
    assert service.list() == [conversation]


def test_create_requires_a_profile_of_the_domain(tmp_path):
    service, session, profiles = _service(tmp_path)

    with pytest.raises(ConversationError) as excinfo:
        service.create("andere-demo")
    assert excinfo.value.code == "profile_not_found"
    with pytest.raises(ConversationError) as excinfo:
        service.ensure_result_conversation("andere-demo", "http://fake", "fake")
    assert excinfo.value.code == "profile_not_found"
    with pytest.raises(ConversationError) as excinfo:
        service.create("verein-demo", participant_id="jemand-anderes")
    assert excinfo.value.code == "profile_not_found"
    assert service.list() == []


def test_send_message_rebuilds_context_and_persists_history(tmp_path):
    service, session, _ = _service(tmp_path)
    conversation = service.create("verein-demo", title="Kassaprüfung")

    first = service.send_message(conversation.conversation_id, "Erste Frage", "http://fake", "fake")
    second = service.send_message(conversation.conversation_id, "Zweite Frage", "http://fake", "fake")

    assert [(m.role, m.content) for m in second.messages] == [
        ("user", "Erste Frage"),
        ("assistant", ANSWER),
        ("user", "Zweite Frage"),
        ("assistant", ANSWER),
    ]
    assert first.revision == 2 and second.revision == 3
    assert service.get(conversation.conversation_id) == second
    provider = service.provider
    assert len(provider.calls) == 2
    assert "DOMAIN_KNOWLEDGE" in provider.calls[1][0]["content"]
    payload = json.loads(provider.calls[1][1]["content"])
    assert "score" not in payload["rule_results"]
    assert payload["rule_results"]["reason_codes"]
    assert {f["field_key"] for f in payload["verified_user_context"]["facts"]} >= {"funding_source"}
    assert [(m["role"], m["content"]) for m in provider.calls[1][2:]] == [
        ("user", "Erste Frage"),
        ("assistant", "Antwort"),
        ("user", "Zweite Frage"),
    ]


def test_first_question_names_a_conversation_with_the_default_title(tmp_path):
    """A conversation created without a title ("Neue Beratung") takes its title
    deterministically from the first user message, shortened at a word boundary
    (decision of 2026-11-03); explicit titles, the "Ergebnis" chat and later
    messages never rename it."""
    service, _, _ = _service(tmp_path)
    conversation = service.create("verein-demo")
    assert conversation.title == DEFAULT_CONVERSATION_TITLE

    short = service.send_message(conversation.conversation_id, "  Wie hoch war meine Spitze?  ", "http://fake", "fake")
    assert short.title == "Wie hoch war meine Spitze?"
    again = service.send_message(conversation.conversation_id, "Und im März?", "http://fake", "fake")
    assert again.title == "Wie hoch war meine Spitze?"
    assert service.get(conversation.conversation_id).title == "Wie hoch war meine Spitze?"

    long_question = "Wie hoch war meine Spitze im März und was kostet mich das nach dem neuen Tarif?"
    other = service.create("verein-demo")
    named = service.send_message(other.conversation_id, long_question, "http://fake", "fake")
    assert named.title == "Wie hoch war meine Spitze im März und …"      # "… und was" would be 41 characters
    assert len(named.title) <= TITLE_MAX_CHARS + 2

    unbroken = service.create("verein-demo")
    assert service.send_message(unbroken.conversation_id, "x" * 60, "http://fake", "fake").title == "x" * TITLE_MAX_CHARS + " …"

    explicit = service.create("verein-demo", title="Kassaprüfung")
    assert service.send_message(explicit.conversation_id, "Erste Frage", "http://fake", "fake").title == "Kassaprüfung"


def test_derive_title_trims_at_a_word_boundary():
    assert derive_title("Kurz") == "Kurz"
    assert derive_title("Mehrere   Leerzeichen\nund Zeilen") == "Mehrere Leerzeichen und Zeilen"
    assert derive_title("a" * TITLE_MAX_CHARS) == "a" * TITLE_MAX_CHARS
    assert derive_title("a" * TITLE_MAX_CHARS + " b") == "a" * TITLE_MAX_CHARS + " …"
    assert derive_title("Ein Satz, der deutlich länger als vierzig Zeichen ist, oder?") == "Ein Satz, der deutlich länger als …"


def test_two_conversations_share_context_but_not_history(tmp_path):
    service, session, _ = _service(tmp_path)
    first = service.create("verein-demo")
    second = service.create("verein-demo")

    service.send_message(first.conversation_id, "Nur in A", "http://fake", "fake")
    service.send_message(second.conversation_id, "Nur in B", "http://fake", "fake")

    calls = service.provider.calls
    assert calls[0][1] == calls[1][1]
    assert [m["content"] for m in calls[1][2:]] == ["Nur in B"]
    assert [m.content for m in service.get(second.conversation_id).messages] == ["Nur in B", ANSWER]


def test_send_message_rejects_empty_text_without_persisting(tmp_path):
    service, session, _ = _service(tmp_path)
    conversation = service.create("verein-demo")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "   ", "http://fake", "fake")

    assert excinfo.value.code == "required"
    assert service.get(conversation.conversation_id).messages == []
    assert service.provider.calls == []


def test_provider_failure_leaves_conversation_unchanged(tmp_path):
    service, session, _ = _service(tmp_path, provider=FakeProvider(error=ProviderError("Ollama weg")))
    conversation = service.create("verein-demo")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")

    assert excinfo.value.code == "provider_error"
    assert "Ollama weg" in str(excinfo.value)
    assert service.get(conversation.conversation_id).messages == []


def test_send_message_rejects_changed_domain_pack(tmp_path):
    service, session, profiles = _service(tmp_path)
    conversation = service.create("verein-demo")
    profile = profiles.get("verein-demo")
    profile.domain_content_hash = "b" * 64
    profiles.save(profile)

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")

    assert excinfo.value.code == "domain_version_unavailable"


def test_send_message_rejects_missing_or_recreated_profile(tmp_path):
    service, session, profiles = _service(tmp_path)
    conversation = service.create("verein-demo")
    profiles.delete("verein-demo")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")
    assert excinfo.value.code == "profile_unavailable"

    # A profile created anew has another id: the old chat does not attach to it.
    profiles.save(Profile.from_session(session))
    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")
    assert excinfo.value.code == "profile_unavailable"


def test_delete_removes_conversation(tmp_path):
    service, session, _ = _service(tmp_path)
    conversation = service.create("verein-demo")

    service.delete(conversation.conversation_id)

    assert service.list() == []
    with pytest.raises(LookupError):
        service.get(conversation.conversation_id)


_NO_MATCH_ANSWERS = {
    "club_type": "sports",
    "founded_year": 2015,
    "members_count": 120,
    "funding_source": "membership_fees",
    "volunteer_situation": "sufficient",
    "annual_budget_eur": 6000,
}


def _service(tmp_path, provider=None, answers=None, knowledge=None):
    pack = load_domain_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    session = AssessmentSession.create(
        domain_id=pack.domain_id,
        domain_version=pack.version,
        domain_content_hash=pack.content_hash,
        locale="de",
    )
    for field_key, value in (answers or {
        "club_type": "sports",
        "founded_year": 1980,
        "members_count": 120,
        "funding_source": "public_grants",
        "volunteer_situation": "tight",
        "annual_budget_eur": None,
    }).items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key,
            field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value),
            normalized_value=value,
            confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    assessments.save(session)
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(Profile.from_session(session))
    service = ConversationService(
        LocalConversationRepository(tmp_path / "conversations.json"),
        profiles,
        provider or FakeProvider(),
        load_pack=lambda domain_id, version: pack,
        knowledge=knowledge,
    )
    return service, session, profiles


def test_delete_for_profile_removes_only_that_profiles_conversations(tmp_path):
    service, session, profiles = _service(tmp_path)
    other = Conversation.create(domain_id="verein-demo", title="C", profile_id="prof_other")
    service.conversations.save(other)
    first = service.create("verein-demo", title="A")
    second = service.create("verein-demo", title="B")
    profile_id = profiles.get("verein-demo").profile_id

    removed = service.delete_for_profile(profile_id)

    assert sorted(removed) == sorted([first.conversation_id, second.conversation_id])
    assert [c.conversation_id for c in service.list()] == [other.conversation_id]
    assert service.delete_for_profile(profile_id) == []


def test_context_is_rebuilt_from_current_profile_state(tmp_path):
    service, session, profiles = _service(tmp_path)
    conversation = service.create("verein-demo")

    service.send_message(conversation.conversation_id, "Erste Frage", "http://fake", "fake")
    current = profiles.get("verein-demo")
    current.answers["funding_source"].normalized_value = "membership_fees"
    current.answers["funding_source"].origin = "chat_update"
    profiles.save(current)
    service.send_message(conversation.conversation_id, "Zweite Frage", "http://fake", "fake")

    first_payload, second_payload = (json.loads(call[1]["content"]) for call in service.provider.calls)
    funding = lambda payload: next(  # noqa: E731
        f["normalized_value"] for f in payload["verified_user_context"]["facts"] if f["field_key"] == "funding_source"
    )
    assert funding(first_payload) == "public_grants"
    assert funding(second_payload) == "membership_fees"


class FakeRecommendationProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.structured_calls = 0

    def generate_structured(self, endpoint, model, messages, schema):
        if "recommendations" not in schema["properties"]:
            return super().generate_structured(endpoint, model, messages, schema)
        self.structured_calls += 1
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


def test_result_conversation_seeds_result_and_recommendation_offer(tmp_path):
    service, session, _ = _service(tmp_path)

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    stored = service.get(conversation.conversation_id)
    assert stored.title == "Ergebnis"
    assert [m.role for m in stored.messages] == ["assistant", "assistant", "assistant"]
    assert stored.messages[0].content == RESULT_INTRO
    assert "Ehrenamt sichern" in stored.messages[1].content
    assert "Bezug: Wie schätzt du die Lage bei den Ehrenamtlichen ein? (Knapp)" in stored.messages[1].content
    assert "75/100" not in stored.messages[1].content
    assert stored.messages[2].content == RECOMMENDATION_OFFER


def test_result_conversation_without_rule_recommendations_delivers_llm_ones(tmp_path):
    provider = FakeRecommendationProvider()
    service, session, _ = _service(tmp_path, provider, answers=_NO_MATCH_ANSWERS)
    assert session.result.recommendations == []

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    stored = service.get(conversation.conversation_id)
    assert [m.role for m in stored.messages] == ["assistant", "assistant"]
    assert stored.messages[0].content == RESULT_INTRO
    assert stored.messages[1].content.startswith("**KI-Empfehlungen**")
    assert "Zusätzliche" not in stored.messages[1].content
    assert "Mitgliedsbeiträge prüfen" in stored.messages[1].content
    assert "keine GuideOS-Regel" in stored.messages[1].content
    # The immediately generated set is a model answer and carries the model like every other.
    assert stored.messages[0].model is None
    assert stored.messages[1].model == "fake"
    assert provider.structured_calls == 1


def test_result_conversation_fails_cleanly_when_llm_fallback_is_unavailable(tmp_path):
    provider = FakeRecommendationProvider()
    provider.generate_structured = lambda *args, **kwargs: (_ for _ in ()).throw(
        ProviderError("Ollama nicht erreichbar.")
    )
    service, session, _ = _service(tmp_path, provider, answers=_NO_MATCH_ANSWERS)

    with pytest.raises(ConversationError) as excinfo:
        service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    assert excinfo.value.code == "provider_error"
    assert service.list() == []


def test_result_conversation_is_created_once_and_extended_after_a_newer_assessment(tmp_path):
    service, session, profiles = _service(tmp_path)

    first = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    again = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    assert again.conversation_id == first.conversation_id
    assert len(service.list()) == 1
    assert again.result_session_id == session.session_id
    assert len(again.messages) == 3

    newer = session.model_copy(update={"session_id": "sess_neu"})
    newer.answers["funding_source"] = AssessmentAnswer(
        question_id="funding_source", field_key="funding_source",
        raw_answer="Mitgliedsbeiträge", normalized_value="membership_fees", confidence=1,
    )
    newer.result = evaluate_assessment(load_domain_pack(), newer)
    profiles.save(profiles.get("verein-demo").replace_with(newer))
    extended = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    assert extended.conversation_id == first.conversation_id
    assert extended.result_session_id == "sess_neu"
    assert extended.messages[3].content == RESULT_UPDATED_INTRO
    assert "Ehrenamt sichern" in extended.messages[4].content
    assert extended.messages[-1].content == RECOMMENDATION_OFFER
    assert len(service.list()) == 1


def test_affirmative_reply_to_offer_returns_llm_recommendations(tmp_path):
    provider = FakeRecommendationProvider()
    service, session, _ = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    updated = service.send_message(conversation.conversation_id, "Ja, bitte!", "http://fake", "fake")

    assert [m.role for m in updated.messages] == [
        "assistant", "assistant", "assistant", "user", "assistant",
    ]
    assert updated.messages[-2].content == "Ja, bitte!"
    assert "Mitgliedsbeiträge prüfen" in updated.messages[-1].content
    assert "keine GuideOS-Regel" in updated.messages[-1].content
    assert "Bezug: Woher stammt der Großteil der Einnahmen?" in updated.messages[-1].content
    assert provider.structured_calls == 1
    assert provider.calls == []


def test_negative_reply_to_offer_is_answered_without_llm(tmp_path):
    provider = FakeProvider()
    service, session, _ = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    updated = service.send_message(conversation.conversation_id, "Nein danke.", "http://fake", "fake")

    assert updated.messages[-1].content == OFFER_DECLINED_REPLY
    assert provider.calls == []


def test_free_reply_to_offer_goes_to_the_advisory_llm(tmp_path):
    provider = FakeProvider()
    service, session, _ = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    updated = service.send_message(
        conversation.conversation_id, "Ja, aber nur zu den Finanzen bitte.", "http://fake", "fake"
    )

    assert updated.messages[-1].content == ANSWER
    assert len(provider.calls) == 1
    assert [m["role"] for m in provider.calls[0][2:]] == [
        "assistant", "assistant", "assistant", "user",
    ]


def test_offer_shortcut_only_applies_directly_after_the_offer(tmp_path):
    provider = FakeRecommendationProvider()
    service, session, _ = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    service.send_message(conversation.conversation_id, "Was kostet das?", "http://fake", "fake")

    updated = service.send_message(conversation.conversation_id, "ja", "http://fake", "fake")

    assert updated.messages[-1].content == ANSWER
    assert provider.structured_calls == 0
    assert len(provider.calls) == 2


def test_provider_failure_on_offer_accept_leaves_conversation_unchanged(tmp_path):
    provider = FakeRecommendationProvider()
    provider.generate_structured = lambda *args, **kwargs: (_ for _ in ()).throw(
        ProviderError("Ollama nicht erreichbar.")
    )
    service, session, _ = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "ja", "http://fake", "fake")

    assert excinfo.value.code == "provider_error"
    assert len(service.get(conversation.conversation_id).messages) == 3


def test_conversation_events_are_logged_without_content(tmp_path, caplog):
    provider = FakeRecommendationProvider()
    service, session, _ = _service(tmp_path, provider)

    with caplog.at_level(logging.INFO, logger="guideos"):
        result = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
        service.send_message(result.conversation_id, "ja", "http://fake", "fake")
        service.send_message(
            result.conversation_id, "Vertrauliche Frage zur Kassaprüfung", "http://fake", "fake"
        )
        manual = service.create("verein-demo", title="Kassaprüfung")

    messages = [
        r.getMessage() for r in caplog.records if r.name == "guideos.conversations.service"
    ]
    result_id = result.conversation_id
    assert any(
        f"conversation_created conversation_id={result_id}" in m
        and f"profile_id={result.profile_id}" in m
        and "kind=result" in m
        for m in messages
    )
    assert any(
        f"conversation_created conversation_id={manual.conversation_id}" in m
        and "kind=manual" in m
        for m in messages
    )
    assert any(
        f"message_sent conversation_id={result_id}" in m and "answer=llm_recommendations" in m
        for m in messages
    )
    assert any(
        f"message_sent conversation_id={result_id}" in m and "answer=advisory" in m
        for m in messages
    )
    assert not any("Vertrauliche" in m or "Kassaprüfung" in m or "Antwort" in m for m in messages)


def _knowledge(tmp_path):
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))
    document = knowledge.add_document(
        "verein-demo", "leitfaden.md",
        "# Förderantrag\n\nDie Anschaffung von Vereinsausstattung wird gefördert.".encode(),
        "http://fake",
    )
    return knowledge, document


def test_advisory_answer_cites_knowledge_passages_per_section(tmp_path):
    knowledge, document = _knowledge(tmp_path)
    provider = FakeProvider(
        sections=[
            {"text": "Die Vereinsausstattung wird gefördert.", "source_ids": ["D1", "funding_source"]},
            {"text": "Plane auch eine Beitragsanpassung ein.", "source_ids": ["D9"]},
            {"text": "  Das Ehrenamt solltest du sichern. ", "source_ids": ["volunteer-recruiting"]},
        ]
    )
    service, session, _ = _service(tmp_path, provider, knowledge=knowledge)
    conversation = service.create("verein-demo")

    updated = service.send_message(
        conversation.conversation_id, "Wird die Vereinsausstattung gefördert?", "http://fake", "fake"
    )

    payload = json.loads(provider.calls[0][1]["content"])
    assert [p["source_id"] for p in payload["knowledge_sources"]] == ["D1"]
    assert "Vereinsausstattung" in payload["knowledge_sources"][0]["text"]
    answer = updated.messages[-1]
    assert [(s.text, [src.kind for src in s.sources]) for s in answer.sections] == [
        ("Die Vereinsausstattung wird gefördert.", ["document", "user_context"]),
        ("Plane auch eine Beitragsanpassung ein.", ["llm"]),
        ("Das Ehrenamt solltest du sichern.", ["rule"]),
    ]
    cited = answer.sections[0].sources[0]
    assert (cited.title, cited.locator, cited.reference) == (
        "Förderantrag", "Förderantrag", document.document_id
    )
    assert cited.content_hash == document.content_hash and "Vereinsausstattung" in cited.excerpt
    assert answer.content == (
        "Die Vereinsausstattung wird gefördert.  \n"
        "_Quelle: Dokument „Förderantrag“, Förderantrag"
        " | Deine Angabe „Woher stammt der Großteil der Einnahmen?“_\n\n"
        "Plane auch eine Beitragsanpassung ein.  \n"
        "_Quelle: KI-Fachwissen_\n\n"
        "Das Ehrenamt solltest du sichern.  \n"
        "_Quelle: GuideOS-Regel „Ehrenamt sichern“_"
    )
    assert service.get(conversation.conversation_id) == updated


def test_history_is_sent_without_rendered_source_lines(tmp_path):
    service, session, _ = _service(tmp_path)
    conversation = service.create("verein-demo")
    service.send_message(conversation.conversation_id, "Erste Frage", "http://fake", "fake")

    service.send_message(conversation.conversation_id, "Zweite Frage", "http://fake", "fake")

    history = service.provider.calls[1][2:]
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Erste Frage"),
        ("assistant", "Antwort"),
        ("user", "Zweite Frage"),
    ]


def test_malformed_advisory_answer_leaves_conversation_unchanged(tmp_path):
    provider = FakeProvider(sections=[{"text": "", "source_ids": []}])
    service, session, _ = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")

    assert excinfo.value.code == "provider_error"
    assert "nicht korrekt strukturiert" in str(excinfo.value)
    assert service.get(conversation.conversation_id).messages == []


def test_unavailable_knowledge_base_is_reported_with_a_stable_code(tmp_path):
    (tmp_path / "knowledge.json").write_text("kaputt", encoding="utf-8")
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))
    service, session, _ = _service(tmp_path, knowledge=knowledge)
    conversation = service.create("verein-demo")

    with pytest.raises(ConversationError) as excinfo:
        service.send_message(conversation.conversation_id, "Frage", "http://fake", "fake")

    assert excinfo.value.code == "knowledge_unavailable"
    assert service.provider.calls == []
    assert service.get(conversation.conversation_id).messages == []


def test_result_conversation_carries_structured_sources_for_recommendations(tmp_path):
    knowledge, _ = _knowledge(tmp_path)
    provider = FakeRecommendationProvider()
    service, session, _ = _service(tmp_path, provider, knowledge=knowledge)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    updated = service.send_message(conversation.conversation_id, "ja", "http://fake", "fake")

    intro, rules, offer = updated.messages[:3]
    assert intro.sections == [] and offer.sections == []
    assert [s.sources[0].reference for s in rules.sections] == [
        r.recommendation_id for r in session.result.recommendations
    ]
    envelope = next(s for s in rules.sections if s.sources[0].reference == "volunteer-recruiting")
    assert envelope.text == "Ehrenamt sichern: " + next(
        r.message for r in session.result.recommendations
        if r.recommendation_id == "volunteer-recruiting"
    )
    assert [(src.kind, src.reference) for src in envelope.sources] == [
        ("rule", "volunteer-recruiting"),
        ("user_context", "volunteer_situation"),
    ]
    [llm_section] = updated.messages[-1].sections
    assert llm_section.text.startswith("Mitgliedsbeiträge prüfen: ")
    assert [(src.kind, src.reference) for src in llm_section.sources] == [
        ("llm", None),
        ("user_context", "funding_source"),
    ]


def test_numbers_missing_from_the_cited_source_are_marked_as_unverified(tmp_path):
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))
    knowledge.add_document(
        "verein-demo", "leitfaden.md",
        "# Förderung\n\nKleine Vereine erhalten bis zu 100 Prozent Zuschuss.".encode(),
        "http://fake",
    )
    provider = FakeProvider(
        sections=[
            {"text": "Es gibt bis zu 100 % Zuschuss für deinen Verein von 1980.", "source_ids": ["D1"]},
            {"text": "Der Zuschuss beträgt höchstens 7500 Euro.", "source_ids": ["D1"]},
            {"text": "Bei 3000 Besuchern lohnt sich das.", "source_ids": ["D1"]},
            {"text": "Meist sind es 30 Prozent.", "source_ids": []},
        ]
    )
    service, session, _ = _service(tmp_path, provider, knowledge=knowledge)
    conversation = service.create("verein-demo")

    updated = service.send_message(
        conversation.conversation_id, "Welche Förderung und welchen Zuschuss gibt es bei 3000 Besuchern?", "http://fake", "fake"
    )

    answer = updated.messages[-1]
    assert [section.unverified_values for section in answer.sections] == [[], ["7500"], [], []]
    assert (
        "Der Zuschuss beträgt höchstens 7500 Euro.  \n"
        "_Quelle: Dokument „Förderung“, Förderung_  \n"
        "_Ungeprüft: Die Zahlenangabe 7500 steht nicht in der zitierten Quelle._"
    ) in answer.content
    assert answer.content.count("Ungeprüft") == 1
    assert service.get(conversation.conversation_id) == updated


def test_money_amounts_from_the_models_own_knowledge_are_marked_as_unverified(tmp_path):
    knowledge = KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"))
    knowledge.add_document(
        "verein-demo", "leitfaden.md",
        "# Kosten\n\nEine Vereinsberatung kostet höchstens 120 Euro.".encode(),
        "http://fake",
    )
    provider = FakeProvider(
        sections=[
            {"text": "LED-Lampen kosten 2 € bis 10 € pro Stück.", "source_ids": []},
            {"text": "Kürze die Sitzungen auf 60 Minuten, das spart rund 5 %.", "source_ids": []},
            {"text": "Eine Beratung kostet höchstens 120 Euro.", "source_ids": []},
            {"text": "Bei deinem Budget von 900 Euro geht das.", "source_ids": []},
        ]
    )
    service, session, _ = _service(tmp_path, provider, knowledge=knowledge)
    conversation = service.create("verein-demo")

    updated = service.send_message(
        conversation.conversation_id, "Was kostet eine Beratung? Ich habe 900 Euro.", "http://fake", "fake"
    )

    answer = updated.messages[-1]
    # Only money amounts are checked without a citation; amounts found in a retrieved
    # passage or in the question count as known.
    assert [section.unverified_values for section in answer.sections] == [["2", "10"], [], [], []]
    assert (
        "LED-Lampen kosten 2 € bis 10 € pro Stück.  \n"
        "_Quelle: KI-Fachwissen_  \n"
        "_Ungeprüft: Die Geldbeträge 2, 10 sind durch keine Quelle belegt._"
    ) in answer.content
    assert answer.content.count("Ungeprüft") == 1


def test_amounts_from_the_models_earlier_turns_do_not_verify_later_ones(tmp_path):
    provider = FakeProvider(
        sections=[{"text": "LED-Lampen kosten 2 € bis 10 € pro Stück.", "source_ids": []}]
    )
    service, session, _ = _service(tmp_path, provider)
    conversation = service.create("verein-demo")
    service.send_message(
        conversation.conversation_id, "Was kosten LED-Lampen? Mein Budget sind 50 Euro.", "http://fake", "fake"
    )
    provider.sections = [
        {"text": "Bei 2 € pro Lampe reichen deine 50 Euro für 25 Stück.", "source_ids": []}
    ]

    updated = service.send_message(conversation.conversation_id, "Wie viele brauche ich?", "http://fake", "fake")

    # 50 Euro comes from the user's earlier question, 2 € only from the model's own earlier answer.
    assert [section.unverified_values for section in updated.messages[-1].sections] == [["2"]]


def test_affirmative_reply_persists_recommendations_on_the_profile(tmp_path):
    provider = FakeRecommendationProvider()
    service, session, profiles = _service(tmp_path, provider)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    service.send_message(conversation.conversation_id, "ja", "http://fake", "fake")

    stored = profiles.get("verein-demo")
    assert [r.recommendation_id for r in stored.result.knowledge_recommendations] == ["fee-review"]
    assert stored.result.knowledge_generation.model == "fake"
    assert provider.structured_calls == 1


def test_advisory_sections_are_cleaned_of_field_keys_and_passage_ids(tmp_path):
    provider = FakeProvider(sections=[{
        "text": "Ihre Förderungen (funding_source = public_grants) im Verein von 1980 (founded_year = 1980) sind unsicher (vgl. D1).",
        "source_ids": ["funding_source"],
    }])
    service, session, _ = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    updated = service.send_message(conversation.conversation_id, "Wie sicher sind unsere Förderungen?", "http://fake", "fake")

    section = updated.messages[-1].sections[0]
    assert section.text == "Ihre Förderungen im Verein von 1980 sind unsicher."
    assert updated.messages[-1].content.startswith("Ihre Förderungen im Verein von 1980 sind unsicher.")
    assert [s.kind for s in section.sources] == ["user_context", "llm"]


def test_result_conversation_is_created_once_under_concurrent_calls(tmp_path):
    """Two requests at the same time (the browser's result page and chat
    page) must not create two "Ergebnis" chats; the service serialises the
    check-and-create."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    service, _, _ = _service(tmp_path)
    original_list = service.conversations.list

    def slow_list(*args, **kwargs):
        result = original_list(*args, **kwargs)
        time.sleep(0.05)
        return result

    service.conversations.list = slow_list

    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: service.ensure_result_conversation("verein-demo", "http://fake", "fake"), range(4)))

    assert len({c.conversation_id for c in created}) == 1
    assert [c.title for c in original_list()] == ["Ergebnis"]


def test_llm_answers_record_the_model_used(tmp_path, caplog):
    """Comparing models needs to know which one answered: the assistant message
    of an LLM answer carries the model, deterministic messages and user messages
    do not, and the message_sent log line names it (never the content)."""
    service, session, _ = _service(tmp_path)
    conversation = service.create("verein-demo", title="Kassaprüfung")

    with caplog.at_level(logging.INFO, logger="guideos"):
        answered = service.send_message(conversation.conversation_id, "Erste Frage", "http://fake", "fake-large")

    user, assistant = answered.messages
    assert user.model is None
    assert assistant.model == "fake-large"
    assert service.get(conversation.conversation_id).messages[1].model == "fake-large"
    assert any(
        "message_sent" in r.getMessage() and "model=fake-large" in r.getMessage()
        for r in caplog.records if r.name == "guideos.conversations.service"
    )
