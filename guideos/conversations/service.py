from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import ValidationError

from typing import Any

from guideos.assessment.models import (
    AssessmentEvent,
    AssessmentResult,
    LLMRecommendation,
    RecommendationResult,
)
from guideos.assessment.orchestrator import _ASK_LLM, direct_chat_value
from guideos.chat import Message
from guideos.context import build_advisory_context
from guideos.context.prompt import AdvisoryAnswer, build_advisory_messages
from guideos.context.sources import (
    render_source_line,
    cites_knowledge,
    render_unverified_line,
    resolve_sources,
    tool_source,
    unverified_values,
)
from guideos.context.verification import strip_internal_references
from guideos.conversations.models import Conversation, MessageSection, PendingAction
from guideos.conversations.repository import ConversationRepository
from guideos.domains.catalog import DomainCatalogError, load_domain_pack_version
from guideos.domains.loader import DomainPackError
from guideos.domains.models import DomainPack, QuestionDefinition, advisory_file_question, display_value
from guideos.knowledge.models import KnowledgeError, SourceRef
from guideos.knowledge.service import KnowledgeService
from guideos.profiles.interpreter import (
    LLMProfileChangeInterpreter,
    ProfileChangeInterpretationError,
)
from guideos.profiles.models import Profile, ProfileAnswer
from guideos.profiles.repository import ProfileRepository
from guideos.profiles.service import ProfileService, ProfileServiceError, ProfileUpdate
from guideos.providers.base import LLMProvider, ProviderError
from guideos.recommendations.service import (
    RecommendationServiceError,
    generate_profile_recommendations,
)
from guideos.reports.renderer import render_llm_recommendations, render_rule_recommendations
from guideos.tools.datasets import DEFAULT_OWNER, DatasetImporter, decode_text, step_arguments
from guideos.tools.models import DatasetRef, ToolError, ToolResult
from guideos.tools.service import ToolService


ConversationErrorCode = Literal[
    "invalid_input",
    "invalid_state",
    "required",
    "profile_not_found",
    "profile_unavailable",
    "domain_version_unavailable",
    "provider_error",
    "knowledge_unavailable",
    "tool_error",
]

DEFAULT_CONVERSATION_TITLE = "Neue Beratung"
# A conversation that still carries the default title takes its title from the
# first user message (decision of 2026-11-03): deterministic, no model call.
TITLE_MAX_CHARS = 40
# Answer kinds whose text an LLM produced; only these record the model used.
LLM_ANSWER_KINDS = frozenset({"advisory", "llm_recommendations"})
RESULT_CONVERSATION_TITLE = "Ergebnis"
RESULT_INTRO = "Onboarding ist abgeschlossen, hier ist gleich meine erste Empfehlung:"
RESULT_UPDATED_INTRO = (
    "Du hast ein neues Assessment abgeschlossen. Dein Profil ist aktualisiert, "
    "hier sind die Empfehlungen zum neuen Stand:"
)
RECOMMENDATION_OFFER = (
    "Möchtest du jetzt zusätzliche Empfehlungen aus KI-Fachwissen zu deinem "
    "Ergebnis? Antworte mit Ja, oder stell direkt eine Frage."
)
OFFER_DECLINED_REPLY = (
    "Alles klar. Du kannst jederzeit nach Empfehlungen fragen oder eine andere "
    "Frage stellen."
)
REGENERATE_OFFER = (
    "Soll ich die Hinweise aus den Wissensquellen zu deinem neuen Stand neu "
    "erzeugen? Antworte mit Ja oder Nein."
)
REGENERATE_DECLINED_REPLY = (
    "Alles klar. Die bisherigen Hinweise bleiben als Stand vor der Änderung "
    "stehen; du kannst die Neuerzeugung jederzeit anstoßen."
)
CHANGE_DISCARDED_REPLY = "Alles klar, ich lasse deine Angabe unverändert."
DATASET_IMPORTED_INTRO = "Messdaten importiert"
ANALYSIS_INTRO = "Erste Analyse"
DATASET_DELETED_REPLY = "Deine Messdaten wurden gelöscht; Berechnungen daraus stehen nicht mehr zur Verfügung."
TOOLS_UNAVAILABLE_NOTES = {
    "model_without_tools": (
        "_Hinweis: Das gewählte Modell kann keine Berechnungswerkzeuge aufrufen; die Antwort "
        "stützt sich nur auf deine Angaben und die Wissensquellen._"
    ),
    "error": (
        "_Hinweis: Der Berechnungsdienst war nicht erreichbar; die Antwort enthält keine neuen "
        "Berechnungen._"
    ),
    "budget": "_Hinweis: Das Budget für Berechnungen je Nachricht war erschöpft._",
}
# Owner of tool calls for local processes without pilot login (PostgreSQL scopes by participant).
MAX_DATASET_BYTES = 64 * 1024 * 1024
PREVIOUS_TOOL_RESULTS = 10
_OFFER_ACCEPTED = {
    "ja", "ja bitte", "ja gerne", "ja gern", "ja klar", "ja sicher", "ja unbedingt",
    "gerne", "gern", "bitte", "ok", "okay", "klar", "sicher", "unbedingt", "yes",
}
_OFFER_DECLINED = {
    "nein", "nein danke", "ne", "noe", "nö", "no", "nicht jetzt", "noch nicht",
    "spaeter", "später", "erstmal nicht", "nein spaeter", "nein später",
}


