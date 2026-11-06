import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from guideos.adapters.question_editor import (
    RULE_KIND_LABELS,
    RULE_OPERATOR_LABELS,
    RULE_ORIGIN_LABELS,
    RULE_STATUS_LABELS,
    RULE_STATUS_TRANSITIONS,
    delete_recommendation,
    delete_rule,
    new_recommendation,
    new_rule,
    pack_with_recommendations,
    pack_with_rules,
    pack_with_topics,
    FIGURE_LINE_HINT,
    figures_to_text,
    recommendation_from_form,
    result_chat_from_form,
    rule_from_form,
    set_rule_status,
    topics_from_text,
    topics_to_text,
    unapproved_rules,
    CHOICE_TYPES,
    DEFAULT_UPLOAD_EXTENSIONS,
    DEFAULT_UPLOAD_MIB,
    FIELD_TYPE_LABELS,
    NEW_PACK_VERSION,
    OPERATOR_LABELS,
    WIDGET_LABELS,
    WIDGETS_BY_TYPE,
    EditorError,
    assert_version_editable,
    delete_question,
    draft_error,
    knowledge_recommendations_from_form,
    move_question,
    new_domain_pack,
    new_question,
    pack_with_questions,
    question_from_form,
)
from guideos.assessment.factory import (
    assessment_repository_from_env,
    assessment_storage_path_from_env,
    build_assessment,
)
from guideos.assessment.models import AssessmentChatMessage, AssessmentResponse, AssessmentSession
from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import (
    AssessmentConflictError,
    AssessmentRepositoryError,
    DEFAULT_ASSESSMENT_STORAGE_PATH,
    ParticipantAssessmentRepository,
)
from guideos.config import DEFAULT_PROVIDER, allowed_domains_from_env
from guideos.conversations.factory import (
    conversation_repository_from_env,
    conversation_storage_path_from_env,
)
from guideos.context.sources import describe_source
from guideos.conversations.models import Conversation, ConversationMessage
from guideos.conversations.repository import (
    ConversationConflictError,
    ConversationRepositoryError,
    ParticipantConversationRepository,
)
from guideos.conversations.service import (
    RECOMMENDATION_OFFER,
    RESULT_INTRO,
    RESULT_UPDATED_INTRO,
    ConversationError,
    ConversationService,
)
from guideos.domains.catalog import (
    DomainCatalogError,
    DomainPackRef,
    activate_domain_version,
    delete_domain_pack_draft,
    list_active_domain_versions,
    list_domain_versions,
    load_active_domain_pack,
    load_domain_pack_ref,
    load_domain_pack_version,
    pack_draft_payload,
    save_domain_pack_draft,
    save_domain_pack_draft_data,
)
from guideos.domains.loader import DomainPackError, load_domain_pack
from guideos.domains.soya import SoyaError, pack_from_soya, pack_to_soya
from guideos.domains.models import (
    DomainPack,
    QuestionDefinition,
    RecommendationDefinition,
    RuleDefinition,
    TopicDefinition,
    VisibilityCondition,
    WebSourceSettings,
    display_value,
    effective_widget,
    format_date_de,
)
from guideos.knowledge.factory import knowledge_service_from_env, knowledge_store_from_env
from guideos.profiles.factory import profile_repository_from_env
from guideos.profiles.models import Profile
from guideos.profiles.repository import (
    ParticipantProfileRepository,
    ProfileRepository,
    ProfileRepositoryError,
)
from guideos.profiles.service import (
    ProfileService,
    ProfileServiceError,
    dataset_refs,
    delete_profile_data,
    delete_session_data,
)
from guideos.tools.datasets import decode_text, format_options
from guideos.tools.factory import tool_service_from_env
from guideos.reports.renderer import (
    FEEDBACK_LABELS,
    render_profile_report,
    render_profile_summary,
)
from guideos.knowledge.models import KnowledgeError, SourceRef
from guideos.providers.base import LLMProvider, ProviderError
from guideos.providers.registry import get_provider, list_provider_names
from guideos.pilot.access import (
    local_participant_id,
    normalize_participant_name,
    participant_choices,
)
from guideos.pilot.feedback import (
    PilotFeedbackError,
    PostgresPilotFeedbackRepository,
)

logger = logging.getLogger(__name__)


def build_demo_assessment(
    provider: LLMProvider,
    path: Path = DEFAULT_ASSESSMENT_STORAGE_PATH,
) -> AssessmentOrchestrator:
    """An orchestrator over the bundled demo pack (DEFAULT_DOMAIN_PACK_PATH)."""
    return build_assessment(load_domain_pack(), provider, path)


def render_assessment(
    default_domain_id: str | None = None,
    *,
    participant_id: str | None = None,
    pilot_mode: bool = False,
    show_report: bool = False,
) -> None:
    provider_names = list_provider_names()
    provider_name = (
        DEFAULT_PROVIDER
        if pilot_mode
        else st.sidebar.selectbox("Assessment-Provider", provider_names)
    )
    provider = get_provider(provider_name)
    endpoint = provider.default_endpoint
    model = provider.default_model
    if not pilot_mode:
        endpoint = st.sidebar.text_input(
            "Assessment-Endpoint",
            value=provider.default_endpoint,
            key="assessment_endpoint",
        )
        model = select_model(provider, endpoint, container=st.sidebar)
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if pilot_mode and (not participant_id or not database_url):
        st.error("Der Pilotmodus benötigt einen Teilnehmer-Schlüssel und PostgreSQL.")
        return
    repository_path = assessment_storage_path_from_env()
    try:
        active_refs = list_active_domain_versions()
    except DomainCatalogError as exc:
        st.error(str(exc))
        return
    allowed = allowed_domains_from_env()
    if allowed is not None:
        available = {ref.domain_id for ref in active_refs}
        active_refs = [ref for ref in active_refs if ref.domain_id in allowed]
        if not active_refs:
            st.error(
                "GUIDEOS_DOMAINS nennt keine aktive Domäne: "
                f"{', '.join(sorted(allowed))}. Aktiv sind: {', '.join(sorted(available)) or 'keine'}."
            )
            return
    if not active_refs:
        st.error("Es ist keine aktive Assessment-Domain konfiguriert.")
        return

    repository = assessment_repository_from_env(repository_path)
    profiles = profile_repository_from_env()
    conversation_repository = conversation_repository_from_env(
        conversation_storage_path_from_env()
    )
    if participant_id is None:
        # Without pilot login the sidebar imitates a multi-user system: the
        # chosen user name is the participant key of every session, profile,
        # chat and dataset; the default is the fixed local participant.
        try:
            participant_id = _select_participant(repository, profiles, conversation_repository)
        except (AssessmentRepositoryError, ProfileRepositoryError, ConversationRepositoryError) as exc:
            st.error(str(exc))
            return
    selected_ref = _select_domain(active_refs, default_domain_id)
    try:
        pack = load_active_domain_pack(selected_ref.domain_id)
    except (DomainCatalogError, DomainPackError) as exc:
        st.error(str(exc))
        return

    repository = ParticipantAssessmentRepository(repository, participant_id)
    knowledge = knowledge_service_from_env(provider)
    profiles = ParticipantProfileRepository(profiles, participant_id)
    tools = tool_service_from_env()
    orchestrator = build_assessment(
        pack, provider, repository=repository, knowledge=knowledge, profiles=profiles, tools=tools
    )
    conversation_repository = ParticipantConversationRepository(
        conversation_repository, participant_id
    )
    conversation_service = ConversationService(
        conversation_repository,
        profiles,
        provider,
        knowledge=knowledge,
        tools=tools,
    )
    sessions = _sessions_for_pack(orchestrator.repository.list(), pack)
    feedback_repository = (
        PostgresPilotFeedbackRepository(database_url)
        if pilot_mode and database_url
        else None
    )

    st.title(pack.name)
    st.warning(
        "Technische Demo: Die Ergebnisse sind keine verbindliche Beratung."
    )
    if pilot_mode:
        st.info("Interner Pilot: Bitte nur synthetische Testdaten eingeben.")
    st.caption(pack.description)

    with st.sidebar:
        if st.button("Neues Assessment starten", type="primary"):
            response = _run_workflow(lambda: orchestrator.start())
            if response is None:
                return
            st.session_state[_active_session_key(pack)] = response.session_id
            st.rerun()

        if sessions:
            session_ids = [session.session_id for session in sessions]
            active_id = st.session_state.get(_active_session_key(pack))
            if active_id not in session_ids:
                active_id = session_ids[-1]
            selected_id = st.selectbox(
                "Gespeicherte Assessments",
                session_ids,
                index=session_ids.index(active_id),
                format_func=lambda session_id: _session_label(sessions, session_id),
            )
            st.session_state[_active_session_key(pack)] = selected_id
            _render_session_deletion(
                orchestrator, conversation_service, profiles, pack,
                next(session for session in sessions if session.session_id == selected_id),
            )
    if not sessions:
        st.info("Starte ein neues Assessment über die Seitenleiste.")
        return

    try:
        response = orchestrator.get(st.session_state[_active_session_key(pack)])
    except LookupError:
        st.warning("Diese Assessment-Session ist nicht mehr verfügbar.")
        return
    st.progress(
        response.progress.answered_required
        / max(1, response.progress.required_total),
        text=(
            f"{response.progress.answered_required} von "
            f"{response.progress.required_total} Pflichtangaben"
        ),
    )

    if response.status == "in_progress":
        _render_question(
            orchestrator,
            response,
            endpoint,
            model,
        )
    elif response.status == "review":
        _render_review(orchestrator, response, endpoint, model)
    else:
        _render_completed(
            orchestrator,
            conversation_service,
            response,
            endpoint,
            model,
            feedback_repository=feedback_repository,
            participant_id=participant_id,
            show_report=show_report,
            profiles=profiles,
        )


_PARTICIPANT_KEY = "assessment_participant_id"
_NEW_PARTICIPANT_KEY = "assessment_new_user"
_PENDING_PARTICIPANT_KEY = "assessment_pending_participant_id"


def _select_participant(repository, profiles, conversations) -> str:
    """Sidebar choice of the user whose data the assessment shows: every
    participant with stored sessions, profiles or chats, the names entered in
    this browser session and the local default."""
    default = local_participant_id()
    entered = st.session_state.setdefault("assessment_entered_users", [])
    sessions = repository.list()
    stored_profiles = profiles.list()
    stored_conversations = conversations.list()
    # Local JSON records from before the user selection carry no participant;
    # they belong to the default user, as they already do with PostgreSQL.
    for record in [*sessions, *stored_profiles, *stored_conversations]:
        if record.participant_id is None:
            record.participant_id = default
            {
                AssessmentSession: repository,
                Profile: profiles,
                Conversation: conversations,
            }[type(record)].save(record)
    known = [
        *(record.participant_id for record in [*sessions, *stored_profiles, *stored_conversations]),
        *entered,
    ]
    choices = participant_choices(known, default)
    pending = st.session_state.pop(_PENDING_PARTICIPANT_KEY, None)
    if pending in choices:
        st.session_state[_PARTICIPANT_KEY] = pending
    if st.session_state.get(_PARTICIPANT_KEY) not in choices:
        st.session_state[_PARTICIPANT_KEY] = default
    with st.sidebar:
        selected = st.selectbox(
            "Benutzer",
            choices,
            key=_PARTICIPANT_KEY,
            help="Imitiert ein Mehrbenutzersystem: Assessments, Profil, Messdaten und "
            "Beratungschats gehören dem gewählten Benutzer.",
        )
        with st.form("assessment_new_user_form", clear_on_submit=True):
            name = st.text_input("Neuer Benutzername", key=_NEW_PARTICIPANT_KEY)
            submitted = st.form_submit_button("Benutzer anlegen")
        if submitted:
            normalized = normalize_participant_name(name)
            if normalized is None:
                st.warning("Bitte gib einen Benutzernamen ein.")
            else:
                if normalized not in entered:
                    entered.append(normalized)
                st.session_state[_PENDING_PARTICIPANT_KEY] = normalized
                st.rerun()
    return selected


def select_model(
    provider: LLMProvider,
    endpoint: str,
    *,
    container=st,
    label: str = "Assessment-Modell",
    key_prefix: str = "assessment",
) -> str:
    """Offer the endpoint's models in a dropdown; fall back to free text offline."""
    cache = st.session_state.setdefault("available_models", {})
    cache_key = f"{provider.name}@{endpoint}"
    if container.button("Modellliste aktualisieren", key=f"{key_prefix}_models_refresh"):
        cache.pop(cache_key, None)
    if cache_key not in cache:
        try:
            cache[cache_key] = provider.list_models(endpoint)
        except ProviderError as exc:
            cache[cache_key] = str(exc)
    models = cache[cache_key]
    if isinstance(models, list) and models:
        default = provider.default_model if provider.default_model in models else models[0]
        return container.selectbox(
            label,
            models,
            index=models.index(default),
            key=f"{key_prefix}_model_select",
        )
    container.caption(
        "Modellliste nicht abrufbar, Modellname manuell eingeben."
        + (f" ({models})" if isinstance(models, str) and models else "")
    )
    return container.text_input(
        label,
        value=provider.default_model,
        key=f"{key_prefix}_model",
    )


