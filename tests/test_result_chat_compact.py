"""Pack block result_chat: the result chat lists recommendations compactly
(title and short text, sources only as message evidence) when a pack asks
for it; the default stays the detailed rendering."""

import pytest

from guideos.assessment.models import LLMRecommendation
from guideos.domains.catalog import pack_draft_payload
from guideos.domains.loader import DomainPackError, validate_domain_pack_data
from guideos.knowledge.models import SourceRef
from guideos.reports.renderer import render_llm_recommendations, render_rule_recommendations
from guideos.conversations.service import RECOMMENDATION_OFFER
from guideos.domains.models import ResultChatSettings
from tests.test_conversation_service import RESULT_INTRO, FakeRecommendationProvider, _service

_BASE = {
    "domain_id": "chat-demo", "version": "1.0.0", "name": "Chat", "description": "Test",
    "default_locale": "de",
    "questions": [{"question_id": "q", "field_key": "q", "text": "Frage?", "field_type": "boolean",
                   "required": True}],
    "rules": [], "recommendations": [],
}


def test_result_chat_block_is_optional_and_left_out_of_drafts_while_unset():
    plain = validate_domain_pack_data(_BASE)
    assert plain.result_chat is None
    assert "result_chat" not in pack_draft_payload(plain)

    compact = validate_domain_pack_data({**_BASE, "result_chat": {"recommendations": "compact"}})
    assert compact.result_chat.recommendations == "compact"
    assert pack_draft_payload(compact)["result_chat"] == {"recommendations": "compact"}
    default = validate_domain_pack_data({**_BASE, "result_chat": {}})
    assert default.result_chat.recommendations == "detailed"

    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**_BASE, "result_chat": {"recommendations": "short"}})
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**_BASE, "result_chat": {"max_items": 3}})


def test_result_chat_texts_are_optional_and_left_out_while_unset():
    intro_only = validate_domain_pack_data({**_BASE, "result_chat": {"intro": "Fertig, hier die Empfehlungen:"}})
    assert intro_only.result_chat.recommendations == "detailed"
    assert intro_only.result_chat.intro == "Fertig, hier die Empfehlungen:"
    assert intro_only.result_chat.updated_intro is None and intro_only.result_chat.offer is None
    assert pack_draft_payload(intro_only)["result_chat"] == {
        "recommendations": "detailed", "intro": "Fertig, hier die Empfehlungen:",
    }

    compact = validate_domain_pack_data({**_BASE, "result_chat": {"recommendations": "compact"}})
    assert pack_draft_payload(compact)["result_chat"] == {"recommendations": "compact"}

    full = validate_domain_pack_data({**_BASE, "result_chat": {
        "recommendations": "compact", "intro": "A", "updated_intro": "B", "offer": "C",
    }})
    assert (full.result_chat.intro, full.result_chat.updated_intro, full.result_chat.offer) == ("A", "B", "C")
    assert pack_draft_payload(full)["result_chat"] == {
        "recommendations": "compact", "intro": "A", "updated_intro": "B", "offer": "C",
    }

    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**_BASE, "result_chat": {"intro": ""}})
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**_BASE, "result_chat": {"intro": "x" * 501}})
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**_BASE, "result_chat": {"greeting": "Hallo"}})


def test_result_conversation_uses_the_pack_texts_when_set(tmp_path):
    service, _, _ = _service(tmp_path, FakeRecommendationProvider())
    pack = service.load_pack("verein-demo", "1.0.0").model_copy(update={
        "result_chat": ResultChatSettings(
            intro="Dein Check ist fertig:", updated_intro="Neuer Stand:",
            offer="Soll ich Hinweise aus den Leitfäden ergänzen? Antworte mit Ja.",
        ),
    })
    service.load_pack = lambda domain_id, version: pack

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    stored = service.get(conversation.conversation_id)

    assert stored.messages[0].content == "Dein Check ist fertig:"
    assert stored.messages[2].content == "Soll ich Hinweise aus den Leitfäden ergänzen? Antworte mit Ja."
    assert RECOMMENDATION_OFFER not in [message.content for message in stored.messages]

    reply = service.send_message(conversation.conversation_id, "ja", "http://fake", "fake").messages[-1]
    assert reply.content.startswith("**Zusätzliche KI-Empfehlungen**")
    assert "Mitgliedsbeiträge prüfen" in reply.content


def test_result_conversation_uses_the_pack_text_after_a_newer_assessment(tmp_path):
    from guideos.assessment.models import AssessmentAnswer
    from guideos.rules.engine import evaluate_assessment

    service, session, profiles = _service(tmp_path)
    pack = service.load_pack("verein-demo", "1.0.0").model_copy(update={
        "result_chat": ResultChatSettings(updated_intro="Neuer Stand deines Checks:"),
    })
    service.load_pack = lambda domain_id, version: pack
    first = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    assert first.messages[0].content == RESULT_INTRO  # intro not overridden

    newer = session.model_copy(update={"session_id": "sess_neu"})
    newer.answers["funding_source"] = AssessmentAnswer(
        question_id="funding_source", field_key="funding_source",
        raw_answer="Wärmepumpe", normalized_value="heat_pump", confidence=1,
    )
    newer.result = evaluate_assessment(pack, newer)
    profiles.save(profiles.get("verein-demo").replace_with(newer))
    extended = service.ensure_result_conversation("verein-demo", "http://fake", "fake")

    assert extended.messages[3].content == "Neuer Stand deines Checks:"
    assert extended.messages[-1].content == RECOMMENDATION_OFFER