class ConversationError(Exception):
    def __init__(self, message: str, code: ConversationErrorCode = "invalid_input"):
        super().__init__(message)
        self.code = code


logger = logging.getLogger(__name__)

class ConversationService:
    """Coordinates independent advisory conversations over the participant's
    profile of a domain. The advisory context is rebuilt from the profile for
    every LLM request; the conversation only owns its own message history.
    With a knowledge service, passages of the domain's knowledge base are
    retrieved before each LLM request and every answer section names its
    sources."""

    def __init__(
        self,
        conversations: ConversationRepository,
        profiles: ProfileRepository,
        provider: LLMProvider,
        load_pack: Callable[[str, str], DomainPack] = load_domain_pack_version,
        knowledge: KnowledgeService | None = None,
        tools: ToolService | None = None,
    ):
        self.conversations = conversations
        self.profiles = profiles
        self.provider = provider
        self.load_pack = load_pack
        self.knowledge = knowledge
        self.tools = tools
        self.profile_service = ProfileService(profiles, load_pack=load_pack)
        # Serialises check-and-create of the result chat within this process.
        self._result_lock = threading.Lock()
        self.change_interpreter = LLMProfileChangeInterpreter(provider)

    def create(
        self,
        domain_id: str,
        title: str | None = None,
        participant_id: str | None = None,
    ) -> Conversation:
        """A new, empty conversation on the participant's profile of the
        domain. Raises ConversationError(profile_not_found) without a
        completed assessment in that domain."""
        profile = self._profile(domain_id, participant_id)
        conversation = Conversation.create(
            domain_id=domain_id,
            title=(title or "").strip() or DEFAULT_CONVERSATION_TITLE,
            participant_id=participant_id or profile.participant_id,
            profile_id=profile.profile_id,
        )
        self.conversations.save(conversation)
        _log_created(conversation, kind="manual")
        return conversation

    def ensure_result_conversation(
        self,
        domain_id: str,
        endpoint: str,
        model: str,
        participant_id: str | None = None,
    ) -> Conversation:
        """The "Ergebnis" chat of the participant's profile, created on the
        first completed assessment and extended by one block each time a
        newer assessment replaced the profile: an intro, the rule
        recommendations and the knowledge recommendations persisted at
        completion. Without persisted ones the chat offers to generate them;
        without rule-based recommendations they are generated right away
        and the offer is skipped. Calling it again for the same profile
        state returns the chat unchanged."""
        with self._result_lock:
            return self._ensure_result_conversation(domain_id, endpoint, model, participant_id)

    def _ensure_result_conversation(
        self, domain_id: str, endpoint: str, model: str, participant_id: str | None
    ) -> Conversation:
        profile = self._profile(domain_id, participant_id)
        existing = next(
            (
                item
                for item in self.conversations.list()
                if item.profile_id == profile.profile_id
                and item.title == RESULT_CONVERSATION_TITLE
            ),
            None,
        )
        if existing is not None and existing.result_session_id == profile.source_session_id:
            return existing
        pack = self._resolve_pack(profile)
        if existing is None:
            conversation = Conversation.create(
                domain_id=domain_id,
                title=RESULT_CONVERSATION_TITLE,
                participant_id=participant_id or profile.participant_id,
                profile_id=profile.profile_id,
            )
            conversation.add_message("assistant", _result_intro(pack))
        else:
            conversation = existing
            conversation.add_message("assistant", _result_updated_intro(pack))
        self._seed_result(conversation, profile, pack, endpoint, model)
        conversation.result_session_id = profile.source_session_id
        self.conversations.save(conversation)
        _log_created(conversation, kind="result" if existing is None else "result_updated")
        self._announce_session_dataset(profile, pack)
        return conversation

    def _announce_session_dataset(self, profile: Profile, pack: DomainPack) -> None:
        """A dataset uploaded in a file question of the assessment: tell the
        result chat about it like the panel import does, and run the pack's
        first analysis. Runs once per completed assessment, because the
        result chat is seeded once per profile state."""
        dataset = profile.dataset
        if dataset is None or dataset.field_key is None or self.tools is None or pack.tools is None:
            return
        answer = profile.answers.get(dataset.field_key)
        if answer is None or answer.origin != "assessment":
            return
        text = f"{DATASET_IMPORTED_INTRO}: {dataset.filename}. {dataset.summary}".strip()
        self._post_to_result_chat(profile, text, [MessageSection(text=text, sources=[])], [])
        steps = pack.tools.analysis_steps()
        if profile.tool_results:
            # The first analysis already ran in the assessment right after the
            # import; post its results instead of computing them again.
            notes = {step.tool: step.note for step in steps}
            for result in profile.tool_results:
                self._post_analysis(profile, result, notes.get(result.call.name))
            return
        for step in steps:
            self._first_analysis(profile, step, dataset.dataset_id)

    def _seed_result(
        self,
        conversation: Conversation,
        profile: Profile,
        pack: DomainPack,
        endpoint: str,
        model: str,
    ) -> None:
        result = profile.result
        persisted = result.knowledge_recommendations
        if result.recommendations:
            conversation.add_message(
                "assistant",
                render_rule_recommendations(pack, result, compact=_compact(pack)),
                _rule_sections(pack, result),
            )
            if persisted:
                # Generated at completion: show the stored set, no new model call.
                conversation.add_message(
                    "assistant",
                    render_llm_recommendations(pack, persisted, compact=_compact(pack)),
                    _llm_recommendation_sections(pack, persisted),
                )
            else:
                conversation.add_message("assistant", _recommendation_offer(pack))
        elif persisted:
            conversation.add_message(
                "assistant",
                render_llm_recommendations(pack, persisted, heading="KI-Empfehlungen",
                                           compact=_compact(pack)),
                _llm_recommendation_sections(pack, persisted),
            )
        else:
            conversation.add_message(
                "assistant",
                *self._llm_recommendations_answer(
                    profile, pack, endpoint, model, heading="KI-Empfehlungen"
                ),
                model=model,
            )

    def list(self) -> list[Conversation]:
        return self.conversations.list()

    def get(self, conversation_id: str) -> Conversation:
        return self.conversations.get(conversation_id)

    def delete(self, conversation_id: str) -> None:
        self.conversations.delete(conversation_id)

    def delete_for_profile(self, profile_id: str) -> list[str]:
        """Delete every conversation of the profile; returns the removed IDs."""
        removed = []
        for conversation in self.conversations.list():
            if conversation.profile_id == profile_id:
                self.conversations.delete(conversation.conversation_id)
                removed.append(conversation.conversation_id)
        return removed

    def send_message(
        self,
        conversation_id: str,
        text: str,
        endpoint: str,
        model: str,
    ) -> Conversation:
        """One user message. A pending action (confirmation of a proposed
        profile change, an open follow-up question, the offer to regenerate
        the knowledge recommendations) is resolved deterministically first; a
        statement is checked for a changed fact (the model proposes, GuideOS
        validates and asks for confirmation); everything else is a question
        for the advisory model."""
        normalized_text = text.strip()
        if not normalized_text:
            raise ConversationError(
                "Bitte gib zuerst eine Frage ein.",
                code="required",
            )
        conversation = self.conversations.get(conversation_id)
        sections: list[MessageSection] = []
        tool_results: list[ToolResult] = []
        handled = self._resolve_pending(conversation, normalized_text, endpoint, model)
        if handled is not None:
            answer, sections, answer_kind = handled
        else:
            decision = (
                _offer_decision(normalized_text) if self._awaits_offer_reply(conversation) else None
            )
            if decision == "accept":
                profile = self._resolve_profile(conversation)
                answer, sections = self._llm_recommendations_answer(
                    profile, self._resolve_pack(profile), endpoint, model
                )
                answer_kind = "llm_recommendations"
            elif decision == "decline":
                answer = OFFER_DECLINED_REPLY
                answer_kind = "offer_declined"
            else:
                proposal = self._change_proposal(conversation, normalized_text, endpoint, model)
                if proposal is not None:
                    answer, answer_kind = proposal
                else:
                    answer, sections, tool_results = self._advisory_answer(
                        conversation, normalized_text, endpoint, model
                    )
                    answer_kind = "advisory"
        if conversation.title == DEFAULT_CONVERSATION_TITLE and not any(
            message.role == "user" for message in conversation.messages
        ):
            conversation.title = derive_title(normalized_text)
        used_model = model if answer_kind in LLM_ANSWER_KINDS else None
        conversation.add_message("user", normalized_text)
        conversation.add_message("assistant", answer, sections, tool_results, model=used_model)
        self.conversations.save(conversation)
        logger.info(
            "message_sent conversation_id=%s profile_id=%s answer=%s model=%s",
            conversation.conversation_id,
            conversation.profile_id,
            answer_kind,
            used_model,
        )
        return conversation

    # --- profile changes in the chat ---------------------------------------

    def _change_proposal(
        self, conversation: Conversation, text: str, endpoint: str, model: str
    ) -> tuple[str, str] | None:
        """Ask the change interpreter whether a statement changes a fact.
        Returns the confirmation question (or the validation message) with
        the answer kind, or None when the message is a question or no change
        was proposed."""
        if text.rstrip().endswith("?") or _offer_decision(text) is not None:
            return None
        profile = self._resolve_profile(conversation)
        pack = self._resolve_pack(profile)
        try:
            proposal = self.change_interpreter.interpret(endpoint, model, pack, profile, text)
        except ProviderError as exc:
            raise ConversationError(str(exc), code="provider_error") from exc
        except ProfileChangeInterpretationError as exc:
            raise ConversationError(str(exc), code="provider_error") from exc
        if proposal.field_key is None:
            return self._feedback_proposal(conversation, profile, proposal)
        try:
            _, _, question, normalized = self.profile_service.check_fact(
                profile.domain_id, profile.participant_id, proposal.field_key, proposal.value
            )
        except ProfileServiceError as exc:
            if exc.code == "validation_failed":
                return f"Das kann ich so nicht übernehmen: {exc}", "change_rejected"
            return None
        previous = profile.answers.get(question.field_key)
        conversation.pending = PendingAction(
            kind="confirm_change",
            field_key=question.field_key,
            value=normalized,
            raw_answer=text,
        )
        before = display_value(question, previous.normalized_value) if previous else "keine Angabe"
        after = display_value(question, normalized)
        return (
            f"Soll ich deine Angabe ändern? {question.text} {before} → {after}. "
            "Antworte mit Ja oder Nein.",
            "change_proposed",
        )

    def _feedback_proposal(
        self, conversation: Conversation, profile: Profile, proposal: Any
    ) -> tuple[str, str] | None:
        """A stance on a known recommendation becomes a confirmation
        question; unknown ids are ignored."""
        recommendation_id = proposal.feedback_recommendation_id
        stance = proposal.feedback_stance
        if recommendation_id is None or stance is None:
            return None
        known = {
            item.recommendation_id: item.title
            for item in (*profile.result.recommendations, *profile.result.knowledge_recommendations)
        }
        if recommendation_id not in known:
            return None
        conversation.pending = PendingAction(
            kind="confirm_feedback", recommendation_id=recommendation_id, stance=stance
        )
        label = "abgelehnt" if stance == "rejected" else "befürwortet"
        return (
            f"Soll ich die Empfehlung „{known[recommendation_id]}“ als {label} vermerken? "
            "Antworte mit Ja oder Nein.",
            "feedback_proposed",
        )

    def _resolve_pending(
        self, conversation: Conversation, text: str, endpoint: str, model: str
    ) -> tuple[str, list[MessageSection], str] | None:
        pending = conversation.pending
        if pending is None:
            return None
        if pending.kind == "confirm_feedback":
            decision = _offer_decision(text)
            conversation.pending = None
            if decision == "accept":
                profile = self._resolve_profile(conversation)
                try:
                    updated = self.profile_service.set_feedback(
                        profile.domain_id,
                        profile.participant_id,
                        pending.recommendation_id or "",
                        pending.stance or "accepted",
                        origin="chat",
                    )
                except ProfileServiceError as exc:
                    return f"Das konnte ich nicht vermerken: {exc}", [], "feedback_rejected"
                item = updated.feedback[pending.recommendation_id or ""]
                label = "abgelehnt" if item.stance == "rejected" else "befürwortet"
                return (
                    f"Vermerkt: Die Empfehlung „{item.title}“ gilt als {label}. "
                    "Bei der nächsten Erzeugung der Hinweise berücksichtige ich das.",
                    [],
                    "feedback_stored",
                )
            if decision == "decline":
                return CHANGE_DISCARDED_REPLY, [], "feedback_discarded"
            return None
        if pending.kind == "confirm_change":
            decision = _offer_decision(text)
            if decision == "accept":
                conversation.pending = None
                return self._apply_change(
                    conversation, pending.field_key or "", pending.value, pending.raw_answer
                )
            if decision == "decline":
                conversation.pending = None
                return CHANGE_DISCARDED_REPLY, [], "change_discarded"
            conversation.pending = None
            return None
        if pending.kind == "answer_question":
            return self._answer_follow_up(conversation, pending.field_key or "", text)
        if pending.kind == "regenerate_offer":
            decision = _offer_decision(text)
            if decision == "accept":
                conversation.pending = None
                profile = self._resolve_profile(conversation)
                answer, sections = self._llm_recommendations_answer(
                    profile, self._resolve_pack(profile), endpoint, model
                )
                return answer, sections, "llm_recommendations"
            if decision == "decline":
                conversation.pending = None
                return REGENERATE_DECLINED_REPLY, [], "regenerate_declined"
            conversation.pending = None
            return None
        return None

    def _apply_change(
        self, conversation: Conversation, field_key: str, value: Any, raw_answer: str | None
    ) -> tuple[str, list[MessageSection], str]:
        profile = self._resolve_profile(conversation)
        try:
            update = self.profile_service.update_fact(
                profile.domain_id, profile.participant_id, field_key, value, raw_answer=raw_answer
            )
        except ProfileServiceError as exc:
            return f"Das konnte ich nicht übernehmen: {exc}", [], "change_rejected"
        pack = self._resolve_pack(update.profile)
        question = next(q for q in pack.questions if q.field_key == field_key)
        lines = [
            f"Übernommen: {question.text} "
            f"{display_value(question, update.profile.answers[field_key].normalized_value)}."
        ]
        sections: list[MessageSection] = []
        if update.result_changed:
            lines.append("Deine Regel-Empfehlungen wurden neu berechnet:")
            lines.append(render_rule_recommendations(pack, update.profile.result,
                                                     compact=_compact(pack)))
            sections = _rule_sections(pack, update.profile.result)
        else:
            lines.append("Die Regel-Empfehlungen bleiben unverändert.")
        lines.append(self._after_change(conversation, pack, update))
        return "\n\n".join(line for line in lines if line), sections, "change_applied"

    def _after_change(
        self, conversation: Conversation, pack: DomainPack, update: ProfileUpdate
    ) -> str:
        """Next step after a stored change: the first open required question,
        or the offer to regenerate the knowledge recommendations."""
        if update.open_questions:
            question = update.open_questions[0]
            conversation.pending = PendingAction(kind="answer_question", field_key=question.field_key)
            return "Dadurch wird eine weitere Angabe nötig: " + _question_prompt(question)
        if update.profile.result.knowledge_generation is not None:
            conversation.pending = PendingAction(kind="regenerate_offer")
            return REGENERATE_OFFER
        return ""

    def _answer_follow_up(
        self, conversation: Conversation, field_key: str, text: str
    ) -> tuple[str, list[MessageSection], str]:
        profile = self._resolve_profile(conversation)
        pack = self._resolve_pack(profile)
        question = next((q for q in pack.questions if q.field_key == field_key), None)
        if question is None:
            conversation.pending = None
            return None  # type: ignore[return-value]
        value = direct_chat_value(question, text)
        if value is _ASK_LLM:
            return (
                "Das habe ich nicht als Antwort erkannt. " + _question_prompt(question),
                [],
                "follow_up_unclear",
            )
        try:
            update = self.profile_service.update_fact(
                profile.domain_id, profile.participant_id, field_key, value, raw_answer=text
            )
        except ProfileServiceError as exc:
            return f"{exc} {_question_prompt(question)}", [], "follow_up_rejected"
        conversation.pending = None
        lines = [
            f"Übernommen: {question.text} "
            f"{display_value(question, update.profile.answers[field_key].normalized_value)}.",
            self._after_change(conversation, pack, update),
        ]
        return "\n\n".join(line for line in lines if line), [], "follow_up_answered"

    def _advisory_answer(
        self, conversation: Conversation, user_text: str, endpoint: str, model: str
    ) -> tuple[str, list[MessageSection], list[ToolResult]]:
        """The model answers in sections and only cites ids; GuideOS resolves
        them and marks everything else as the model's own knowledge. With the
        pack's tools enabled, the tool loop runs first: the model chooses
        tools, GuideOS executes them, and the results join the context as
        T1… for the structured answer."""
        profile = self._resolve_profile(conversation)
        pack = self._resolve_pack(profile)
        context = build_advisory_context(pack, profile)
        # The profile's analyses first (every chat sees them), then what this
        # conversation computed earlier, without repeating a calculation.
        context = context.model_copy(update={
            "tool_results": _merge_tool_results(context.tool_results, _previous_tool_results(conversation)),
        })
        history = _history(conversation)
        new_results: list[ToolResult] = []
        note = ""
        try:
            passages = (
                self.knowledge.search(pack, user_text, endpoint)
                if self.knowledge is not None
                else []
            )
            new_results, note = self._run_tools(pack, profile, context, history, user_text, passages,
                                                endpoint, model)
            context = context.model_copy(update={"tool_results": [*context.tool_results, *new_results]})
            answer = AdvisoryAnswer.model_validate(
                self.provider.generate_structured(
                    endpoint,
                    model,
                    build_advisory_messages(context, history, user_text, passages),
                    AdvisoryAnswer.model_json_schema(),
                )
            )
        except KnowledgeError as exc:
            raise ConversationError(str(exc), code="knowledge_unavailable") from exc
        except ProviderError as exc:
            raise ConversationError(str(exc), code="provider_error") from exc
        except ValidationError as exc:
            raise ConversationError(
                "Die Antwort des Modells war nicht korrekt strukturiert.",
                code="provider_error",
            ) from exc
        field_keys = [fact.field_key for fact in context.facts]
        passage_ids = [passage.source_id for passage in passages]
        sections = [
            MessageSection(
                text=strip_internal_references(section.text, field_keys, passage_ids),
                sources=resolve_sources(section.source_ids, context, passages),
                unverified_values=unverified_values(
                    section.text,
                    section.source_ids,
                    context,
                    passages,
                    [user_text, *(m["content"] for m in history if m["role"] == "user")],
                ),
            )
            for section in answer.sections
            if section.text.strip()
        ]
        if not sections:
            raise ConversationError(
                "Die Antwort des Modells war nicht korrekt strukturiert.",
                code="provider_error",
            )
        content = "\n\n".join(
            f"{section.text}  \n{render_source_line(section.sources)}"
            + (
                f"  \n{render_unverified_line(section.unverified_values, cites_knowledge(section.sources))}"
                if section.unverified_values
                else ""
            )
            for section in sections
        )
        if note:
            content = f"{content}\n\n{note}"
        if new_results:
            self._remember_tool_results(profile, new_results)
        return content, sections, new_results

    def _run_tools(self, pack, profile, context, history, user_text, passages, endpoint, model
                   ) -> tuple[list[ToolResult], str]:
        """The model-driven tool loop, when the pack version enables tools and
        the domain has allowed tools; returns the new results and a note for
        the user when tools could not be used."""
        settings = pack.tools
        if settings is None or not settings.enabled or self.tools is None:
            return [], ""
        if not self.tools.specs_for(pack.domain_id):
            return [], ""
        messages = build_advisory_messages(context, history, user_text, passages)
        outcome = self.tools.run_loop(
            pack.domain_id, _owner(profile), self.provider, endpoint, model, messages,
            max_calls=settings.max_calls_per_message, max_seconds=settings.max_seconds_per_message,
        )
        logger.info(
            "advisory_tools profile_id=%s stop=%s calls=%d results=%d code=%s",
            profile.profile_id, outcome.stop_reason, len(outcome.transcript) - len(messages),
            len(outcome.tool_results), outcome.error.code if outcome.error else None,
        )
        return outcome.tool_results, TOOLS_UNAVAILABLE_NOTES.get(outcome.stop_reason, "")

    def _remember_tool_results(self, profile: Profile, results: Sequence[ToolResult]) -> None:
        """Keep the last result per tool on the profile for the report."""
        kept = {result.call.name: result for result in profile.tool_results}
        for result in results:
            kept[result.call.name] = result
        profile.tool_results = list(kept.values())
        profile.updated_at = datetime.now(UTC)
        self.profiles.save(profile)

    # --- datasets on a tool server -------------------------------------------

    def import_dataset(
        self,
        domain_id: str,
        filename: str,
        data: bytes,
        format_hint: str = "auto",
        participant_id: str | None = None,
    ) -> Profile:
        """Upload measurement data for the participant's profile of a domain:
        GuideOS itself calls the pack's dataset tool with the file content,
        remembers the dataset on the profile and posts a deterministic message
        to the result chat. A previous dataset is deleted on the server."""
        profile = self._profile(domain_id, participant_id)
        pack = self._resolve_pack(profile)
        settings = self._require_tools(pack)
        if len(data) > MAX_DATASET_BYTES:
            raise ConversationError(
                f"Die Datei ist größer als {MAX_DATASET_BYTES // (1024 * 1024)} MiB.", code="invalid_input"
            )
        if not data.strip():
            raise ConversationError("Die Datei ist leer.", code="invalid_input")
        content = decode_text(data)
        try:
            reference, result = DatasetImporter(self.tools).import_text(
                domain_id, profile.participant_id, settings.dataset_tool, filename, content, format_hint,
            )
        except ToolError as exc:
            raise ConversationError(
                f"Die Messdaten konnten nicht importiert werden: {exc.message}", code="tool_error"
            ) from exc
        dataset_id = reference.dataset_id
        if profile.dataset is not None:
            self._delete_on_server(profile, settings.delete_tool, profile.dataset.dataset_id)
        question = advisory_file_question(pack)
        if question is not None:
            reference.field_key = question.field_key
            _set_file_answer(profile, question, reference)
        profile.dataset = reference
        profile.tool_results = []
        profile.events = [
            *profile.events,
            AssessmentEvent(event_type="dataset_imported",
                            payload={"dataset_id": dataset_id, "server": result.server_name}),
        ]
        profile.updated_at = datetime.now(UTC)
        self.profiles.save(profile)
        logger.info("dataset_imported profile_id=%s dataset_id=%s server=%s", profile.profile_id,
                    dataset_id, result.server_name)
        text = f"{DATASET_IMPORTED_INTRO}: {profile.dataset.filename}. {result.summary}".strip()
        self._post_to_result_chat(profile, text, [MessageSection(
            text=text, sources=[tool_source(result)])], [result])
        for step in settings.analysis_steps():
            self._first_analysis(profile, step, dataset_id, step.arguments)
        answers = {key: item.normalized_value for key, item in profile.answers.items()}
        for step in settings.completion_steps():
            self._first_analysis(profile, step, dataset_id, step_arguments(step, answers))
        return profile

    def _first_analysis(self, profile: Profile, analysis, dataset_id: str, arguments: dict[str, Any]) -> None:
        """The pack's deterministic first analysis of a new dataset: one tool
        call GuideOS makes itself, posted with the pack's note. A failure is
        reported in the chat and does not undo the import."""
        try:
            result = self.tools.call(
                profile.domain_id, _owner(profile), analysis.tool,
                {"dataset_id": dataset_id, **arguments}, timeout=180,
            )
        except ToolError as exc:
            logger.warning("first_analysis_failed profile_id=%s tool=%s code=%s",
                           profile.profile_id, analysis.tool, exc.code)
            self._post_to_result_chat(
                profile, f"{ANALYSIS_INTRO}: Die erste Analyse konnte nicht erstellt werden: "
                f"{exc.message}", [], [])
            return
        self._remember_tool_results(profile, [result])
        self._post_analysis(profile, result, analysis.note)

    def _post_analysis(self, profile: Profile, result: ToolResult, note: str | None) -> None:
        text = f"{ANALYSIS_INTRO}: {result.summary}".strip()
        if note:
            text = f"{text} {note}"
        logger.info("first_analysis_posted profile_id=%s tool=%s calculation_id=%s",
                    profile.profile_id, result.call.name, result.calculation_id)
        self._post_to_result_chat(profile, text, [MessageSection(text=text, sources=[tool_source(result)])],
                                  [result])

    def delete_dataset(self, domain_id: str, participant_id: str | None = None) -> Profile:
        """Delete the participant's dataset on the server and forget it in
        GuideOS, with its tool results."""
        profile = self._profile(domain_id, participant_id)
        pack = self._resolve_pack(profile)
        settings = self._require_tools(pack)
        if profile.dataset is None:
            raise ConversationError("Für dieses Profil sind keine Messdaten gespeichert.",
                                    code="invalid_state")
        self._delete_on_server(profile, settings.delete_tool, profile.dataset.dataset_id)
        dataset_id = profile.dataset.dataset_id
        question = advisory_file_question(pack)
        if question is not None:
            _set_file_answer(profile, question, None)
        profile.dataset = None
        profile.tool_results = []
        profile.events = [*profile.events, AssessmentEvent(event_type="dataset_deleted",
                                                           payload={"dataset_id": dataset_id})]
        profile.updated_at = datetime.now(UTC)
        self.profiles.save(profile)
        logger.info("dataset_deleted profile_id=%s dataset_id=%s", profile.profile_id, dataset_id)
        self._post_to_result_chat(profile, DATASET_DELETED_REPLY, [], [])
        return profile

    def post_dataset_deleted(self, domain_id: str, participant_id: str | None = None) -> None:
        """Tell the result chat that the datasets of a deleted assessment are
        gone (delete_session_data changed the profile; no model involved)."""
        profile = self._profile(domain_id, participant_id)
        self._post_to_result_chat(profile, DATASET_DELETED_REPLY, [], [])

    def _require_tools(self, pack: DomainPack):
        settings = pack.tools
        if settings is None or not settings.enabled or self.tools is None:
            raise ConversationError(
                "Diese Domain-Version nimmt keine Messdaten an.", code="invalid_state"
            )
        return settings

    def _delete_on_server(self, profile: Profile, delete_tool: str, dataset_id: str) -> bool:
        return DatasetImporter(self.tools).delete(
            profile.domain_id, profile.participant_id, delete_tool, dataset_id
        )

    def _post_to_result_chat(self, profile: Profile, text: str, sections: list[MessageSection],
                             tool_results: list[ToolResult]) -> None:
        """Append a deterministic assistant message to the profile's result
        chat (or its latest chat, or a new one)."""
        conversations = [c for c in self.conversations.list() if c.profile_id == profile.profile_id]
        conversation = next((c for c in conversations if c.title == RESULT_CONVERSATION_TITLE), None)
        if conversation is None:
            conversation = conversations[-1] if conversations else Conversation.create(
                profile.domain_id, RESULT_CONVERSATION_TITLE, profile.participant_id, profile.profile_id
            )
        conversation.add_message("assistant", text, sections, tool_results)
        self.conversations.save(conversation)

    def _llm_recommendations_answer(
        self,
        profile: Profile,
        pack: DomainPack,
        endpoint: str,
        model: str,
        heading: str = "Zusätzliche KI-Empfehlungen",
    ) -> tuple[str, list[MessageSection]]:
        """Knowledge recommendations generated on request and persisted on
        the profile, rendered as a chat message."""
        try:
            items = generate_profile_recommendations(
                self.profiles,
                profile.domain_id,
                profile.participant_id,
                self.provider,
                endpoint,
                model,
                load_pack=self.load_pack,
                knowledge=self.knowledge,
            )
        except RecommendationServiceError as exc:
            raise ConversationError(str(exc), code=exc.code) from exc
        return (
            render_llm_recommendations(pack, items, heading=heading, compact=_compact(pack)),
            _llm_recommendation_sections(pack, items),
        )

    def _profile(self, domain_id: str, participant_id: str | None) -> Profile:
        try:
            return self.profiles.get(domain_id, participant_id)
        except LookupError as exc:
            raise ConversationError(
                "Für diese Domäne gibt es noch kein Profil. Schließe zuerst ein "
                "Assessment ab.",
                code="profile_not_found",
            ) from exc

    def _awaits_offer_reply(self, conversation: Conversation) -> bool:
        """True while the last message is the offer to generate knowledge
        recommendations: the standard text, or the text the pack sets. A
        profile or pack that cannot be resolved is not an error here; the
        later steps raise it as before."""
        last = conversation.messages[-1] if conversation.messages else None
        if last is None or last.role != "assistant":
            return False
        if last.content == RECOMMENDATION_OFFER:
            return True
        try:
            pack = self._resolve_pack(self._resolve_profile(conversation))
        except ConversationError:
            return False
        offer = _recommendation_offer(pack)
        return offer != RECOMMENDATION_OFFER and last.content == offer

    def _resolve_profile(self, conversation: Conversation) -> Profile:
        unavailable = ConversationError(
            "Das Profil zu dieser Konversation ist nicht mehr verfügbar.",
            code="profile_unavailable",
        )
        if conversation.profile_id is None:
            raise unavailable
        try:
            profile = self.profiles.get(conversation.domain_id, conversation.participant_id)
        except LookupError as exc:
            raise unavailable from exc
        if profile.profile_id != conversation.profile_id:
            raise unavailable
        return profile

    def _resolve_pack(self, profile: Profile) -> DomainPack:
        try:
            pack = self.load_pack(profile.domain_id, profile.domain_version)
        except (DomainCatalogError, DomainPackError) as exc:
            raise ConversationError(
                "Die Domain-Version des Profils ist nicht mehr verfügbar.",
                code="domain_version_unavailable",
            ) from exc
        if pack.content_hash != profile.domain_content_hash:
            raise ConversationError(
                "Die Domain-Version des Profils ist nicht mehr verfügbar.",
                code="domain_version_unavailable",
            )
        return pack


