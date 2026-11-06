"""Changing profile answers in the advisory chat (concept step 3): the
model only proposes, GuideOS validates, asks for confirmation, applies,
asks open follow-up questions and offers to regenerate the knowledge
recommendations."""

import json

from guideos.assessment.models import AssessmentAnswer, AssessmentSession, KnowledgeGeneration, LLMRecommendation
from guideos.context.prompt import AdvisoryAnswer
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import (
    CHANGE_DISCARDED_REPLY,
    REGENERATE_OFFER,
    ConversationService,
)
from guideos.domains.loader import load_domain_pack
from guideos.knowledge.models import SourceRef
from guideos.profiles.interpreter import ProfileChangeProposal
from guideos.profiles.models import Profile
from guideos.profiles.repository import LocalProfileRepository
from guideos.rules.engine import evaluate_assessment

from tests.test_profile_service import visibility_pack

DEMO_ANSWERS = {
    "club_type": "sports", "founded_year": 1980, "members_count": 120,
    "funding_source": "public_grants", "volunteer_situation": "tight", "annual_budget_eur": None,
}
# The visibility pack of test_profile_service: a club with rented premises,
# so the follow-up question about the clubhouse is hidden at first.
VISIBILITY_ANSWERS = {
    "club_type": "sports", "premises": "rented", "funding_source": "public_grants", "annual_budget_eur": 18000,
}


class FakeProvider:
    """Answers each structured schema: change proposals, advisory answers
    and recommendation batches."""

    name = "fake"
    display_name = "Fake"
    default_endpoint = "http://fake"
    default_model = "fake"

    def __init__(self, proposal=None):
        self.proposal = proposal
        self.calls = []

    def stream_chat(self, endpoint, model, messages):
        yield ""

    def generate_structured(self, endpoint, model, messages, schema):
        self.calls.append((schema.get("title"), messages))
        if schema.get("title") == ProfileChangeProposal.__name__:
            return self.proposal or {"field_key": None, "value": None}
        if schema == AdvisoryAnswer.model_json_schema():
            return {"sections": [{"text": "Beratungsantwort", "source_ids": []}]}
        return {"recommendations": [{
            "recommendation_id": "fee-schedule", "title": "Beitragsordnung überarbeiten",
            "message": "Gestaffelte Beiträge prüfen.", "rationale": "Mitgliedsbeiträge.",
            "evidence_fields": ["funding_source"], "confidence": 0.8,
        }]}


def _service(tmp_path, provider, pack=None, answers=None, with_knowledge=True):
    pack = pack or load_domain_pack()
    session = AssessmentSession.create(
        domain_id=pack.domain_id, domain_version=pack.version,
        domain_content_hash=pack.content_hash, locale="de",
    )
    for field_key, value in (answers or DEMO_ANSWERS).items():
        session.answers[field_key] = AssessmentAnswer(
            question_id=field_key, field_key=field_key,
            raw_answer="unbekannt" if value is None else str(value), normalized_value=value, confidence=1,
        )
    session.status = "completed"
    session.result = evaluate_assessment(pack, session)
    if with_knowledge:
        session.result.knowledge_recommendations = [LLMRecommendation(
            recommendation_id="fee-review", title="Mitgliedsbeiträge prüfen",
            message="Verringert die Abhängigkeit von Förderungen.", rationale="Förderabhängig.",
            evidence_fields=["funding_source"],
            sources=[SourceRef(kind="llm", title="KI-Fachwissen")],
        )]
        session.result.knowledge_generation = KnowledgeGeneration(model="fake", prompt_version=2)
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    profiles.save(Profile.from_session(session))
    service = ConversationService(
        LocalConversationRepository(tmp_path / "conversations.json"), profiles, provider,
        load_pack=lambda domain_id, version: pack,
    )
    return service, profiles


def _send(service, conversation, text):
    return service.send_message(conversation.conversation_id, text, "http://fake", "fake").messages[-1]


