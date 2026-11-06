import argparse
import os
import sys

import streamlit as st

from guideos.adapters.streamlit import render_assessment, render_domain_admin, select_model
from guideos.chat import (
    ChatValidationError,
    ChatState,
    DEFAULT_CHAT_TITLE,
    append_assistant_message,
    append_user_message,
    clear_chat,
    create_initial_chat_state,
    create_next_chat,
    delete_chat,
    derive_chat_title,
    get_active_chat,
    rollback_pending_user_message,
    validate_chat_input,
)
from guideos.config import DEFAULT_PROVIDER, configure_logging
from guideos.pilot.access import (
    authenticate,
    logout,
    normalize_participant_name,
    participant_id,
    password_is_configured,
    pilot_mode_enabled,
)
from guideos.providers.base import LLMProvider, ProviderError
from guideos.providers.registry import get_provider, list_provider_names
from guideos.storage import ChatStorageError, chat_state_store_from_env

configure_logging()


def get_session_chat_state() -> ChatState:
    return {
        "chats": st.session_state.chats,
        "active_chat_id": st.session_state.active_chat_id,
        "next_chat_id": st.session_state.next_chat_id,
    }


def set_session_chat_state(chat_state: ChatState) -> None:
    st.session_state.chats = chat_state["chats"]
    st.session_state.active_chat_id = chat_state["active_chat_id"]
    st.session_state.next_chat_id = chat_state["next_chat_id"]


def persist_session_chat_state() -> None:
    if not st.session_state.get("chat_storage_enabled", True):
        return

    try:
        chat_state_store_from_env().save(get_session_chat_state())
    except ChatStorageError as exc:
        st.session_state.chat_storage_enabled = False
        st.error(f"{exc} Weitere Änderungen bleiben nur in dieser Sitzung.")


def submit_prompt(provider: LLMProvider, endpoint: str, model: str, prompt: str) -> None:
    active_chat = get_active_chat(
        st.session_state.chats,
        st.session_state.active_chat_id,
    )
    messages = active_chat["messages"]
    previous_title = active_chat["title"]

    try:
        normalized_endpoint, normalized_model, normalized_prompt = validate_chat_input(
            endpoint,
            model,
            prompt,
        )
    except ChatValidationError as exc:
        st.warning(str(exc))
        return

    if active_chat["title"] == DEFAULT_CHAT_TITLE and not messages:
        active_chat["title"] = derive_chat_title(normalized_prompt)

    append_user_message(messages, normalized_prompt)

    with st.chat_message("user"):
        st.write(normalized_prompt)

    answer_parts = []
    try:
        with st.chat_message("assistant"):
            streamed_answer = st.empty()
            for chunk in provider.stream_chat(
                normalized_endpoint,
                normalized_model,
                messages,
            ):
                answer_parts.append(chunk)
                streamed_answer.write("".join(answer_parts))
    except ProviderError as exc:
        rollback_pending_user_message(messages)
        if not messages:
            active_chat["title"] = previous_title
        persist_session_chat_state()
        st.error(str(exc))
    else:
        append_assistant_message(messages, "".join(answer_parts))
        persist_session_chat_state()
        st.rerun()


def initialize_chat_state() -> None:
    if "chats" not in st.session_state:
        try:
            chat_state = chat_state_store_from_env().load()
        except ChatStorageError as exc:
            chat_state = create_initial_chat_state()
            st.session_state.chat_storage_enabled = False
            st.session_state.chat_storage_error = str(exc)
        else:
            st.session_state.chat_storage_enabled = True
            st.session_state.chat_storage_error = None

        set_session_chat_state(chat_state)
        return

    if "active_chat_id" not in st.session_state:
        st.session_state.active_chat_id = st.session_state.chats[0]["id"]
    if "next_chat_id" not in st.session_state:
        st.session_state.next_chat_id = max(
            chat["id"] for chat in st.session_state.chats
        ) + 1


def start_new_chat() -> None:
    chat_state = get_session_chat_state()
    create_next_chat(chat_state)
    set_session_chat_state(chat_state)
    st.session_state.delete_candidate_chat_id = None
    persist_session_chat_state()


def remove_chat(chat_id: int) -> None:
    chat_state = get_session_chat_state()
    delete_chat(chat_state, chat_id)
    set_session_chat_state(chat_state)
    st.session_state.delete_candidate_chat_id = None
    persist_session_chat_state()