def _set_file_answer(profile: Profile, question: QuestionDefinition, reference: DatasetRef | None) -> None:
    """Keep the profile's answer to the advisory file question in step with
    the dataset the panel imported or deleted."""
    profile.answers[question.field_key] = ProfileAnswer(
        question_id=question.question_id,
        field_key=question.field_key,
        raw_answer=reference.filename if reference is not None else "unbekannt",
        normalized_value=reference.model_dump(mode="json") if reference is not None else None,
        confidence=1.0,
        origin="chat_update",
        updated_at=datetime.now(UTC),
    )


def _owner(profile: Profile) -> str:
    return profile.participant_id or DEFAULT_OWNER


def _previous_tool_results(conversation: Conversation) -> list[ToolResult]:
    """The last tool results of the conversation, oldest first, so the model
    can answer follow-up questions without recomputing."""
    results = [result for message in conversation.messages for result in message.tool_results]
    return results[-PREVIOUS_TOOL_RESULTS:]


def _merge_tool_results(*groups: Sequence[ToolResult]) -> list[ToolResult]:
    """The results of several groups in order, each calculation once (by its
    calculation id, or by tool name and summary when the server gave none)."""
    merged: list[ToolResult] = []
    seen: set[tuple] = set()
    for group in groups:
        for result in group:
            key = ("id", result.calculation_id) if result.calculation_id else ("text", result.call.name, result.summary)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)
    return merged