def render_domain_admin() -> None:
    st.title("Domain-Admin")
    st.warning(
        "Lokales Admin-Werkzeug für Demo-Domain-Packs. Aktive Versionen sind "
        "read-only; Änderungen werden als Draft gespeichert."
    )
    try:
        refs = list_domain_versions()
    except DomainCatalogError as exc:
        st.error(str(exc))
        return
    created = st.session_state.pop(_CREATED_PACK_KEY, None)
    if created is not None:
        st.success(
            f"Leeres Domain-Pack als Draft angelegt: {created[0]}/{created[1]}. "
            "Fragen ergänzt du im Tab Fragen, Regeln und Empfehlungen in den Tabs Regeln "
            "und Empfehlungen."
        )
        for ref in refs:
            if (ref.domain_id, ref.version) == created:
                st.session_state[_SELECTED_VERSION_KEY] = ref
    deleted = st.session_state.pop(_DELETED_PACK_KEY, None)
    if deleted is not None:
        st.success(deleted)
    _render_new_pack(refs)
    if not refs:
        st.info("Es wurden keine Domain Packs gefunden.")
        return

    selected_ref = st.selectbox(
        "Domain-Version",
        refs,
        format_func=_domain_ref_label,
        key=_SELECTED_VERSION_KEY,
    )
    _render_delete_version(selected_ref, refs)
    try:
        pack = load_domain_pack_ref(selected_ref)
    except (DomainCatalogError, DomainPackError) as exc:
        st.error(str(exc))
        if selected_ref.error:
            st.caption(selected_ref.error)
        return

    st.caption(f"Quelle: {selected_ref.source} - {selected_ref.location}")
    if selected_ref.active:
        st.info("Diese Version ist aktiv und wird nicht direkt bearbeitet.")
    if selected_ref.source == "bundled":
        st.info("Gebundelte Packs werden nicht direkt bearbeitet.")

    editor = _editor_state(pack)
    working = pack_with_recommendations(
        pack_with_rules(
            pack_with_topics(pack_with_questions(pack, editor["questions"]), editor["topics"]),
            editor["rules"],
        ),
        editor["recommendations"],
    )
    tabs = st.tabs(
        [
            "Metadaten", "Fragen", "Regeln", "Empfehlungen", "JSON-Draft", "Aktivieren", "SOyA",
            "Wissen", "Werkzeuge",
        ]
    )
    with tabs[0]:
        draft_pack = _render_metadata_editor(working)
    with tabs[1]:
        _render_question_editor(selected_ref, refs, pack, editor, draft_pack)
    with tabs[2]:
        _render_rules(selected_ref, refs, working, editor, draft_pack)
    with tabs[3]:
        _render_recommendations(selected_ref, refs, working, editor, draft_pack)
    with tabs[4]:
        _render_json_draft_editor(working)
    with tabs[5]:
        _render_activation(selected_ref, draft_pack)
    with tabs[6]:
        _render_soya(selected_ref, refs, pack)
    with tabs[7]:
        _render_knowledge(pack)
    with tabs[8]:
        _render_tool_servers(pack)


_SELECTED_VERSION_KEY = "domain_admin_version"
_CREATED_PACK_KEY = "domain_admin_created_pack"
_DELETED_PACK_KEY = "domain_admin_deleted_pack"
_NEW_PACK_KEYS = ("new_pack_domain_id", "new_pack_name", "new_pack_description")


def _render_new_pack(refs: list[DomainPackRef]) -> None:
    with st.expander("Neues Domain-Pack erstellen"):
        st.caption(
            f"Legt ein leeres Pack als Draft in Version {NEW_PACK_VERSION} an und wählt "
            "es aus. Sichtbar im Assessment wird es erst nach dem Aktivieren."
        )
        domain_id = st.text_input(
            "Domain-ID des neuen Packs",
            key=_NEW_PACK_KEYS[0],
            help="Kleinbuchstaben, Ziffern und Bindestriche, zum Beispiel sanierungs-check.",
        )
        name = st.text_input("Name des neuen Packs", key=_NEW_PACK_KEYS[1])
        description = st.text_area(
            "Beschreibung des neuen Packs (optional)", key=_NEW_PACK_KEYS[2]
        )
        if not st.button("Neues Domain-Pack erstellen", type="primary"):
            return
        try:
            pack = new_domain_pack(domain_id, name, description, refs)
            save_domain_pack_draft(pack)
        except (EditorError, DomainCatalogError) as exc:
            st.error(str(exc))
            return
        for key in _NEW_PACK_KEYS:
            del st.session_state[key]
        st.session_state[_CREATED_PACK_KEY] = (pack.domain_id, pack.version)
        st.rerun()


def _render_delete_version(ref: DomainPackRef, refs: list[DomainPackRef]) -> None:
    label = f"{ref.domain_id}/{ref.version}"
    last_version = not any(
        other.domain_id == ref.domain_id
        and (other.version, other.source) != (ref.version, ref.source)
        for other in refs
    )
    with st.expander("Ausgewählte Version löschen"):
        if ref.source == "bundled":
            st.caption(
                "Gebündelte Packs lassen sich nicht im Admin löschen; sie liegen im "
                "Repository unter domain_packs/."
            )
            return
        if ref.active:
            st.caption(
                f"Version {label} ist aktiv. Aktiviere zuerst eine andere Version, "
                "dann lässt sie sich löschen."
            )
            return
        knowledge = knowledge_service_from_env(get_provider(DEFAULT_PROVIDER))
        if last_version:
            try:
                documents = len(knowledge.list_documents(ref.domain_id))
            except KnowledgeError as exc:
                st.error(str(exc))
                return
            st.caption(
                f"Das ist die letzte Version der Domäne {ref.domain_id}. Mit ihr werden "
                f"auch die {documents} Dokument(e) ihrer Wissensbasis gelöscht. Das "
                "Löschen lässt sich nicht rückgängig machen."
            )
        else:
            st.caption(
                "Entfernt nur diesen Draft. Andere Versionen der Domäne und ihre "
                "Wissensbasis bleiben erhalten. Das Löschen lässt sich nicht rückgängig "
                "machen."
            )
        confirmed = st.checkbox(
            f"Ich möchte {label} endgültig löschen",
            key=f"delete_confirm_{ref.domain_id}_{ref.version}",
        )
        if not st.button("Version löschen", disabled=not confirmed):
            return
        try:
            sessions = sum(
                1
                for session in assessment_repository_from_env(
                    assessment_storage_path_from_env()
                ).list()
                if session.domain_id == ref.domain_id and session.domain_version == ref.version
            )
            if sessions:
                st.error(
                    f"Zu Version {label} gibt es noch {sessions} Assessment-Session(s). "
                    "Lösche sie zuerst; sonst ließen sie sich nicht mehr öffnen."
                )
                return
            delete_domain_pack_draft(ref.domain_id, ref.version)
        except (AssessmentRepositoryError, DomainCatalogError) as exc:
            st.error(str(exc))
            return
        notice = f"Version {label} wurde gelöscht."
        if last_version:
            # The pack goes first: a failure here leaves documents behind instead
            # of a pack that silently lost its knowledge base.
            try:
                removed = knowledge.delete_domain(ref.domain_id)
            except (KnowledgeError, LookupError) as exc:
                st.error(
                    f"{notice} Die Wissensbasis der Domäne konnte nicht entfernt werden: {exc}"
                )
                return
            notice += f" Mit ihr {removed} Dokument(e) der Wissensbasis."
        del st.session_state[_SELECTED_VERSION_KEY]
        st.session_state[_DELETED_PACK_KEY] = notice
        st.rerun()


def _select_domain(
    active_refs: list[DomainPackRef],
    default_domain_id: str | None,
) -> DomainPackRef:
    index = 0
    labels = [_domain_select_label(ref) for ref in active_refs]
    refs_by_label = dict(zip(labels, active_refs, strict=True))
    if default_domain_id:
        for item_index, ref in enumerate(active_refs):
            if ref.domain_id == default_domain_id:
                index = item_index
                break
    selected = st.sidebar.selectbox(
        "Assessment-Domain",
        labels,
        index=index,
    )
    return refs_by_label[selected]


def _domain_select_label(ref: DomainPackRef) -> str:
    name = ref.name or ref.domain_id
    if ref.version:
        return f"{name} ({ref.version})"
    return name


def _sessions_for_pack(sessions, pack: DomainPack):
    return [
        session for session in sessions
        if session.domain_id == pack.domain_id
        and session.domain_version == pack.version
        and session.domain_content_hash == pack.content_hash
    ]


def _active_session_key(pack: DomainPack) -> str:
    return f"active_assessment_id_{pack.domain_id}_{pack.version}_{pack.content_hash}"


def _domain_ref_label(ref: DomainPackRef) -> str:
    status = "aktiv" if ref.active else "draft"
    validity = "gültig" if ref.valid else "ungültig"
    name = ref.name or ref.domain_id
    return f"{name} ({ref.domain_id}/{ref.version}, {ref.source}, {status}, {validity})"