def parse_cli_options(argv: list[str]) -> argparse.Namespace:
    """Parse app options given after `--` in `streamlit run app.py -- ...`."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--show-report", action="store_true")
    options, _ = parser.parse_known_args(argv)
    return options


st.set_page_config(page_title="GuideOS Prompt", page_icon=">", layout="wide")

cli_options = parse_cli_options(sys.argv[1:])

pilot_mode = pilot_mode_enabled(os.environ.get("GUIDEOS_PILOT_MODE"))
if pilot_mode:
    configured_password = os.environ.get("GUIDEOS_TEST_PASSWORD")
    if not password_is_configured(configured_password):
        st.error("Der Pilotmodus kann ohne GUIDEOS_TEST_PASSWORD nicht gestartet werden.")
        st.stop()
    if participant_id(st.session_state) is None:
        st.title("GuideOS interner Pilot")
        st.info("Bitte nutze nur synthetische Testdaten.")
        with st.form("pilot_login"):
            username = st.text_input("Benutzername")
            password = st.text_input("Testpasswort", type="password")
            submitted = st.form_submit_button("Anmelden")
        if submitted:
            if normalize_participant_name(username) is None:
                st.error("Bitte gib einen Benutzernamen ein.")
            elif not authenticate(st.session_state, username, password, configured_password):
                st.error("Das Testpasswort ist nicht korrekt.")
        st.stop()
    if st.sidebar.button("Abmelden"):
        logout(st.session_state)
        st.rerun()
    render_assessment(
        participant_id=participant_id(st.session_state),
        pilot_mode=True,
        show_report=cli_options.show_report,
    )
    st.stop()

available_modes = ["Chat", "Assessment"]
if os.environ.get("GUIDEOS_ADMIN_ENABLED") == "1":
    available_modes.extend(["Domain-Admin", "Beratungstest"])

mode = st.sidebar.radio("Modus", available_modes)
if mode == "Assessment":
    render_assessment(
        show_report=cli_options.show_report,
    )
    st.stop()
if mode == "Domain-Admin":
    render_domain_admin()
    st.stop()
if mode == "Beratungstest":
    # The review view belongs to the test-runner; the pilot image ships without it.
    from testsuite.review_app import render_review

    render_review()
    st.stop()

initialize_chat_state()

if st.session_state.get("chat_storage_error"):
    st.warning(
        f"{st.session_state.chat_storage_error} "
        "Die App verwendet für diese Sitzung einen leeren In-Memory-Chat "
        "und überschreibt den gespeicherten Verlauf nicht."
    )

provider_names = list_provider_names()
default_provider_index = provider_names.index(DEFAULT_PROVIDER)
active_chat = get_active_chat(st.session_state.chats, st.session_state.active_chat_id)

chat_list_column, chat_column, llm_column = st.columns([1.1, 3.2, 1.25], gap="large")

with chat_list_column:
    st.title("GuideOS")
    if st.button("Neuer Chat", use_container_width=True):
        start_new_chat()
        st.rerun()

    st.divider()

    for chat in st.session_state.chats:
        button_label = chat["title"]
        if chat["id"] == st.session_state.active_chat_id:
            button_label = f"> {button_label}"

        if st.button(
            button_label,
            key=f"chat_{chat['id']}",
            use_container_width=True,
        ):
            st.session_state.active_chat_id = chat["id"]
            st.session_state.delete_candidate_chat_id = None
            persist_session_chat_state()
            st.rerun()

        if st.session_state.get("delete_candidate_chat_id") == chat["id"]:
            st.warning(f"'{chat['title']}' wirklich löschen?")
            confirm_column, cancel_column = st.columns(2)
            with confirm_column:
                if st.button(
                    "Löschen",
                    key=f"confirm_delete_chat_{chat['id']}",
                    use_container_width=True,
                ):
                    remove_chat(chat["id"])
                    st.rerun()
            with cancel_column:
                if st.button(
                    "Abbrechen",
                    key=f"cancel_delete_chat_{chat['id']}",
                    use_container_width=True,
                ):
                    st.session_state.delete_candidate_chat_id = None
                    st.rerun()
        elif st.button(
            "Chat löschen",
            key=f"delete_chat_{chat['id']}",
            use_container_width=True,
        ):
            st.session_state.delete_candidate_chat_id = chat["id"]
            st.rerun()

with llm_column:
    st.header("LLM")
    provider_name = st.selectbox(
        "Provider",
        provider_names,
        index=default_provider_index,
        format_func=lambda name: get_provider(name).display_name,
    )
    provider = get_provider(provider_name)
    endpoint = st.text_input(
        "Endpoint",
        value=provider.default_endpoint,
        key=f"{provider.name}_endpoint",
    )
    model = select_model(provider, endpoint, label="Model", key_prefix=f"chat_{provider.name}")
    if st.button("Aktiven Chat leeren", use_container_width=True):
        clear_chat(active_chat)
        active_chat["title"] = DEFAULT_CHAT_TITLE
        persist_session_chat_state()
        st.rerun()

with chat_column:
    st.title(active_chat["title"])

    for message in active_chat["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    prompt = st.chat_input("Prompt eingeben...")

    if prompt:
        submit_prompt(provider, endpoint, model, prompt)