def _compact_pack(pack):
    from guideos.domains.models import ResultChatSettings

    return pack.model_copy(update={"result_chat": ResultChatSettings(recommendations="compact")})


def test_compact_rule_recommendations_list_title_and_text_only(tmp_path):
    service, session, _ = _service(tmp_path)
    pack = _compact_pack(service.load_pack("verein-demo", "1.0.0"))
    detailed = render_rule_recommendations(pack, session.result)
    compact = render_rule_recommendations(pack, session.result, compact=True)

    count = len(session.result.recommendations)
    assert count >= 2
    assert compact.startswith(f"**Empfehlungen aus deinen Angaben** ({count})\n\n- **")
    assert compact.count("\n- **") == count
    assert "Finanzdaten erfassen** – " in compact
    assert "Quelle:" not in compact and "Bezug:" not in compact and "Gründe:" not in compact
    assert compact.endswith("_Details und Quellen stehen im Bericht; frag mich zu einer Empfehlung._")
    assert "Quelle: GuideOS-Regel" in detailed and len(compact) < len(detailed)


def test_compact_llm_recommendations_keep_the_ai_label_and_unverified_marks(tmp_path):
    service, _, _ = _service(tmp_path)
    pack = _compact_pack(service.load_pack("verein-demo", "1.0.0"))
    items = [
        LLMRecommendation(
            recommendation_id="llm_1", title="Mitgliedsbeiträge prüfen",
            message="Lass den Abgleich prüfen.", rationale="Das spart Energie.",
            sources=[SourceRef(kind="llm", title="KI-Fachwissen")], evidence_fields=["funding_source"],
            unverified_values=["300 €"],
        ),
        LLMRecommendation(
            recommendation_id="llm_2", title="Thermostate einstellen",
            message="Stelle die Thermostate niedriger.", rationale="Begründung.",
            sources=[SourceRef(kind="llm", title="KI-Fachwissen")], evidence_fields=["funding_source"],
        ),
    ]
    compact = render_llm_recommendations(pack, items, compact=True)

    assert compact.startswith("**Zusätzliche KI-Empfehlungen** (2)\n\n- **Mitgliedsbeiträge prüfen** – ")
    assert "Lass den Abgleich prüfen." in compact and "Das spart Energie." not in compact
    assert "Ungeprüft: Geldbetrag 300 € ist durch keine Quelle belegt" in compact
    assert "Konfidenz" not in compact and "Bezug:" not in compact
    assert compact.endswith(
        "_KI-formuliert, keine GuideOS-Regel. Begründungen und Quellen stehen im Bericht; "
        "frag mich zu einer Empfehlung._"
    )
    assert render_llm_recommendations(pack, items, heading="KI-Empfehlungen", compact=True).startswith(
        "**KI-Empfehlungen** (2)")


def test_result_conversation_uses_the_compact_form_when_the_pack_asks_for_it(tmp_path):
    service, session, _ = _service(tmp_path, FakeRecommendationProvider())
    pack = _compact_pack(service.load_pack("verein-demo", "1.0.0"))
    service.load_pack = lambda domain_id, version: pack

    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    stored = service.get(conversation.conversation_id)

    assert stored.messages[0].content == RESULT_INTRO
    rules = stored.messages[1]
    assert rules.content.startswith("**Empfehlungen aus deinen Angaben**")
    assert "Ehrenamt sichern" in rules.content and "Quelle: GuideOS-Regel" not in rules.content
    # The evidence stays on the message, so the source expander still works.
    assert len(rules.sections) == len(session.result.recommendations)
    assert rules.sections[0].sources[0].kind == "rule"

    reply = service.send_message(conversation.conversation_id, "ja", "http://fake", "fake").messages[-1]
    assert reply.content.startswith("**Zusätzliche KI-Empfehlungen** (")
    assert "Mitgliedsbeiträge prüfen" in reply.content
    assert "keine GuideOS-Regel" in reply.content and "Konfidenz" not in reply.content


def test_result_conversation_stays_detailed_without_the_block(tmp_path):
    service, _, _ = _service(tmp_path)
    conversation = service.ensure_result_conversation("verein-demo", "http://fake", "fake")
    stored = service.get(conversation.conversation_id)
    assert "Quelle: GuideOS-Regel" in stored.messages[1].content
    assert "Empfehlungen aus deinen Angaben" not in stored.messages[1].content