def _history(conversation: Conversation) -> list[Message]:
    """Earlier messages for the model; answers with sections are passed as
    their plain statements, without the rendered source lines."""
    return [
        {
            "role": message.role,
            "content": "\n\n".join(section.text for section in message.sections)
            or message.content,
        }
        for message in conversation.messages
    ]


def _fact_sources(pack: DomainPack, field_keys: Sequence[str]) -> list[SourceRef]:
    questions = {question.field_key: question.text for question in pack.questions}
    return [
        SourceRef(
            kind="user_context",
            title=questions.get(field_key, field_key),
            reference=field_key,
        )
        for field_key in field_keys
    ]


def _compact(pack: DomainPack) -> bool:
    """Whether the pack asks for the compact recommendation list in the result chat."""
    return pack.result_chat is not None and pack.result_chat.recommendations == "compact"


def _rule_sections(pack: DomainPack, result: AssessmentResult) -> list[MessageSection]:
    return [
        MessageSection(
            text=f"{item.title}: {item.message}",
            sources=[
                SourceRef(kind="rule", title=item.title, reference=item.recommendation_id),
                *_fact_sources(pack, [key for key, value in item.evidence.items() if not _is_metric(value)]),
                *_metric_sources(item.evidence),
                *_figure_sources(item),
            ],
        )
        for item in result.recommendations
    ]