def test_change_is_proposed_confirmed_applied_and_recommendations_offered(tmp_path):
    provider = FakeProvider({"field_key": "funding_source", "value": "membership_fees"})
    service, profiles = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    asked = _send(service, conversation, "Wir finanzieren uns inzwischen vor allem über Mitgliedsbeiträge.")

    assert "Woher stammt der Großteil der Einnahmen?" in asked.content
    assert "Öffentliche Förderungen" in asked.content and "Mitgliedsbeiträge" in asked.content
    assert "Ja" in asked.content and "Nein" in asked.content
    assert profiles.get("verein-demo").answers["funding_source"].normalized_value == "public_grants"
    assert service.get(conversation.conversation_id).pending.kind == "confirm_change"

    applied = _send(service, conversation, "ja")

    profile = profiles.get("verein-demo")
    assert profile.answers["funding_source"].normalized_value == "membership_fees"
    assert profile.answers["funding_source"].origin == "chat_update"
    assert profile.answers["funding_source"].raw_answer == "Wir finanzieren uns inzwischen vor allem über Mitgliedsbeiträge."
    assert "Übernommen" in applied.content and "Mitgliedsbeiträge" in applied.content
    assert "neu berechnet" in applied.content
    assert "Ehrenamt sichern" in applied.content
    assert applied.sections and applied.sections[0].sources[0].kind == "rule"
    assert applied.content.rstrip().endswith(REGENERATE_OFFER)
    assert service.get(conversation.conversation_id).pending.kind == "regenerate_offer"
    assert profile.knowledge_stale is True

    regenerated = _send(service, conversation, "ja")

    profile = profiles.get("verein-demo")
    assert "Beitragsordnung überarbeiten" in regenerated.content
    assert [r.recommendation_id for r in profile.result.knowledge_recommendations] == ["fee-schedule"]
    assert profile.knowledge_stale is False
    assert service.get(conversation.conversation_id).pending is None
    assert [kind for kind, _ in provider.calls] == ["ProfileChangeProposal", "LLMRecommendationBatch"]


def test_change_can_be_declined_and_other_replies_are_advisory_questions(tmp_path):
    provider = FakeProvider({"field_key": "funding_source", "value": "membership_fees"})
    service, profiles = _service(tmp_path, provider)
    conversation = service.create("verein-demo")
    _send(service, conversation, "Wir finanzieren uns inzwischen vor allem über Mitgliedsbeiträge.")

    declined = _send(service, conversation, "nein")

    assert declined.content == CHANGE_DISCARDED_REPLY
    assert profiles.get("verein-demo").answers["funding_source"].normalized_value == "public_grants"
    assert service.get(conversation.conversation_id).pending is None

    _send(service, conversation, "Wir finanzieren uns inzwischen vor allem über Mitgliedsbeiträge.")
    other = _send(service, conversation, "Was kostet so eine Vereinsberatung?")

    assert other.content.startswith("Beratungsantwort")
    assert service.get(conversation.conversation_id).pending is None
    assert profiles.get("verein-demo").answers["funding_source"].normalized_value == "public_grants"


def test_questions_skip_the_change_interpreter_and_no_proposal_means_advice(tmp_path):
    provider = FakeProvider({"field_key": "funding_source", "value": "membership_fees"})
    service, _ = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    answer = _send(service, conversation, "Lohnt sich ein Dachverband für uns?")
    assert answer.content.startswith("Beratungsantwort")
    assert [kind for kind, _ in provider.calls] == ["AdvisoryAnswer"]

    provider.proposal = {"field_key": None, "value": None}
    answer = _send(service, conversation, "Ich überlege, die Beiträge zu erhöhen.")
    assert answer.content.startswith("Beratungsantwort")
    assert [kind for kind, _ in provider.calls][1:] == ["ProfileChangeProposal", "AdvisoryAnswer"]
    assert service.get(conversation.conversation_id).pending is None


def test_invalid_proposal_is_answered_with_the_validation_message_without_pending(tmp_path):
    provider = FakeProvider({"field_key": "founded_year", "value": 1500})
    service, profiles = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    reply = _send(service, conversation, "Der Verein ist von 1500.")

    assert "mindestens" in reply.content
    assert service.get(conversation.conversation_id).pending is None
    assert profiles.get("verein-demo").answers["founded_year"].normalized_value == 1980


def test_follow_up_questions_are_asked_one_per_message_before_the_offer(tmp_path):
    pack = visibility_pack()
    provider = FakeProvider({"field_key": "premises", "value": "owned"})
    service, profiles = _service(tmp_path, provider, pack=pack, answers=VISIBILITY_ANSWERS)
    conversation = service.create("verein-raeume-demo")
    _send(service, conversation, "Wir haben das Vereinshaus jetzt gekauft, die Räume gehören uns.")

    applied = _send(service, conversation, "ja")

    assert "Übernommen" in applied.content
    assert "Wie alt ist" in applied.content or "Vereinshaus" in applied.content
    pending = service.get(conversation.conversation_id).pending
    assert pending.kind == "answer_question" and pending.field_key == "premises_age_years"
    assert REGENERATE_OFFER not in applied.content

    rejected = _send(service, conversation, "keine Ahnung, sehr alt")
    assert service.get(conversation.conversation_id).pending.kind == "answer_question"
    assert rejected.content.startswith("Das habe ich nicht als Antwort erkannt.")
    assert "Vereinshaus" in rejected.content

    answered = _send(service, conversation, "25")

    profile = profiles.get("verein-raeume-demo")
    assert profile.answers["premises"].normalized_value == "owned"
    assert profile.answers["premises_age_years"].normalized_value == 25
    assert profile.answers["premises_age_years"].origin == "chat_update"
    assert answered.content.rstrip().endswith(REGENERATE_OFFER)
    assert service.get(conversation.conversation_id).pending.kind == "regenerate_offer"