def _render_metadata_editor(pack: DomainPack) -> DomainPack:
    st.subheader("Metadaten")
    with st.form(f"metadata_{pack.domain_id}_{pack.version}"):
        domain_id = st.text_input("Domain-ID", value=pack.domain_id)
        version = st.text_input("Version", value=_next_patch_version(pack.version))
        name = st.text_input("Name", value=pack.name)
        description = st.text_area("Beschreibung", value=pack.description)
        default_locale = st.text_input("Default-Locale", value=pack.default_locale)
        web = pack.web_sources
        web_enabled = st.checkbox(
            "Webquellen der Wissensbasis in der Beratung verwenden",
            value=web is not None and web.enabled,
        )
        web_domains = st.text_input(
            "Erlaubte Web-Domains (kommagetrennt, leer = alle)",
            value=", ".join(web.allowed_domains) if web else "",
        )
        guidance = st.text_area(
            "Beratungsvorgaben für das Modell (optional)",
            value=pack.advisory_guidance or "",
            help=(
                "Fachliche Vorgaben dieser Domäne für Beratungsantworten und "
                "KI-Empfehlungen, etwa der geltende Länderkontext oder Themen, die "
                "nur mit Quelle genannt werden dürfen. Leer = keine Vorgaben."
            ),
        )
        compact_chat = st.checkbox(
            "Empfehlungen im Chat „Ergebnis“ kompakt anzeigen",
            value=pack.result_chat is not None and pack.result_chat.recommendations == "compact",
            help=(
                "Kompakt: je Empfehlung eine Zeile mit Titel und Kurztext, Quellen und Gründe "
                "nur im Bericht und als Belege der Nachricht. Ohne Häkchen erscheint jede "
                "Empfehlung mit Quelle, Gründen und Bezug."
            ),
        )
        knowledge = pack.knowledge_recommendations
        knowledge_enabled = st.checkbox(
            "Zusätzliche KI-Empfehlungen beim Abschluss erzeugen",
            value=knowledge is None or knowledge.enabled,
            help=(
                "Nach dem Abschluss erzeugt das Modell zusätzliche, als KI-Empfehlung gekennzeichnete "
                "Hinweise aus der Wissensbasis. Ohne Häkchen gibt es nur die Regel-Empfehlungen."
            ),
        )
        knowledge_max = st.number_input(
            "Höchstzahl KI-Empfehlungen (1–5)",
            min_value=1, max_value=5, step=1,
            value=knowledge.max_count if knowledge is not None else 5,
        )
        chat = pack.result_chat
        chat_intro = st.text_area(
            "Einleitung im Chat „Ergebnis“ (optional)",
            value=(chat.intro if chat else None) or "",
            help=f"Erste Nachricht nach dem Abschluss. Leer = Standardtext: „{RESULT_INTRO}“",
        )
        chat_updated_intro = st.text_area(
            "Einleitung nach einem neuen Assessment (optional)",
            value=(chat.updated_intro if chat else None) or "",
            help=f"Nachricht, wenn ein neues Assessment das Profil ersetzt hat. Leer = Standardtext: „{RESULT_UPDATED_INTRO}“",
        )
        chat_offer = st.text_area(
            "Angebot für KI-Empfehlungen (optional)",
            value=(chat.offer if chat else None) or "",
            help=(
                "Frage, ob zusätzliche KI-Empfehlungen erzeugt werden sollen; erscheint nur, wenn beim "
                f"Abschluss keine entstanden sind. Ein kurzes Ja oder Nein darauf wertet GuideOS "
                f"deterministisch aus. Leer = Standardtext: „{RECOMMENDATION_OFFER}“"
            ),
        )
        save = st.form_submit_button("Als Draft speichern")
    try:
        result_chat = result_chat_from_form(compact_chat, chat_intro, chat_updated_intro, chat_offer)
    except EditorError as exc:
        st.error(str(exc))
        return pack
    draft = pack.model_copy(
        update={
            "domain_id": domain_id.strip(),
            "version": version.strip(),
            "name": name.strip(),
            "description": description.strip(),
            "default_locale": default_locale.strip(),
            "web_sources": _web_sources_from_form(web_enabled, web_domains),
            "advisory_guidance": guidance.strip() or None,
            "result_chat": result_chat,
            "knowledge_recommendations": knowledge_recommendations_from_form(
                knowledge_enabled, knowledge_max
            ),
            "content_hash": "",
        },
        deep=True,
    )
    if save:
        try:
            path = save_domain_pack_draft(draft)
        except (DomainCatalogError, DomainPackError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Draft gespeichert: {path}")
    return draft


def _web_sources_from_form(enabled: bool, domains_text: str) -> WebSourceSettings | None:
    """None while nothing is configured, so such packs serialize as before."""
    domains = [item.strip().lower() for item in domains_text.split(",") if item.strip()]
    if not enabled and not domains:
        return None
    return WebSourceSettings(enabled=enabled, allowed_domains=domains)


def _editor_state(pack: DomainPack) -> dict[str, Any]:
    key = f"question_editor_{pack.domain_id}_{pack.version}_{pack.content_hash}"
    if key not in st.session_state:
        st.session_state[key] = {
            "questions": list(pack.questions),
            "pending_selection": None,
            "rules": list(pack.rules),
            "topics": list(pack.topics or []),
            "pending_rule": None,
            "recommendations": list(pack.recommendations),
            "pending_recommendation": None,
        }
    return st.session_state[key]


def _render_question_editor(
    ref: DomainPackRef,
    refs: list[DomainPackRef],
    pack: DomainPack,
    editor: dict[str, Any],
    draft_pack: DomainPack,
) -> None:
    st.subheader("Fragen")
    questions: list[QuestionDefinition] = editor["questions"]
    rows = [
        {
            "Position": position + 1,
            "question_id": question.question_id,
            "field_key": question.field_key,
            "text": question.text,
            "field_type": question.field_type,
            "widget": effective_widget(question) or "",
            "required": question.required,
            "allow_unknown": question.allow_unknown,
            "options": ", ".join(
                f"{option.value}:{option.label}" for option in question.options
            ),
            "visible_if": _visible_if_label(question),
        }
        for position, question in enumerate(questions)
    ]
    st.dataframe(rows, width="stretch")
    error = draft_error(pack_with_questions(pack, questions))
    if error:
        st.error(f"Der aktuelle Stand ist ungültig: {error}")

    select_key = f"question_select_{ref.domain_id}_{ref.version}"
    if editor["pending_selection"] is not None:
        st.session_state[select_key] = editor["pending_selection"]
        editor["pending_selection"] = None
    if questions and st.session_state.get(select_key, 0) >= len(questions):
        st.session_state[select_key] = len(questions) - 1
    index = None
    if questions:
        index = st.selectbox(
            "Frage bearbeiten",
            list(range(len(questions))),
            format_func=lambda i: f"{i + 1}. {questions[i].question_id}: {questions[i].text[:40]}",
            key=select_key,
        )
    else:
        st.session_state.pop(select_key, None)
        st.info("Dieses Pack enthält noch keine Fragen.")

    columns = st.columns(5)
    if columns[0].button("Neue Frage", key=f"question_new_{ref.domain_id}_{ref.version}"):
        editor["questions"] = [*questions, new_question(questions)]
        editor["pending_selection"] = len(questions)
        st.rerun()
    if index is not None:
        if columns[1].button("Nach oben", key=f"question_up_{ref.domain_id}_{ref.version}"):
            editor["questions"] = move_question(questions, index, -1)
            editor["pending_selection"] = max(index - 1, 0)
            st.rerun()
        if columns[2].button("Nach unten", key=f"question_down_{ref.domain_id}_{ref.version}"):
            editor["questions"] = move_question(questions, index, 1)
            editor["pending_selection"] = min(index + 1, len(questions) - 1)
            st.rerun()
        if columns[3].button("Frage löschen", key=f"question_delete_{ref.domain_id}_{ref.version}"):
            editor["questions"] = delete_question(questions, index)
            editor["pending_selection"] = min(index, len(questions) - 2) if len(questions) > 1 else None
            st.rerun()
    if columns[4].button("Änderungen verwerfen", key=f"question_reset_{ref.domain_id}_{ref.version}"):
        editor["questions"] = list(pack.questions)
        editor["pending_selection"] = 0 if pack.questions else None
        st.rerun()

    if index is not None:
        _render_question_form(ref, questions, index, editor)

    st.caption(
        f"Gespeichert wird nach {draft_pack.domain_id}/{draft_pack.version} "
        "(Version aus dem Tab Metadaten)."
    )
    if st.button("Fragen als Draft speichern", key=f"question_save_{ref.domain_id}_{ref.version}"):
        try:
            assert_version_editable(draft_pack.domain_id, draft_pack.version, refs)
            invalid = draft_error(draft_pack)
            if invalid:
                raise DomainPackError(invalid)
            path = save_domain_pack_draft(draft_pack)
        except (DomainCatalogError, DomainPackError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Draft gespeichert: {path}")


def _render_question_form(
    ref: DomainPackRef,
    questions: list[QuestionDefinition],
    index: int,
    editor: dict[str, Any],
) -> None:
    question = questions[index]
    suffix = f"{ref.domain_id}_{ref.version}_{index}_{question.question_id}"
    type_options = list(FIELD_TYPE_LABELS)
    field_type = st.selectbox(
        "Feldtyp",
        type_options,
        index=type_options.index(question.field_type),
        format_func=FIELD_TYPE_LABELS.get,
        key=f"question_type_{suffix}",
    )
    same_type = field_type == question.field_type
    validation = question.validation if same_type else None
    values: dict[str, Any] = {"field_type": field_type}

    with st.form(f"question_form_{suffix}"):
        values["question_id"] = st.text_input("Frage-ID", value=question.question_id)
        values["field_key"] = st.text_input("Feldschlüssel", value=question.field_key)
        values["text"] = st.text_input("Fragetext", value=question.text)
        values["help_text"] = st.text_area("Hilfetext", value=question.help_text or "")
        if field_type in WIDGETS_BY_TYPE:
            choices = [None, *WIDGETS_BY_TYPE[field_type]]
            current = question.widget if same_type and question.widget in choices else None
            values["widget"] = st.selectbox(
                "Darstellung",
                choices,
                index=choices.index(current),
                format_func=lambda w: "Standard" if w is None else WIDGET_LABELS[w],
            )
        values["required"] = st.checkbox("Pflichtfrage", value=question.required)
        values["allow_unknown"] = st.checkbox("Unbekannt erlaubt", value=question.allow_unknown)

        if field_type in CHOICE_TYPES:
            option_rows = [
                {"value": option.value, "label": option.label} for option in question.options
            ] or [{"value": "", "label": ""}]
            values["option_rows"] = st.data_editor(
                option_rows,
                num_rows="dynamic",
                column_config={
                    "value": st.column_config.TextColumn("Wert"),
                    "label": st.column_config.TextColumn("Bezeichnung"),
                },
                key=f"options_{suffix}",
            )

        if field_type in ("integer", "number"):
            step = 1 if field_type == "integer" else 1.0
            convert = int if field_type == "integer" else float
            values["minimum"] = st.number_input(
                "Minimum",
                value=convert(validation.minimum) if validation and validation.minimum is not None else None,
                step=step,
            )
            values["maximum"] = st.number_input(
                "Maximum",
                value=convert(validation.maximum) if validation and validation.maximum is not None else None,
                step=step,
            )
        elif field_type == "text":
            values["min_length"] = st.number_input(
                "Mindestlänge", value=validation.min_length if validation else None, min_value=0, step=1
            )
            values["max_length"] = st.number_input(
                "Maximallänge", value=validation.max_length if validation else None, min_value=1, step=1
            )
            values["pattern"] = st.text_input(
                "Muster (regulärer Ausdruck)", value=(validation.pattern if validation else None) or ""
            )
        elif field_type == "multiselect":
            values["min_count"] = st.number_input(
                "Mindestanzahl", value=validation.min_count if validation else None, min_value=0, step=1
            )
            values["max_count"] = st.number_input(
                "Höchstanzahl", value=validation.max_count if validation else None, min_value=1, step=1
            )
        elif field_type == "date":
            values["min_date"] = st.date_input(
                "Frühestes Datum",
                value=date.fromisoformat(validation.min_date) if validation and validation.min_date else None,
                format="DD.MM.YYYY",
            )
            values["max_date"] = st.date_input(
                "Spätestes Datum",
                value=date.fromisoformat(validation.max_date) if validation and validation.max_date else None,
                format="DD.MM.YYYY",
            )
        elif field_type == "file":
            upload = question.upload if same_type else None
            st.caption(
                "Die Datei geht an das Importwerkzeug des registrierten Servers (Tab Werkzeuge); "
                "GuideOS speichert nur die Datensatzreferenz. Das Pack braucht dafür den Block tools."
            )
            values["upload_tool"] = st.text_input(
                "Importwerkzeug (leer: dataset_tool des Packs)", value=(upload.tool if upload else None) or ""
            )
            values["upload_arguments"] = st.text_input(
                "Feste Argumente (JSON-Objekt)",
                value=json.dumps(upload.arguments, ensure_ascii=False) if upload and upload.arguments else "",
            )
            values["upload_extensions"] = st.text_input(
                "Erlaubte Endungen (kommagetrennt)",
                value=", ".join(upload.extensions) if upload else DEFAULT_UPLOAD_EXTENSIONS,
            )
            values["upload_max_mib"] = st.number_input(
                "Größengrenze (MiB)",
                value=upload.max_bytes / (1024 * 1024) if upload else float(DEFAULT_UPLOAD_MIB),
                min_value=0.1, step=1.0,
            )
            values["use_in_advisory"] = st.checkbox(
                "Datensatz für die Beratung verwenden",
                value=upload.use_in_advisory if upload else True,
                help="Höchstens eine Datei-Frage je Pack; ihr Datensatz steht dem Modell als DATASET "
                     "zur Verfügung und bekommt die erste Analyse.",
            )

        earlier_keys = [None, *[item.field_key for item in questions[:index]]]
        condition = question.visible_if
        current_key = condition.field_key if condition and condition.field_key in earlier_keys else None
        values["visible_field_key"] = st.selectbox(
            "Sichtbar nur wenn Feld",
            earlier_keys,
            index=earlier_keys.index(current_key),
            format_func=lambda key: "(immer sichtbar)" if key is None else key,
        )
        operators = list(OPERATOR_LABELS)
        values["visible_operator"] = st.selectbox(
            "Bedingung",
            operators,
            index=operators.index(condition.operator) if condition else 0,
            format_func=OPERATOR_LABELS.get,
        )
        values["visible_value"] = st.text_input(
            "Vergleichswert (bei Listen kommagetrennt)",
            value=_condition_value_text(condition),
        )
        submitted = st.form_submit_button("Frage übernehmen")

    if submitted:
        try:
            updated = question_from_form(values, earlier_questions=questions[:index])
        except EditorError as exc:
            st.error(str(exc))
            return
        editor["questions"] = [*questions[:index], updated, *questions[index + 1:]]
        st.rerun()


def _condition_value_text(condition: VisibilityCondition | None) -> str:
    if condition is None or condition.operator == "is_missing" or condition.value is None:
        return ""
    value = condition.value
    if isinstance(value, list):
        return ", ".join(_condition_value_text_item(item) for item in value)
    return _condition_value_text_item(value)


def _condition_value_text_item(value: Any) -> str:
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return str(value)


def _render_rules(
    ref: DomainPackRef,
    refs: list[DomainPackRef],
    pack: DomainPack,
    editor: dict[str, Any],
    draft_pack: DomainPack,
) -> None:
    """Rules with kind, topics, origin, source and status; status moves
    Entwurf -> Prüfung -> freigegeben; topics as one line each."""
    st.subheader("Regeln")
    rules: list[RuleDefinition] = editor["rules"]
    rows = [
        {
            "rule_id": rule.rule_id,
            "kind": RULE_KIND_LABELS[rule.kind],
            "field_key": rule.field_key,
            "operator": rule.operator,
            "value": json.dumps(rule.value, ensure_ascii=False),
            "divisor_field_key": rule.divisor_field_key,
            "reason_code": rule.reason_code,
            "recommendation_ids": ", ".join(rule.recommendation_ids),
            "topics": ", ".join(rule.topics),
            "priority": rule.priority,
            "status": RULE_STATUS_LABELS[rule.status],
            "origin": RULE_ORIGIN_LABELS[rule.origin],
            "source_reference": rule.source_reference,
        }
        for rule in rules
    ]
    st.dataframe(rows, width="stretch")
    open_rules = unapproved_rules(pack)
    if open_rules:
        st.warning(
            "Nicht freigegebene Regeln (die Version lässt sich erst nach der Freigabe "
            "aktivieren): " + ", ".join(open_rules)
        )
    suffix = f"{ref.domain_id}_{ref.version}"
    select_key = f"rule_select_{suffix}"
    if editor["pending_rule"] is not None:
        st.session_state[select_key] = editor["pending_rule"]
        editor["pending_rule"] = None
    if rules and st.session_state.get(select_key, 0) >= len(rules):
        st.session_state[select_key] = len(rules) - 1
    index = None
    if rules:
        index = st.selectbox(
            "Regel bearbeiten",
            list(range(len(rules))),
            format_func=lambda i: f"{rules[i].rule_id} ({RULE_STATUS_LABELS[rules[i].status]})",
            key=select_key,
        )
    else:
        st.session_state.pop(select_key, None)
        st.info("Dieses Pack enthält noch keine Regeln.")

    columns = st.columns(3)
    if columns[0].button("Neue Regel", key=f"rule_new_{suffix}"):
        try:
            editor["rules"] = [*rules, new_rule(pack)]
        except EditorError as exc:
            st.error(str(exc))
            return
        editor["pending_rule"] = len(rules)
        st.rerun()
    if index is not None and columns[1].button("Regel löschen", key=f"rule_delete_{suffix}"):
        editor["rules"] = delete_rule(rules, index)
        editor["pending_rule"] = min(index, len(rules) - 2) if len(rules) > 1 else None
        st.rerun()
    if columns[2].button("Regeländerungen verwerfen", key=f"rule_reset_{suffix}"):
        editor["rules"] = list(_stored_pack_rules(ref))
        editor["topics"] = list(_stored_pack_topics(ref))
        st.rerun()

    if index is not None:
        _render_rule_status_buttons(rules, index, editor, suffix)
        _render_rule_form(pack, rules, index, editor, suffix)

    st.markdown("**Themen** (eine Zeile je Thema als `topic_id: Bezeichnung`)")
    topics_text = st.text_area(
        "Themen (topic_id: Bezeichnung je Zeile)",
        value=topics_to_text(editor["topics"]),
        key=f"topics_text_{suffix}",
        height=120,
    )
    if st.button("Themen übernehmen", key=f"topics_apply_{suffix}"):
        try:
            editor["topics"] = topics_from_text(topics_text)
        except EditorError as exc:
            st.error(str(exc))
            return
        st.rerun()

    st.caption(
        f"Gespeichert wird nach {draft_pack.domain_id}/{draft_pack.version} "
        "(Version aus dem Tab Metadaten)."
    )
    if st.button("Regeln als Draft speichern", key=f"rule_save_{suffix}"):
        try:
            assert_version_editable(draft_pack.domain_id, draft_pack.version, refs)
            invalid = draft_error(draft_pack)
            if invalid:
                raise DomainPackError(invalid)
            path = save_domain_pack_draft(draft_pack)
        except (DomainCatalogError, DomainPackError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Draft gespeichert: {path}")


def _stored_pack_rules(ref: DomainPackRef) -> list[RuleDefinition]:
    return load_domain_pack_version(ref.domain_id, ref.version).rules


def _stored_pack_topics(ref: DomainPackRef) -> list[TopicDefinition]:
    return load_domain_pack_version(ref.domain_id, ref.version).topics or []


def _render_rule_status_buttons(
    rules: list[RuleDefinition], index: int, editor: dict[str, Any], suffix: str
) -> None:
    rule = rules[index]
    st.caption(f"Status: {RULE_STATUS_LABELS[rule.status]} · Herkunft: {RULE_ORIGIN_LABELS[rule.origin]}")
    columns = st.columns(3)
    transitions = [
        ("review", "Zur Prüfung geben", columns[0]),
        ("approved", "Freigeben", columns[1]),
        ("draft", "Zurück auf Entwurf", columns[2]),
    ]
    for status, label, column in transitions:
        allowed = status in RULE_STATUS_TRANSITIONS[rule.status]
        if column.button(label, key=f"rule_status_{status}_{suffix}", disabled=not allowed):
            editor["rules"] = [*rules[:index], set_rule_status(rule, status), *rules[index + 1:]]
            st.rerun()


def _render_rule_form(
    pack: DomainPack,
    rules: list[RuleDefinition],
    index: int,
    editor: dict[str, Any],
    suffix: str,
) -> None:
    rule = rules[index]
    field_keys = [question.field_key for question in pack.questions]
    recommendation_ids = [item.recommendation_id for item in pack.recommendations]
    topic_ids = [topic.topic_id for topic in pack.topics or []]
    values: dict[str, Any] = {}
    with st.form(f"rule_form_{suffix}_{index}"):
        values["rule_id"] = st.text_input("Regel-ID", value=rule.rule_id)
        values["kind"] = st.selectbox(
            "Regelart", list(RULE_KIND_LABELS), index=list(RULE_KIND_LABELS).index(rule.kind),
            format_func=RULE_KIND_LABELS.get,
        )
        values["field_key"] = st.selectbox(
            "Feld", field_keys, index=field_keys.index(rule.field_key) if rule.field_key in field_keys else 0,
        )
        operators = list(RULE_OPERATOR_LABELS)
        values["operator"] = st.selectbox(
            "Operator", operators, index=operators.index(rule.operator), format_func=RULE_OPERATOR_LABELS.get,
        )
        values["value"] = st.text_input(
            "Vergleichswert (bei Listen kommagetrennt)",
            value="" if rule.operator == "is_missing" or rule.value is None else (
                ", ".join(str(item) for item in rule.value) if isinstance(rule.value, list) else str(rule.value)
            ),
        )
        values["divisor_field_key"] = st.selectbox(
            "Divisor-Feld (optional, für Verhältnisse)", ["", *field_keys],
            index=(["", *field_keys].index(rule.divisor_field_key) if rule.divisor_field_key in field_keys else 0),
        )
        metric = rule.metric
        values["metric_tool"] = st.text_input(
            "Werkzeug (Kennzahl)", value=metric.tool if metric else "",
            help=(
                "Optional: Die Regel vergleicht statt der Antwort eine Kennzahl aus dem letzten "
                "Ergebnis dieses Werkzeugs (eines aus tools.after_import). Das Feld muss dann die "
                "Datei-Frage sein, der Vergleichswert eine Zahl."
            ),
        )
        values["metric_pointer"] = st.text_input(
            "JSON-Pointer (Kennzahl)", value=metric.pointer if metric else "",
            help="Pfad zur Zahl im strukturierten Ergebnis, zum Beispiel /totals/months_above.",
        )
        values["metric_label"] = st.text_input(
            "Bezeichnung (Kennzahl)", value=(metric.label if metric else None) or "",
            help="Optionale deutsche Bezeichnung der Kennzahl für die Belegzeile.",
        )
        values["reason_code"] = st.text_input("Reason Code", value=rule.reason_code)
        values["recommendation_ids"] = st.multiselect(
            "Empfehlungen (Pflicht-Empfehlung)", recommendation_ids,
            default=[item for item in rule.recommendation_ids if item in recommendation_ids],
        )
        values["topics"] = st.multiselect(
            "Themen (Ausschluss, Priorisierung)", topic_ids,
            default=[item for item in rule.topics if item in topic_ids],
        )
        values["priority"] = st.text_input(
            "Priorität (Priorisierung)", value="" if rule.priority is None else str(rule.priority)
        )
        values["origin"] = st.selectbox(
            "Herkunft", list(RULE_ORIGIN_LABELS), index=list(RULE_ORIGIN_LABELS).index(rule.origin),
            format_func=RULE_ORIGIN_LABELS.get,
        )
        values["source_reference"] = st.text_input("Fachquelle", value=rule.source_reference or "")
        values["status"] = st.selectbox(
            "Status", list(RULE_STATUS_LABELS), index=list(RULE_STATUS_LABELS).index(rule.status),
            format_func=RULE_STATUS_LABELS.get, disabled=True,
            help="Den Status änderst du über die Schaltflächen oberhalb des Formulars.",
        )
        submitted = st.form_submit_button("Regel übernehmen")
    if submitted:
        values["status"] = rule.status
        try:
            updated = rule_from_form(values, pack_with_rules(pack, rules), index=index)
        except EditorError as exc:
            st.error(str(exc))
            return
        editor["rules"] = [*rules[:index], updated, *rules[index + 1:]]
        st.rerun()


def _render_recommendations(
    ref: DomainPackRef,
    refs: list[DomainPackRef],
    pack: DomainPack,
    editor: dict[str, Any],
    draft_pack: DomainPack,
) -> None:
    """The recommendation catalog: what a rule of kind Pflicht-Empfehlung
    shows, ordered by priority; edited in a form like the rules."""
    st.subheader("Empfehlungen")
    recommendations: list[RecommendationDefinition] = editor["recommendations"]
    rows = [
        {
            "recommendation_id": recommendation.recommendation_id,
            "priority": recommendation.priority,
            "title": recommendation.title,
            "message": recommendation.message,
            "source_reference": recommendation.source_reference,
        }
        for recommendation in recommendations
    ]
    st.dataframe(rows, width="stretch")
    suffix = f"{ref.domain_id}_{ref.version}"
    select_key = f"recommendation_select_{suffix}"
    if editor["pending_recommendation"] is not None:
        st.session_state[select_key] = editor["pending_recommendation"]
        editor["pending_recommendation"] = None
    if recommendations and st.session_state.get(select_key, 0) >= len(recommendations):
        st.session_state[select_key] = len(recommendations) - 1
    index = None
    if recommendations:
        index = st.selectbox(
            "Empfehlung bearbeiten",
            list(range(len(recommendations))),
            format_func=lambda i: (
                f"{recommendations[i].recommendation_id} (Priorität {recommendations[i].priority})"
            ),
            key=select_key,
        )
    else:
        st.session_state.pop(select_key, None)
        st.info("Dieses Pack enthält noch keine Empfehlungen.")

    columns = st.columns(3)
    if columns[0].button("Neue Empfehlung", key=f"recommendation_new_{suffix}"):
        editor["recommendations"] = [*recommendations, new_recommendation(pack)]
        editor["pending_recommendation"] = len(recommendations)
        st.rerun()
    if index is not None and columns[1].button("Empfehlung löschen", key=f"recommendation_delete_{suffix}"):
        try:
            editor["recommendations"] = delete_recommendation(pack, index)
        except EditorError as exc:
            st.error(str(exc))
            return
        editor["pending_recommendation"] = (
            min(index, len(recommendations) - 2) if len(recommendations) > 1 else None
        )
        st.rerun()
    if columns[2].button("Empfehlungsänderungen verwerfen", key=f"recommendation_reset_{suffix}"):
        editor["recommendations"] = list(load_domain_pack_version(ref.domain_id, ref.version).recommendations)
        st.rerun()

    if index is not None:
        _render_recommendation_form(pack, recommendations, index, editor, suffix)

    st.caption(
        f"Gespeichert wird nach {draft_pack.domain_id}/{draft_pack.version} "
        "(Version aus dem Tab Metadaten)."
    )
    if st.button("Empfehlungen als Draft speichern", key=f"recommendation_save_{suffix}"):
        try:
            assert_version_editable(draft_pack.domain_id, draft_pack.version, refs)
            invalid = draft_error(draft_pack)
            if invalid:
                raise DomainPackError(invalid)
            path = save_domain_pack_draft(draft_pack)
        except (DomainCatalogError, DomainPackError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Draft gespeichert: {path}")


def _render_recommendation_form(
    pack: DomainPack,
    recommendations: list[RecommendationDefinition],
    index: int,
    editor: dict[str, Any],
    suffix: str,
) -> None:
    recommendation = recommendations[index]
    values: dict[str, Any] = {}
    with st.form(f"recommendation_form_{suffix}_{index}"):
        values["recommendation_id"] = st.text_input("Empfehlungs-ID", value=recommendation.recommendation_id)
        values["priority"] = st.text_input(
            "Priorität (Empfehlung)", value=str(recommendation.priority),
            help="Reihenfolge im Ergebnis: 1 steht ganz oben.",
        )
        values["title"] = st.text_input("Titel", value=recommendation.title)
        values["message"] = st.text_area("Nachricht", value=recommendation.message, height=120)
        values["source_reference"] = st.text_input(
            "Fachquelle (Empfehlung)", value=recommendation.source_reference or "",
            help="Optional; wird als „Fachquelle“ unter der Empfehlung angezeigt.",
        )
        analysis_tools = [step.tool for step in pack.tools.analysis_steps()] if pack.tools else []
        values["message_with_figures"] = st.text_area(
            "Text mit Kennzahlen (optional)", value=recommendation.message_with_figures or "", height=120,
            help="Ersetzt die Nachricht, sobald alle Kennzahlen aus den Werkzeugergebnissen vorliegen; "
                 "Platzhalter wie {months_above} in geschweiften Klammern. Fehlt eine Kennzahl, bleibt die "
                 "Nachricht stehen.",
        )
        values["figures"] = st.text_area(
            "Kennzahlen (optional)", value=figures_to_text(recommendation.figures), height=100,
            help=f"{FIGURE_LINE_HINT} Werkzeuge: "
                 + (", ".join(analysis_tools) if analysis_tools else "keine erste Analyse im Pack-Block tools")
                 + ". Die Dezimalstellen runden, die Zuordnung ersetzt Werte durch Texte.",
        )
        submitted = st.form_submit_button("Empfehlung übernehmen")
    if submitted:
        try:
            updated = recommendation_from_form(
                values, pack_with_recommendations(pack, recommendations), index=index
            )
        except EditorError as exc:
            st.error(str(exc))
            return
        editor["recommendations"] = [*recommendations[:index], updated, *recommendations[index + 1:]]
        st.rerun()


def _render_json_draft_editor(pack: DomainPack) -> None:
    st.subheader("JSON-Draft")
    draft_json = json.dumps(
        pack_draft_payload(pack),
        ensure_ascii=False,
        indent=2,
    )
    with st.form(f"json_draft_{pack.domain_id}_{pack.version}"):
        edited = st.text_area("Pack JSON", value=draft_json, height=420)
        save = st.form_submit_button("JSON als Draft speichern")
    if save:
        try:
            data = json.loads(edited)
            if not isinstance(data, dict):
                raise ValueError("Das JSON muss ein Objekt sein.")
            path = save_domain_pack_draft_data(data)
        except (json.JSONDecodeError, DomainCatalogError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Draft gespeichert: {path}")


def _render_activation(ref: DomainPackRef, draft_pack: DomainPack) -> None:
    st.subheader("Validieren und aktivieren")
    if st.button("Draft validieren"):
        try:
            save_domain_pack_draft(draft_pack)
            load_domain_pack_version(draft_pack.domain_id, draft_pack.version)
        except (DomainCatalogError, DomainPackError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("Domain Pack ist gültig.")

    if ref.active:
        st.info("Diese Domain-Version ist bereits aktiv.")
        return
    if st.button("Ausgewählte Version aktivieren", type="primary"):
        try:
            pack = activate_domain_version(ref.domain_id, ref.version)
        except (DomainCatalogError, DomainPackError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Aktiviert: {pack.name} ({pack.domain_id}/{pack.version})")
            st.rerun()


def _render_soya(ref: DomainPackRef, refs: list[DomainPackRef], pack: DomainPack) -> None:
    st.subheader("SOyA-Struktur")
    st.caption(
        "Export und Import der Fragen als SOyA-Struktur (Basis, Validation-, Form- und "
        "Annotation-Overlay) im JSON-Format. Regeln und Empfehlungen bleiben GuideOS-spezifisch."
    )
    document = json.dumps(pack_to_soya(pack), ensure_ascii=False, indent=2)
    st.download_button(
        "SOyA-Struktur herunterladen (JSON)",
        data=document.encode("utf-8"),
        file_name=f"{pack.domain_id}-{pack.version}.soya.json",
        mime="application/json",
        key=f"soya_download_{ref.domain_id}_{ref.version}",
    )

    uploaded = st.file_uploader(
        "SOyA-Datei importieren (JSON)",
        type=["json"],
        key=f"soya_upload_{ref.domain_id}_{ref.version}",
    )
    target_version = st.text_input(
        "Ziel-Version",
        value=_next_patch_version(pack.version),
        key=f"soya_version_{ref.domain_id}_{ref.version}",
    )
    keep_rules = st.checkbox(
        "Regeln und Empfehlungen der ausgewählten Version übernehmen",
        value=True,
        key=f"soya_keep_rules_{ref.domain_id}_{ref.version}",
    )
    if not st.button("Import als Draft speichern", key=f"soya_import_{ref.domain_id}_{ref.version}"):
        return
    if uploaded is None:
        st.error("Bitte zuerst eine SOyA-Datei auswählen.")
        return
    try:
        try:
            data = json.loads(uploaded.getvalue().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Die SOyA-Datei enthält kein gültiges JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Die SOyA-Datei muss ein JSON-Objekt enthalten.")
        imported, warnings = pack_from_soya(
            data,
            domain_id=pack.domain_id,
            version=target_version.strip(),
            name=pack.name,
            description=pack.description,
            default_locale=pack.default_locale,
            base_pack=pack if keep_rules else None,
        )
        for warning in warnings:
            st.warning(warning)
        imported = imported.model_copy(
            update={
                "web_sources": pack.web_sources,
                "advisory_guidance": pack.advisory_guidance,
                "result_chat": pack.result_chat,
                "knowledge_recommendations": pack.knowledge_recommendations,
                "tools": pack.tools,
                "topics": pack.topics,
            }
        )
        assert_version_editable(imported.domain_id, imported.version, refs)
        path = save_domain_pack_draft(imported)
    except (SoyaError, DomainPackError, DomainCatalogError, ValueError) as exc:
        st.error(str(exc))
    else:
        st.success(f"Draft gespeichert: {path}")


def _render_knowledge(pack: DomainPack) -> None:
    st.subheader("Wissensbasis")
    st.caption(
        "Dokumente und Webseiten, die die Beratung dieser Domäne durchsucht und als Quelle "
        "nennt. Die Wissensbasis gilt für alle Versionen der Domäne; Score, Regeln und "
        "Bericht bleiben davon unberührt."
    )
    provider = get_provider(DEFAULT_PROVIDER)
    endpoint = provider.default_endpoint
    service = knowledge_service_from_env(provider)
    domain_id = pack.domain_id
    key = f"knowledge_{domain_id}_{pack.version}"
    if service.embedding_model:
        st.caption(
            f"Suche: hybrid, lexikalisch plus Embeddings mit dem Modell {service.embedding_model}"
            + (f", nachsortiert mit {service.rerank_model}." if service.rerank_model else ".")
        )
    else:
        st.caption(
            "Suche: lexikalisch. Für eine semantische Suche GUIDEOS_EMBEDDING_MODEL setzen."
        )
    overview = st.container()

    uploaded = st.file_uploader(
        "Dokument hochladen (PDF, Markdown, Text)",
        type=["pdf", "md", "txt"],
        key=f"{key}_upload",
    )
    title = st.text_input("Titel (optional)", key=f"{key}_title")
    if st.button("Dokument hinzufügen", key=f"{key}_add"):
        if uploaded is None:
            st.error("Bitte zuerst eine Datei auswählen.")
        else:
            try:
                document = service.add_document(
                    domain_id, uploaded.name, uploaded.getvalue(), endpoint, title=title
                )
            except (KnowledgeError, ProviderError) as exc:
                st.error(str(exc))
            else:
                st.success(
                    f"Hinzugefügt: {document.title} ({len(document.chunks)} Abschnitte)"
                )

    web = pack.web_sources
    if web is None or not web.enabled:
        st.warning(
            "Diese Pack-Version verwendet keine Webquellen. Hinterlegte Webseiten nutzt die "
            "Beratung erst, wenn die Webquellen in den Metadaten aktiviert sind."
        )
    elif web.allowed_domains:
        st.caption("Erlaubte Web-Domains: " + ", ".join(web.allowed_domains))
    url = st.text_input("Adresse der Webseite", key=f"{key}_url")
    if st.button("Webseite abrufen und hinzufügen", key=f"{key}_add_url"):
        try:
            document = service.add_web_page(
                domain_id, url, endpoint, allowed_domains=web.allowed_domains if web else ()
            )
        except (KnowledgeError, ProviderError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Hinzugefügt: {document.title} ({len(document.chunks)} Abschnitte)")

    flash = st.session_state.pop(f"{key}_flash", None)
    if flash:
        st.success(flash)
    try:
        documents = service.list_documents(domain_id)
    except KnowledgeError as exc:
        st.error(str(exc))
        return
    if documents:
        selected = st.selectbox(
            "Dokument",
            documents,
            format_func=lambda item: (
                f"{item.title} ({item.url or item.filename})"
                + (" (ersetzt)" if item.superseded_by else "")
            ),
            key=f"{key}_selected",
        )
        action_columns = st.columns(3)
        if action_columns[0].button("Ausgewähltes Dokument entfernen", key=f"{key}_delete"):
            try:
                service.delete_document(selected.document_id)
            except (KnowledgeError, LookupError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        if selected.kind == "web" and selected.superseded_by is None:
            if action_columns[1].button("Neu abrufen", key=f"{key}_refetch"):
                try:
                    refreshed = service.refetch_web_page(
                        domain_id, selected.document_id, endpoint,
                        allowed_domains=web.allowed_domains if web else (),
                    )
                except (KnowledgeError, ProviderError) as exc:
                    st.error(str(exc))
                else:
                    if refreshed.document_id == selected.document_id:
                        st.info("Die Webseite ist unverändert; die gespeicherte Version bleibt.")
                    else:
                        st.session_state[f"{key}_flash"] = (
                            f"Neue Quellversion gespeichert: {refreshed.title} "
                            f"({len(refreshed.chunks)} Abschnitte); die alte bleibt als ersetzt erhalten."
                        )
                        st.rerun()
        if service.superseded_documents(domain_id):
            if action_columns[2].button("Ersetzte Versionen entfernen", key=f"{key}_prune"):
                try:
                    removed = service.delete_superseded(domain_id)
                except KnowledgeError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[f"{key}_flash"] = f"{removed} ersetzte Version(en) entfernt."
                    st.rerun()
    with overview:
        if documents:
            st.dataframe(
                [
                    {
                        "Titel": item.title,
                        "Art": "Dokument" if item.kind == "document" else "Website",
                        "Herkunft": item.url or item.filename,
                        "Abschnitte": len(item.chunks),
                        "Stand": format_date_de(item.created_at.date().isoformat()),
                        "Status": "ersetzt" if item.superseded_by else "aktuell",
                        "Hash": item.content_hash[:12],
                        "Embedding-Modell": item.embedding_model or "",
                    }
                    for item in documents
                ],
                width="stretch",
            )
        else:
            st.info("Für diese Domäne sind noch keine Dokumente hinterlegt.")

    pending = service.pending_reindex(domain_id)
    if pending:
        st.warning(
            f"{pending} Dokument(e) sind nicht mit dem Embedding-Modell "
            f"{service.embedding_model} indexiert; bis dahin wird lexikalisch gesucht."
        )
        if st.button("Neu indexieren", key=f"{key}_reindex"):
            try:
                count = service.reindex(domain_id, endpoint)
            except (KnowledgeError, ProviderError) as exc:
                st.error(str(exc))
            else:
                st.success(f"{count} Dokument(e) neu indexiert.")

    query = st.text_input("Suche testen", key=f"{key}_query")
    if st.button("Suchen", key=f"{key}_search"):
        try:
            passages = service.search(pack, query, endpoint)
        except (KnowledgeError, ProviderError) as exc:
            st.error(str(exc))
            return
        if not passages:
            st.info("Keine passenden Abschnitte gefunden.")
        for passage in passages:
            st.markdown(
                f"**{passage.source_id}** · {describe_source(passage.to_source())} "
                f"· Score {passage.score:.2f}"
            )
            st.caption(passage.text)


def _render_tool_servers(pack: DomainPack) -> None:
    """Tab Werkzeuge: MCP servers of the domain, their tools and which of them
    the model may call. Registrations belong to the domain, not to a version."""
    from guideos.tools.factory import tool_service_from_env
    from guideos.tools.models import ToolError
    from guideos.tools.store import ToolStoreError

    st.subheader("Werkzeuge (MCP-Server)")
    st.caption(
        "MCP-Server, deren Werkzeuge das Modell in der Beratung dieser Domäne aufrufen darf. "
        "Die Registrierung gilt für alle Versionen; ob die Beratung Werkzeuge nutzt, legt die "
        "Pack-Version im Block tools fest. Das Token liegt im Klartext in der Datenbank; der "
        "Domain-Admin ist kein Produktionszugang."
    )
    service = tool_service_from_env()
    domain_id = pack.domain_id
    key = f"tools_{domain_id}"
    try:
        servers = service.list_servers(domain_id)
    except ToolStoreError as exc:
        st.error(str(exc))
        return
    flash = st.session_state.pop(f"{key}_flash", None)
    if flash:
        st.success(flash)

    if servers:
        for server in servers:
            fetched = (
                f"abgerufen am {server.tools_fetched_at.strftime('%d.%m.%Y %H:%M')}"
                if server.tools_fetched_at else "noch nicht abgerufen"
            )
            st.markdown(
                f"**{server.name}** – {server.url} – {'aktiv' if server.enabled else 'inaktiv'} – "
                f"{len(server.allowed_tools)} von {len(server.tool_specs)} Werkzeugen freigegeben "
                f"({fetched}) – Token {'gesetzt' if server.token else 'fehlt'}"
            )
    else:
        st.info("Für diese Domäne ist noch kein MCP-Server registriert.")
    settings = pack.tools
    if settings is not None and settings.enabled:
        allowed = {tool for server in servers if server.enabled for tool in server.allowed_tools}
        missing = [tool for tool in (settings.dataset_tool, settings.delete_tool) if tool not in allowed]
        if missing:
            st.warning(
                "Die Pack-Version erwartet die Werkzeuge " + ", ".join(missing)
                + " für Upload und Löschung von Messdaten; sie sind auf keinem aktiven Server freigegeben."
            )

    with st.expander("Server registrieren oder ändern", expanded=not servers):
        name = st.text_input("Name des Servers", key=f"{key}_name",
                             help="Kleinbuchstaben, Ziffern, - und _; eindeutig je Domäne.")
        url = st.text_input("Adresse des Servers (URL)", key=f"{key}_url",
                            placeholder="http://127.0.0.1:8600/mcp")
        token = st.text_input("Token", key=f"{key}_token", type="password")
        enabled = st.checkbox("Aktiv", value=True, key=f"{key}_enabled")
        if st.button("Server speichern", key=f"{key}_save"):
            try:
                service.register(domain_id, name.strip(), url.strip(), token.strip(), enabled)
            except (ValueError, ToolStoreError) as exc:
                st.error(str(exc))
            else:
                st.session_state[f"{key}_flash"] = f"Server {name.strip()} gespeichert."
                st.rerun()

    if not servers:
        return
    selected = st.selectbox("Server", servers, format_func=lambda item: item.name, key=f"{key}_selected")
    columns = st.columns(3)
    if columns[0].button("Werkzeuge abrufen", key=f"{key}_refresh"):
        try:
            refreshed = service.refresh_tools(domain_id, selected.name)
        except (ToolError, ToolStoreError) as exc:
            st.error(exc.message if isinstance(exc, ToolError) else str(exc))
        else:
            st.session_state[f"{key}_flash"] = (
                f"{len(refreshed.tool_specs)} Werkzeuge von {selected.name} abgerufen."
            )
            st.rerun()
    if columns[2].button("Server entfernen", key=f"{key}_remove"):
        service.remove(domain_id, selected.name)
        st.session_state[f"{key}_flash"] = f"Server {selected.name} entfernt."
        st.rerun()
    if not selected.tool_specs:
        st.caption("Noch keine Werkzeuge abgerufen.")
        return
    st.markdown("Freigegebene Werkzeuge sieht das Modell mit Beschreibung und Schema; "
                "nicht freigegebene bleiben unsichtbar.")
    chosen = []
    for spec in selected.tool_specs:
        if st.checkbox(f"Freigeben: {spec.name}", value=spec.name in selected.allowed_tools,
                       key=f"{key}_{selected.name}_allow_{spec.name}"):
            chosen.append(spec.name)
        st.caption(spec.description or "(keine Beschreibung)")
        with st.expander(f"Schema von {spec.name}"):
            st.json({"input_schema": spec.parameters_for_model(), "output_schema": spec.output_schema})
    if st.button("Freigaben speichern", key=f"{key}_allow_save"):
        try:
            service.set_allowed_tools(domain_id, selected.name, chosen)
        except (ToolError, ToolStoreError) as exc:
            st.error(exc.message if isinstance(exc, ToolError) else str(exc))
        else:
            st.session_state[f"{key}_flash"] = f"{len(chosen)} Werkzeuge freigegeben."
            st.rerun()


def _visible_if_label(question: QuestionDefinition) -> str:
    if question.visible_if is None:
        return ""
    condition = question.visible_if
    return f"{condition.field_key} {condition.operator} {condition.value}"


def _next_patch_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        return version
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        return version
    return f"{major}.{minor}.{patch + 1}"


CHOICE_FIELD_TYPES = {"select", "multiselect", "boolean"}


def _render_question(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
    endpoint: str,
    model: str,
) -> None:
    question = response.current_question
    if question is None:
        return
    _render_previous_answers(orchestrator, response)
    st.subheader(question.text)
    st.caption(_question_hint(question))
    _render_question_chat_messages(orchestrator, response, question.question_id)
    _render_pending_question_help_errors(response.session_id, question.question_id)

    proposal = response.proposal
    if proposal is not None:
        _render_proposal(orchestrator, response, question)

    if question.field_type == "file":
        _render_file_answer(orchestrator, response, question)
    elif question.field_type in CHOICE_FIELD_TYPES:
        with st.form(f"answer_{response.session_id}_{question.question_id}"):
            value = _render_value_input(
                question, "answer", proposal.normalized_value if proposal is not None else None
            )
            save = st.form_submit_button("Antwort speichern", type="primary")
        if save:
            updated = _run_workflow(
                lambda: orchestrator.submit(
                    response.session_id,
                    question.question_id,
                    value,
                )
            )
            if updated is None:
                return
            if updated.validation_errors:
                _render_errors(updated)
            else:
                st.rerun()

    if question.allow_unknown or not question.required:
        if st.button(
            "Unbekannt / überspringen",
            key=f"skip_{response.session_id}_{question.question_id}",
        ):
            updated = _run_workflow(
                lambda: orchestrator.submit(
                    response.session_id,
                    question.question_id,
                    None,
                )
            )
            if updated is None:
                return
            if updated.validation_errors:
                _render_errors(updated)
            else:
                st.rerun()

    if question.field_type == "file":
        # A file is answered through the uploader only; the model never
        # interprets it, so there is no chat input for this question.
        return
    placeholder = (
        "Rückfrage zur Frage eingeben"
        if question.field_type in CHOICE_FIELD_TYPES
        else "Antwort oder Rückfrage eingeben"
    )
    # Inside a container the chat input renders inline under the question
    # instead of being pinned to the bottom of the page.
    with st.container():
        text = st.chat_input(placeholder, key=f"question_chat_{response.session_id}")
    if text:
        updated = _run_workflow(
            lambda: orchestrator.submit_question_chat(
                response.session_id,
                question.question_id,
                text,
                endpoint,
                model,
            )
        )
        if updated is None:
            return
        if updated.validation_errors:
            _store_question_help_errors(
                response.session_id,
                question.question_id,
                updated,
            )
        else:
            _clear_question_help_errors(response.session_id, question.question_id)
        st.rerun()


CONFIDENCE_LABELS = {"high": "hoch", "medium": "mittel", "low": "niedrig"}
PROPOSAL_BUTTON = "Vorschlag übernehmen"


def _proposal_text(question: QuestionDefinition, proposal) -> str:
    text = f"Aus deiner hochgeladenen Datei: **{display_value(question, proposal.normalized_value)}**"
    if proposal.confidence:
        text += f" (Konfidenz {CONFIDENCE_LABELS.get(proposal.confidence, proposal.confidence)})"
    text += "."
    if proposal.note:
        text += f" {proposal.note}"
    return text + " Du kannst den Vorschlag übernehmen oder anders antworten."


def _render_proposal(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
    question: QuestionDefinition,
) -> None:
    """A value derived from the measurement data for the current question:
    shown with its confidence, taken over with one click, never stored
    without the participant's confirmation."""
    proposal = response.proposal
    st.info(_proposal_text(question, proposal))
    if st.button(PROPOSAL_BUTTON, key=f"proposal_{response.session_id}_{question.question_id}"):
        updated = _run_workflow(
            lambda: orchestrator.submit(response.session_id, question.question_id, proposal.normalized_value)
        )
        if updated is None:
            return
        if updated.validation_errors:
            _render_errors(updated)
        else:
            st.rerun()


def _render_file_answer(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
    question: QuestionDefinition,
) -> None:
    """A file question: the uploader plus the format hint of the pack's
    dataset tool. Saving sends the decoded text to the orchestrator, which
    imports it through the tool and stores only the dataset reference."""
    key = f"file_{response.session_id}_{question.question_id}"
    tools = orchestrator.dataset_importer.tools if orchestrator.dataset_importer is not None else None
    with st.form(f"answer_{response.session_id}_{question.question_id}"):
        uploaded = st.file_uploader(
            "Datei auswählen", type=_upload_types(question), key=f"{key}_file"
        )
        format_hint = st.selectbox(
            "Dateiformat", _dataset_format_options(tools, orchestrator.pack, question), key=f"{key}_format"
        )
        save = st.form_submit_button("Antwort speichern", type="primary")
    if not save:
        return
    if uploaded is None:
        st.error("Bitte zuerst eine Datei auswählen.")
        return
    value = _file_answer_value(uploaded, format_hint)
    with st.spinner("Datei wird geprüft und importiert ..."):
        updated = _run_workflow(
            lambda: orchestrator.submit(response.session_id, question.question_id, value)
        )
    if updated is None:
        return
    if updated.validation_errors:
        _render_errors(updated)
    else:
        st.rerun()


def _upload_types(question: QuestionDefinition) -> list[str]:
    return list(question.upload.extensions) if question.upload is not None else ["csv", "txt"]


def _file_answer_value(uploaded, format_hint: str) -> dict[str, Any]:
    return {"filename": uploaded.name, "content": decode_text(uploaded.getvalue()), "format_hint": format_hint}


def _render_previous_answers(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
) -> None:
    if not response.answers:
        return
    questions = {question.field_key: question for question in orchestrator.pack.questions}
    with st.expander(f"Bisherige Angaben ({len(response.answers)})"):
        lines = [
            f"- {questions[field_key].text} "
            f"**{display_value(questions[field_key], answer.normalized_value)}**"
            + _origin_note(answer)
            for field_key, answer in response.answers.items()
        ]
        st.markdown("\n".join(lines))


MEASUREMENT_ANSWER_NOTE = " _(aus den Messdaten abgeleitet)_"


def _origin_note(answer) -> str:
    return MEASUREMENT_ANSWER_NOTE if getattr(answer, "origin", "user") == "measurement" else ""


def _render_question_chat_messages(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
    question_id: str,
) -> None:
    _render_assessment_chat_messages(
        [
            message
            for message in response.chat_messages
            if message.scope == "question_help"
            and message.question_id == question_id
        ],
        orchestrator,
        response,
    )


def _question_hint(question: QuestionDefinition) -> str:
    field_type = question.field_type
    validation = question.validation
    labels = ", ".join(option.label for option in question.options)
    if field_type == "select":
        hint = f"Mögliche Antworten: {labels}"
    elif field_type == "multiselect":
        bounds = _bounds_text(validation.min_count, validation.max_count, "mindestens", "höchstens")
        hint = f"Mehrfachauswahl möglich{bounds}: {labels}"
    elif field_type == "text":
        if validation.min_length is not None and validation.max_length is not None:
            hint = f"Freitext ({validation.min_length} bis {validation.max_length} Zeichen)"
        elif validation.min_length is not None:
            hint = f"Freitext (mindestens {validation.min_length} Zeichen)"
        elif validation.max_length is not None:
            hint = f"Freitext (höchstens {validation.max_length} Zeichen)"
        else:
            hint = "Freitext"
    elif field_type == "boolean":
        hint = "Antwort mit Ja oder Nein"
    elif field_type == "file":
        extensions = ", ".join(f".{item}" for item in (question.upload.extensions if question.upload else []))
        hint = f"Datei hochladen ({extensions})" if extensions else "Datei hochladen"
    elif field_type == "date":
        hint = "Datum im Format TT.MM.JJJJ" + _range_text(
            format_date_de(validation.min_date) if validation.min_date else None,
            format_date_de(validation.max_date) if validation.max_date else None,
        )
    else:
        kind = "Ganze Zahl" if field_type == "integer" else "Zahl"
        hint = kind + _range_text(
            f"{validation.minimum:g}" if validation.minimum is not None else None,
            f"{validation.maximum:g}" if validation.maximum is not None else None,
        )
    if question.allow_unknown or not question.required:
        hint += ". Du kannst auch 'unbekannt' antworten."
    return hint


def _range_text(lower: str | None, upper: str | None) -> str:
    if lower is not None and upper is not None:
        return f" zwischen {lower} und {upper}"
    if lower is not None:
        return f" ab {lower}"
    if upper is not None:
        return f" bis {upper}"
    return ""


def _bounds_text(lower, upper, lower_word: str, upper_word: str) -> str:
    parts = []
    if lower is not None:
        parts.append(f"{lower_word} {lower}")
    if upper is not None:
        parts.append(f"{upper_word} {upper}")
    return f" ({', '.join(parts)})" if parts else ""


def _render_review(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
    endpoint: str,
    model: str,
) -> None:
    _render_answer_summary(orchestrator, response)
    with st.chat_message("assistant"):
        st.markdown(
            "Alle Pflichtangaben sind vorhanden. Prüfe deine Angaben und "
            "schließe das Assessment ab, oder korrigiere einzelne Antworten."
        )
    if st.button("Assessment abschließen", type="primary"):
        with st.spinner("Ergebnis und erste Empfehlungen werden erstellt ..."):
            completed = _run_workflow(
                lambda: orchestrator.complete(response.session_id, endpoint, model)
            )
        if completed is None:
            return
        st.rerun()
    with st.expander("Angaben korrigieren"):
        _render_revision_forms(orchestrator, response)


def _render_revision_forms(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
) -> None:
    questions = {question.field_key: question for question in orchestrator.pack.questions}
    for field_key, answer in response.answers.items():
        question = questions[field_key]
        st.markdown(f"**{question.text}**")
        with st.form(f"revise_{response.session_id}_{field_key}"):
            use_unknown = False
            if question.allow_unknown or not question.required:
                use_unknown = st.checkbox(
                    "Als unbekannt speichern",
                    value=answer.normalized_value is None,
                    key=f"unknown_{response.session_id}_{field_key}",
                )
            if question.field_type == "file":
                st.caption(f"Gespeichert: {display_value(question, answer.normalized_value)}")
                uploaded = st.file_uploader(
                    "Neue Datei (leer: gespeicherte Datei behalten)", type=_upload_types(question),
                    key=f"review_file_{response.session_id}_{field_key}",
                )
                tools = (orchestrator.dataset_importer.tools
                         if orchestrator.dataset_importer is not None else None)
                format_hint = st.selectbox(
                    "Dateiformat", _dataset_format_options(tools, orchestrator.pack, question),
                    key=f"review_format_{response.session_id}_{field_key}",
                )
                value = (_file_answer_value(uploaded, format_hint) if uploaded is not None
                         else answer.normalized_value)
            else:
                value = _render_value_input(
                    question,
                    f"review_{field_key}",
                    answer.normalized_value,
                )
            save = st.form_submit_button("Änderung speichern")
        if save:
            updated = _run_workflow(
                lambda: orchestrator.revise(
                    response.session_id,
                    question.question_id,
                    None if use_unknown else value,
                )
            )
            if updated is None:
                return
            if updated.validation_errors:
                _render_errors(updated)
            else:
                st.rerun()


def _render_answer_summary(
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
) -> None:
    questions = {question.field_key: question for question in orchestrator.pack.questions}
    lines = ["**Deine Angaben**", ""]
    for field_key, answer in response.answers.items():
        question = questions[field_key]
        lines.append(
            f"- {question.text} **{display_value(question, answer.normalized_value)}**" + _origin_note(answer)
        )
    with st.chat_message("assistant"):
        st.markdown("\n".join(lines))


def _render_completed(
    orchestrator: AssessmentOrchestrator,
    service: ConversationService,
    response: AssessmentResponse,
    endpoint: str,
    model: str,
    *,
    feedback_repository: PostgresPilotFeedbackRepository | None = None,
    participant_id: str | None = None,
    show_report: bool = False,
    profiles: ProfileRepository | None = None,
) -> None:
    if response.result is None or response.report is None:
        st.error("Das Ergebnis konnte nicht geladen werden.")
        return
    pack = orchestrator.pack
    profile = _current_profile(profiles, pack.domain_id)
    is_profile_source = profile is not None and profile.source_session_id == response.session_id
    if profile is not None and not is_profile_source:
        st.info(
            "Das aktuelle Profil dieser Domäne stammt aus einem neueren Assessment. "
            "Diese Session bleibt als Verlauf mit Angaben und Bericht erhalten."
        )
    if is_profile_source:
        with st.expander("Profil"):
            _render_profile_panel(profiles, pack, profile)
            _render_profile_deletion(orchestrator, service, profiles, pack)
        if pack.tools is not None and pack.tools.enabled and service.tools is not None:
            with st.expander("Messdaten (CSV)", expanded=profile.dataset is None):
                _render_dataset_panel(service, pack, profile)
        # The advisory chat lives on the profile; older sessions keep answers and report only.
        if not _ensure_result_conversation(service, pack, endpoint, model):
            return
        _render_advisory(service, pack, profile, endpoint, model)
    if show_report:
        with st.expander("Bericht (Markdown)"):
            if is_profile_source:
                st.download_button(
                    "Profilbericht herunterladen",
                    data=render_profile_report(pack, profile).content,
                    file_name=f"{profile.profile_id}.md",
                    mime="text/markdown",
                )
            st.download_button(
                "Bericht herunterladen",
                data=response.report.content,
                file_name=f"{response.session_id}.md",
                mime="text/markdown",
            )
    if feedback_repository is not None and participant_id is not None:
        with st.expander("Feedback zum Pilot", expanded=True):
            _render_pilot_feedback(feedback_repository, response.session_id, participant_id)


def _render_profile_panel(
    profiles: ProfileRepository | None, pack: DomainPack, profile: Profile
) -> None:
    """Answers and recommendations of the profile, open required questions,
    the note that knowledge recommendations predate a change, and one
    pair of feedback buttons per recommendation (deterministic, no model)."""
    service = ProfileService(profiles) if profiles is not None else None
    if service is not None:
        try:
            open_questions = service.open_questions(pack.domain_id, profile.participant_id)
        except ProfileServiceError:
            open_questions = []
        if open_questions:
            st.warning(
                "Offene Angaben nach einer Änderung: "
                + "; ".join(question.text for question in open_questions)
                + " Beantworte sie im Chat."
            )
    if profile.knowledge_stale:
        st.info(
            "Die KI-Empfehlungen zeigen den Stand vor der Änderung deiner Angaben. "
            "Im Chat kannst du sie neu erzeugen lassen."
        )
    st.markdown(render_profile_summary(pack, profile))
    if service is None:
        return
    st.caption("Rückmeldung zu den Empfehlungen: Was passt für dich, was nicht?")
    items = [
        (item.recommendation_id, item.title)
        for item in (*profile.result.recommendations, *profile.result.knowledge_recommendations)
    ]
    for recommendation_id, title in items:
        current = profile.feedback.get(recommendation_id)
        label_col, yes_col, no_col = st.columns([4, 1, 1])
        label_col.write(title + (f" ({FEEDBACK_LABELS[current.stance]})" if current else ""))
        for stance, column, label in (
            ("accepted", yes_col, "Passt"),
            ("rejected", no_col, "Passt nicht"),
        ):
            if column.button(
                label,
                key=f"feedback_{stance}_{recommendation_id}",
                disabled=current is not None and current.stance == stance,
            ):
                try:
                    service.set_feedback(
                        pack.domain_id, profile.participant_id, recommendation_id, stance, origin="button"
                    )
                except (ProfileServiceError, ProfileRepositoryError) as exc:
                    st.error(str(exc))
                    return
                st.rerun()


def _render_session_deletion(
    orchestrator: AssessmentOrchestrator,
    service: ConversationService,
    profiles: ProfileRepository | None,
    pack: DomainPack,
    session: AssessmentSession,
) -> None:
    """Delete one assessment. With uploaded measurement data the participant
    confirms first, because the datasets go with the session, also when the
    profile still uses them for the advisory (decision 2026-10-20)."""
    has_datasets = bool(dataset_refs(session))
    confirmed = True
    if has_datasets:
        confirmed = st.checkbox(
            "Auch die hochgeladenen Messdaten dieses Assessments löschen; Berechnungen daraus stehen "
            "der Beratung dann nicht mehr zur Verfügung.",
            key=f"session_delete_confirm_{session.session_id}",
        )
    if not st.button("Ausgewähltes Assessment löschen", disabled=not confirmed):
        return
    try:
        deletion = delete_session_data(
            orchestrator.repository, profiles, session.session_id, tools=service.tools
        )
    except AssessmentConflictError:
        st.warning("Der Session-Stand wurde geändert. Bitte aktualisiere die Seite.")
        return
    except LookupError:
        st.warning("Das Assessment wurde bereits gelöscht.")
        return
    except (ProfileRepositoryError, ConversationRepositoryError, AssessmentRepositoryError) as exc:
        st.error(str(exc))
        return
    if deletion.profile_changed:
        try:
            service.post_dataset_deleted(pack.domain_id)
        except (ConversationError, ConversationRepositoryError) as exc:
            logger.warning("dataset_deleted_notice_failed session_id=%s reason=%s", session.session_id, exc)
    st.session_state[_active_session_key(pack)] = None
    st.rerun()


def _render_profile_deletion(
    orchestrator: AssessmentOrchestrator,
    service: ConversationService,
    profiles: ProfileRepository | None,
    pack: DomainPack,
) -> None:
    """Deletion by the participant: profile, its chats and the assessments
    of this domain, after an explicit confirmation (no model involved)."""
    if profiles is None:
        return
    st.divider()
    confirmed = st.checkbox(
        "Profil mit allen Chats und Assessments dieser Domäne löschen",
        key=f"profile_delete_confirm_{pack.domain_id}",
    )
    if not st.button("Profil löschen", key=f"profile_delete_{pack.domain_id}", disabled=not confirmed):
        return
    try:
        delete_profile_data(profiles, service.conversations, orchestrator.repository, pack.domain_id,
                            tools=service.tools)
    except LookupError:
        st.warning("Das Profil wurde bereits gelöscht.")
    except (ProfileRepositoryError, ConversationRepositoryError, AssessmentRepositoryError) as exc:
        st.error(str(exc))
        return
    st.session_state[_active_session_key(pack)] = None
    st.rerun()


def _render_dataset_panel(service: ConversationService, pack: DomainPack, profile: Profile) -> None:
    """Upload, status and deletion of the participant's measurement data. The
    file goes to the pack's dataset tool on the registered server; GuideOS
    keeps only the dataset reference and the import summary."""
    key = f"dataset_{profile.profile_id}"
    dataset = profile.dataset
    if dataset is not None:
        st.markdown(
            f"**{dataset.filename}**, importiert am {dataset.imported_at.strftime('%d.%m.%Y %H:%M')} "
            f"(Datensatz `{dataset.dataset_id}`)."
        )
        if dataset.summary:
            st.caption(dataset.summary)
        confirmed = st.checkbox("Messdaten und Berechnungen daraus löschen", key=f"{key}_confirm")
        if st.button("Messdaten löschen", key=f"{key}_delete", disabled=not confirmed):
            try:
                service.delete_dataset(pack.domain_id)
            except (ConversationError, ConversationRepositoryError, ProfileRepositoryError) as exc:
                st.error(str(exc))
                return
            st.rerun()
        st.caption("Eine neue Datei ersetzt die gespeicherten Messdaten.")
    else:
        st.caption(
            "Noch keine Messdaten. Lade die Datei hoch, die diese Domäne erwartet (z. B. eine CSV mit "
            "Messwerten); sie wird zur Berechnung an den registrierten Berechnungsdienst übergeben, "
            "GuideOS merkt sich nur die Kennung des Datensatzes."
        )
    uploaded = st.file_uploader("CSV-Datei mit Messwerten", type=["csv", "txt"], key=f"{key}_file")
    format_hint = st.selectbox("Dateiformat", _dataset_format_options(service.tools, pack), key=f"{key}_format")
    if st.button("Messdaten importieren", key=f"{key}_import"):
        if uploaded is None:
            st.error("Bitte zuerst eine Datei auswählen.")
            return
        try:
            with st.spinner("Messdaten werden geprüft und importiert ..."):
                service.import_dataset(pack.domain_id, uploaded.name, uploaded.getvalue(), format_hint)
        except (ConversationError, ConversationRepositoryError, ProfileRepositoryError) as exc:
            st.error(str(exc))
            return
        st.rerun()


def _dataset_format_options(
    tools, pack: DomainPack, question: QuestionDefinition | None = None
) -> list[str]:
    """The format hints the import tool accepts (shared with the REST contract)."""
    return format_options(tools, pack, question)


def _render_tool_results(message: ConversationMessage) -> None:
    """What the advisory computed for an answer: tools, arguments, the
    server's summary and the provenance (assumptions, limitations, data
    quality) of every result."""
    if not message.tool_results:
        return
    names = ", ".join(dict.fromkeys(result.call.name for result in message.tool_results))
    st.caption(f"Verwendete Werkzeuge: {names}")
    with st.expander("Berechnungsgrundlagen"):
        for result in message.tool_results:
            st.markdown(f"**{result.call.name}**")
            if result.call.arguments:
                st.json(result.call.arguments, expanded=False)
            if result.summary:
                st.write(result.summary)
            provenance = (result.structured_content or {}).get("provenance") or {}
            for key, label in (("assumptions", "Annahmen"), ("limitations", "Grenzen"),
                               ("quality_issues", "Datenqualität")):
                items = provenance.get(key)
                if items:
                    rendered = [item.get("message", item) if isinstance(item, dict) else item for item in items]
                    st.caption(f"{label}: " + "; ".join(str(item) for item in rendered))
            if result.calculation_id:
                st.caption(f"Berechnung {result.calculation_id}" + (
                    f", Kern {provenance['core_version']}" if provenance.get("core_version") else ""))


def _current_profile(profiles: ProfileRepository | None, domain_id: str) -> Profile | None:
    """The participant's profile of the domain, or None without one; a
    storage error is shown but does not hide the session."""
    if profiles is None:
        return None
    try:
        return profiles.get(domain_id)
    except LookupError:
        return None
    except ProfileRepositoryError as exc:
        st.error(str(exc))
        return None


def _ensure_result_conversation(
    service: ConversationService,
    pack: DomainPack,
    endpoint: str,
    model: str,
) -> bool:
    """The profile's result chat: created on the first completion, extended
    once after every newer one, otherwise returned unchanged."""
    try:
        with st.spinner("Erste Empfehlung wird vorbereitet ..."):
            service.ensure_result_conversation(pack.domain_id, endpoint, model)
        return True
    except (ConversationError, ConversationRepositoryError, ProfileRepositoryError) as exc:
        st.error(str(exc))
        return False


def _render_advisory(
    service: ConversationService,
    pack: DomainPack,
    profile: Profile,
    endpoint: str,
    model: str,
) -> None:
    try:
        conversations = [
            item for item in service.list() if item.profile_id == profile.profile_id
        ]
    except ConversationRepositoryError as exc:
        st.error(str(exc))
        return

    state_key = f"advisory_conversation_{profile.profile_id}"
    selected_id = None
    with st.sidebar:
        if conversations:
            conversation_ids = [item.conversation_id for item in conversations]
            active_id = st.session_state.get(state_key)
            if active_id not in conversation_ids:
                active_id = conversation_ids[-1]
            selected_id = st.selectbox(
                "Beratungschats",
                conversation_ids,
                index=conversation_ids.index(active_id),
                format_func=lambda item_id: _conversation_label(conversations, item_id),
            )
            st.session_state[state_key] = selected_id
        title = st.text_input(
            "Titel des neuen Chats (optional)", key=f"advisory_title_{profile.profile_id}"
        )
        if st.button("Neuer Chat"):
            try:
                conversation = service.create(pack.domain_id, title=title)
            except (ConversationError, ConversationRepositoryError, LookupError) as exc:
                st.error(str(exc))
                return
            st.session_state[state_key] = conversation.conversation_id
            st.rerun()
        if selected_id is not None and st.button("Chat löschen"):
            try:
                service.delete(selected_id)
            except (ConversationRepositoryError, LookupError) as exc:
                st.error(str(exc))
                return
            st.session_state[state_key] = None
            st.rerun()

    if selected_id is not None:
        conversation = next(item for item in conversations if item.conversation_id == selected_id)
        for message in conversation.messages:
            with st.chat_message(message.role):
                st.markdown(message.content)
                _render_tool_results(message)
                _render_source_excerpts(message)

    text = st.chat_input("Frage an die Beratung", key=f"advisory_input_{profile.profile_id}")
    if text:
        try:
            if selected_id is None:
                selected_id = service.create(pack.domain_id).conversation_id
                st.session_state[state_key] = selected_id
            service.send_message(selected_id, text, endpoint, model)
        except ConversationConflictError:
            st.warning("Der Chat wurde zwischenzeitlich geändert. Bitte aktualisiere die Seite.")
            return
        except (ConversationError, ConversationRepositoryError, LookupError) as exc:
            st.error(str(exc))
            return
        st.rerun()


def _render_source_excerpts(message: ConversationMessage) -> None:
    """Let the user check an answer against the cited documents and web pages."""
    cited = {}
    for section in message.sections:
        for source in section.sources:
            if source.excerpt:
                cited.setdefault((source.reference, source.locator, source.excerpt), source)
    if not cited:
        return
    with st.expander("Quellenauszüge"):
        for source in cited.values():
            st.markdown(f"**{describe_source(source)}**")
            link = _original_markdown_link(source)
            if link:
                st.markdown(link)
            st.caption(source.excerpt)


_PAGE_LOCATOR = re.compile(r"Seite (\d+)")


def original_link(media_url: str, locator: str | None) -> str:
    """Link to a served original, relative so that it also works below a base
    path. A page locator opens the PDF on that page."""
    link = media_url.lstrip("/")
    page = _PAGE_LOCATOR.fullmatch(locator or "")
    return f"{link}#page={page.group(1)}" if page else link


def _original_markdown_link(source: SourceRef) -> str | None:
    """Markdown link to the uploaded file behind a cited document passage, or
    None when no original was kept or the file cannot be served."""
    if source.kind != "document" or not source.content_hash:
        return None
    try:
        raw = _original_bytes(source.content_hash)
    except (LookupError, KnowledgeError):
        return None
    mimetype = "application/pdf" if raw.startswith(b"%PDF") else "text/plain"
    media_url = _register_media(raw, mimetype, f"guideos-original-{source.content_hash}")
    if not media_url:
        return None
    page = _PAGE_LOCATOR.fullmatch(source.locator or "")
    label = f"Im Dokument öffnen ({source.locator})" if page else "Dokument öffnen"
    return f"[{label}]({original_link(media_url, source.locator)})"


@st.cache_resource(max_entries=8, ttl=600, show_spinner=False)
def _original_bytes(content_hash: str) -> bytes:
    """Originals are immutable per content hash, so one copy serves every
    session. A missing file raises and is therefore not cached."""
    raw = knowledge_store_from_env().load_original(content_hash)
    if raw is None:
        raise LookupError(content_hash)
    return raw


def _register_media(data: bytes, mimetype: str, coordinates: str) -> str | None:
    """Hand a file to Streamlit's in-memory media endpoint, the one its own
    download and media elements use. The URL is only valid while a session
    that rendered it is open."""
    from streamlit import runtime

    if not runtime.exists():
        return None
    return runtime.get_instance().media_file_mgr.add(data, mimetype, coordinates)


def _conversation_label(conversations: list[Conversation], conversation_id: str) -> str:
    conversation = next(item for item in conversations if item.conversation_id == conversation_id)
    return f"{conversation.title} ({len(conversation.messages)} Nachrichten)"


def _render_assessment_chat_messages(
    messages: list[AssessmentChatMessage],
    orchestrator: AssessmentOrchestrator,
    response: AssessmentResponse,
) -> None:
    for message in messages:
        with st.chat_message(message.role):
            st.write(message.content)
            if message.proposed_answer is not None:
                question = next(
                    question
                    for question in orchestrator.pack.questions
                    if question.question_id == message.proposed_answer.question_id
                )
                st.caption(
                    "Antwortvorschlag: "
                    + display_value(
                        question,
                        message.proposed_answer.normalized_value,
                    )
                )
                accept_column, discard_column = st.columns(2)
                with accept_column:
                    if st.button(
                        "Antwort übernehmen",
                        key=f"accept_{message.message_id}",
                    ):
                        updated = _run_workflow(
                            lambda: orchestrator.accept_proposed_answer(
                                response.session_id,
                                message.message_id,
                            )
                        )
                        if updated is None:
                            return
                        if updated.validation_errors:
                            _render_errors(updated)
                        else:
                            st.rerun()
                with discard_column:
                    if st.button(
                        "Verwerfen",
                        key=f"discard_{message.message_id}",
                    ):
                        if _run_workflow(
                            lambda: (
                                orchestrator.discard_proposed_answer(
                                    response.session_id,
                                    message.message_id,
                                )
                                or True
                            )
                        ) is None:
                            return
                        st.rerun()


def _render_value_input(
    question: QuestionDefinition,
    key_suffix: str,
    current_value: Any = None,
) -> Any:
    key = f"assessment_{question.question_id}_{key_suffix}"
    field_type = question.field_type
    widget = effective_widget(question)
    values = [option.value for option in question.options]
    labels = {option.value: option.label for option in question.options}
    validation = question.validation

    if field_type == "select":
        index = values.index(current_value) if current_value in values else None
        if widget == "dropdown":
            return st.selectbox("Antwort", values, index=index, format_func=labels.get, key=key)
        return st.radio("Antwort", values, index=index, format_func=labels.get, key=key)

    if field_type == "multiselect":
        selected = [value for value in (current_value or []) if value in values]
        if widget == "dropdown":
            return st.multiselect(
                "Antwort",
                values,
                default=selected,
                format_func=labels.get,
                max_selections=validation.max_count,
                key=key,
            )
        chosen = []
        for option in question.options:
            if st.checkbox(
                option.label,
                value=option.value in selected,
                key=f"{key}_{option.value}",
            ):
                chosen.append(option.value)
        return chosen

    if field_type == "text":
        text = current_value or ""
        if widget == "textarea":
            return st.text_area("Antwort", value=text, max_chars=validation.max_length, key=key)
        return st.text_input("Antwort", value=text, max_chars=validation.max_length, key=key)

    if field_type == "boolean":
        return st.radio(
            "Antwort",
            [True, False],
            index={True: 0, False: 1}.get(current_value),
            format_func=lambda value: "Ja" if value else "Nein",
            horizontal=True,
            key=key,
        )

    if field_type == "date":
        min_value = date.fromisoformat(validation.min_date) if validation.min_date else None
        max_value = date.fromisoformat(validation.max_date) if validation.max_date else None
        if isinstance(current_value, str):
            value = date.fromisoformat(current_value)
        else:
            value = min_value or max_value or date.today()
        return st.date_input(
            "Antwort",
            value=value,
            min_value=min_value,
            max_value=max_value,
            format="DD.MM.YYYY",
            key=key,
        )

    minimum = validation.minimum
    maximum = validation.maximum
    if field_type == "integer":
        min_value = int(minimum) if minimum is not None else None
        max_value = int(maximum) if maximum is not None else None
        value = int(current_value) if current_value is not None else min_value
        return st.number_input(
            "Antwort",
            min_value=min_value,
            max_value=max_value,
            value=value,
            step=1,
            key=key,
        )

    min_value = float(minimum) if minimum is not None else None
    max_value = float(maximum) if maximum is not None else None
    value = float(current_value) if current_value is not None else min_value
    return st.number_input(
        "Antwort",
        min_value=min_value,
        max_value=max_value,
        value=value,
        step=1.0,
        key=key,
    )


def _render_errors(response: AssessmentResponse) -> None:
    for issue in response.validation_errors:
        st.error(issue.message)


def _run_workflow(action):
    try:
        return action()
    except AssessmentConflictError:
        st.warning("Der Session-Stand wurde geändert. Bitte aktualisiere die Seite.")
        return None


def _render_pilot_feedback(
    repository: PostgresPilotFeedbackRepository,
    session_id: str,
    participant_id: str,
) -> None:
    st.subheader("Feedback zum Pilot")
    try:
        feedback = repository.get(session_id, participant_id)
    except PilotFeedbackError as exc:
        st.error(str(exc))
        return
    if feedback is not None:
        st.success("Danke, dein Feedback wurde gespeichert.")
        return
    with st.form(f"pilot_feedback_{session_id}"):
        rating = st.selectbox("Bewertung", [1, 2, 3, 4, 5], index=4)
        comment = st.text_area("Kommentar (optional)", max_chars=2000)
        submitted = st.form_submit_button("Feedback senden")
    if submitted:
        try:
            repository.save(session_id, participant_id, rating, comment)
        except (PilotFeedbackError, LookupError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.rerun()


def _store_question_help_errors(
    session_id: str,
    question_id: str,
    response: AssessmentResponse,
) -> None:
    st.session_state[_question_help_error_key(session_id, question_id)] = [
        issue.message for issue in response.validation_errors
    ]


def _render_pending_question_help_errors(session_id: str, question_id: str) -> None:
    key = _question_help_error_key(session_id, question_id)
    messages = st.session_state.pop(key, [])
    for message in messages:
        st.error(message)


def _clear_question_help_errors(session_id: str, question_id: str) -> None:
    st.session_state.pop(_question_help_error_key(session_id, question_id), None)


def _question_help_error_key(session_id: str, question_id: str) -> str:
    return f"assessment_question_help_errors_{session_id}_{question_id}"


def _session_label(sessions, session_id: str) -> str:
    session = next(item for item in sessions if item.session_id == session_id)
    return f"{session.created_at:%d.%m.%Y %H:%M} - {session.status} ({session.session_id[-6:]})"