def _figure_sources(item: RecommendationResult) -> list[SourceRef]:
    """The calculations behind the figures quoted in a recommendation text,
    one source per calculation the metric evidence does not cite already."""
    cited = {value.get("calculation_id") for value in item.evidence.values() if _is_metric(value)}
    by_calculation: dict[tuple[str, str | None], list[str]] = {}
    for name, figure in item.figures.items():
        if figure.calculation_id in cited:
            continue
        by_calculation.setdefault((figure.tool, figure.calculation_id), []).append(f"{name}: {figure.text}")
    return [
        SourceRef(kind="tool", title=tool, reference=calculation_id, excerpt=", ".join(parts))
        for (tool, calculation_id), parts in by_calculation.items()
    ]


def _is_metric(value: Any) -> bool:
    return isinstance(value, dict) and "metric" in value and "tool" in value


def _metric_sources(evidence: dict[str, Any]) -> list[SourceRef]:
    """A metric rule rests on a calculation: cite it like a tool result."""
    return [
        SourceRef(kind="tool", title=value["tool"], reference=value.get("calculation_id"),
                  excerpt=f"{value.get('label') or value['metric']}: {value.get('value')}")
        for value in evidence.values() if _is_metric(value)
    ]


def _llm_recommendation_sections(
    pack: DomainPack,
    items: Sequence[LLMRecommendation],
) -> list[MessageSection]:
    return [
        MessageSection(
            text=f"{item.title}: {item.message} {item.rationale}",
            sources=[*item.sources, *_fact_sources(pack, item.evidence_fields)],
            unverified_values=item.unverified_values,
        )
        for item in items
    ]