def test_without_knowledge_recommendations_no_regeneration_is_offered(tmp_path):
    provider = FakeProvider({"field_key": "funding_source", "value": "membership_fees"})
    service, _ = _service(tmp_path, provider, with_knowledge=False)
    conversation = service.create("verein-demo")
    _send(service, conversation, "Wir leben jetzt von Mitgliedsbeiträgen.")

    applied = _send(service, conversation, "ja")

    assert REGENERATE_OFFER not in applied.content
    assert service.get(conversation.conversation_id).pending is None


def test_change_interpreter_receives_questions_and_current_answers_only(tmp_path):
    provider = FakeProvider({"field_key": "funding_source", "value": "membership_fees"})
    service, _ = _service(tmp_path, provider)
    conversation = service.create("verein-demo")

    _send(service, conversation, "Wir leben jetzt von Mitgliedsbeiträgen.")

    kind, messages = provider.calls[0]
    payload = json.loads(messages[1]["content"])
    assert payload["message"] == "Wir leben jetzt von Mitgliedsbeiträgen."
    assert [q["field_key"] for q in payload["questions"]][:2] == ["club_type", "founded_year"]
    assert payload["current_answers"]["funding_source"]["display_value"] == "Öffentliche Förderungen"
    assert "Frage" in messages[0]["content"] and "null" in messages[0]["content"]


# --- feedback on recommendations in the chat --------------------------------


def _service_with_feedback_provider(tmp_path, proposal):
    provider = FakeProvider(proposal)
    service, profiles = _service(tmp_path, provider)
    return service, profiles, provider


def test_feedback_stated_in_the_chat_is_confirmed_before_it_is_stored(tmp_path):
    service, profiles, provider = _service_with_feedback_provider(
        tmp_path, {"field_key": None, "value": None, "feedback_recommendation_id": "fee-review",
                   "feedback_stance": "rejected"},
    )
    conversation = service.create("verein-demo")

    asked = _send(service, conversation, "Eine Überarbeitung der Mitgliedsbeiträge kommt für uns nicht in Frage.")

    assert "Mitgliedsbeiträge prüfen" in asked.content and "abgelehnt" in asked.content
    assert "Ja" in asked.content and "Nein" in asked.content
    assert service.get(conversation.conversation_id).pending.kind == "confirm_feedback"
    assert profiles.get("verein-demo").feedback == {}

    stored = _send(service, conversation, "ja")

    profile = profiles.get("verein-demo")
    assert profile.feedback["fee-review"].stance == "rejected"
    assert profile.feedback["fee-review"].origin == "chat"
    assert "abgelehnt" in stored.content and "vermerkt" in stored.content.lower()
    assert service.get(conversation.conversation_id).pending is None
    payload = json.loads(provider.calls[0][1][1]["content"])
    assert [r["recommendation_id"] for r in payload["recommendations"]] == [
        *[r.recommendation_id for r in profile.result.recommendations], "fee-review"
    ]


def test_feedback_can_be_declined_and_unknown_ids_are_ignored(tmp_path):
    service, profiles, provider = _service_with_feedback_provider(
        tmp_path, {"field_key": None, "value": None, "feedback_recommendation_id": "fee-review",
                   "feedback_stance": "accepted"},
    )
    conversation = service.create("verein-demo")
    _send(service, conversation, "Die Mitgliedsbeiträge überarbeiten wir auf jeden Fall.")

    declined = _send(service, conversation, "nein")

    assert declined.content == CHANGE_DISCARDED_REPLY
    assert profiles.get("verein-demo").feedback == {}

    provider.proposal = {"field_key": None, "value": None, "feedback_recommendation_id": "nope",
                         "feedback_stance": "accepted"}
    other = _send(service, conversation, "Das machen wir sicher.")

    assert other.content.startswith("Beratungsantwort")
    assert service.get(conversation.conversation_id).pending is None