def _result_intro(pack: DomainPack) -> str:
    settings = pack.result_chat
    return settings.intro if settings is not None and settings.intro else RESULT_INTRO


def _result_updated_intro(pack: DomainPack) -> str:
    settings = pack.result_chat
    return (
        settings.updated_intro if settings is not None and settings.updated_intro
        else RESULT_UPDATED_INTRO
    )


def _recommendation_offer(pack: DomainPack) -> str:
    settings = pack.result_chat
    return settings.offer if settings is not None and settings.offer else RECOMMENDATION_OFFER


def _offer_decision(text: str) -> Literal["accept", "decline"] | None:
    """Deterministic yes/no reading of a short reply to the recommendation
    offer; anything else is treated as a regular advisory question."""
    normalized = " ".join(
        "".join(char if char.isalnum() or char.isspace() else " " for char in text.lower()).split()
    )
    if normalized in _OFFER_ACCEPTED:
        return "accept"
    if normalized in _OFFER_DECLINED:
        return "decline"
    return None


def _log_created(conversation: Conversation, kind: str) -> None:
    logger.info(
        "conversation_created conversation_id=%s profile_id=%s kind=%s",
        conversation.conversation_id,
        conversation.profile_id,
        kind,
    )


def _question_prompt(question: QuestionDefinition) -> str:
    text = question.text
    if question.options:
        text += " Mögliche Antworten: " + ", ".join(option.label for option in question.options) + "."
    return text


def derive_title(text: str) -> str:
    """The title of a conversation from its first question: whitespace
    collapsed, cut after at most TITLE_MAX_CHARS characters at a word boundary
    (or hard when the first word is longer) and marked with an ellipsis."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    head = collapsed[: TITLE_MAX_CHARS + 1]
    cut = head.rfind(" ")
    shortened = head[:cut] if cut > 0 else collapsed[:TITLE_MAX_CHARS]
    return f"{shortened.rstrip(' ,;:-')} …"
