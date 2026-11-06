from typing import TypeAlias, TypedDict


Message: TypeAlias = dict[str, str]
DEFAULT_CHAT_TITLE = "Neuer Chat"
MAX_CHAT_TITLE_LENGTH = 36


class Chat(TypedDict):
    id: int
    title: str
    messages: list[Message]


class ChatState(TypedDict):
    chats: list[Chat]
    active_chat_id: int
    next_chat_id: int


class ChatValidationError(ValueError):
    pass


def validate_chat_input(endpoint: str, model: str, prompt: str) -> tuple[str, str, str]:
    normalized_endpoint = endpoint.strip().rstrip("/")
    normalized_model = model.strip()
    normalized_prompt = prompt.strip()

    if not normalized_prompt:
        raise ChatValidationError("Bitte gib zuerst einen Prompt ein.")
    if not normalized_endpoint:
        raise ChatValidationError("Bitte gib einen LLM-Endpunkt ein.")
    if not normalized_endpoint.startswith(("http://", "https://")):
        raise ChatValidationError(
            "Bitte gib eine vollständige URL ein, z. B. http://localhost:11434.",
        )
    if not normalized_model:
        raise ChatValidationError("Bitte gib ein Modell ein.")

    return normalized_endpoint, normalized_model, normalized_prompt


def append_user_message(messages: list[Message], prompt: str) -> None:
    messages.append({"role": "user", "content": prompt})


def append_assistant_message(messages: list[Message], content: str) -> None:
    messages.append({"role": "assistant", "content": content})


def rollback_pending_user_message(messages: list[Message]) -> None:
    if messages and messages[-1].get("role") == "user":
        messages.pop()


def create_chat(chat_id: int, title: str = DEFAULT_CHAT_TITLE) -> Chat:
    return {"id": chat_id, "title": title, "messages": []}


def create_initial_chat_state() -> ChatState:
    return {"chats": [create_chat(1)], "active_chat_id": 1, "next_chat_id": 2}


def create_next_chat(chat_state: ChatState) -> Chat:
    chat = create_chat(chat_state["next_chat_id"])
    chat_state["chats"].append(chat)
    chat_state["active_chat_id"] = chat["id"]
    chat_state["next_chat_id"] += 1
    return chat


def get_active_chat(chats: list[Chat], active_chat_id: int) -> Chat:
    for chat in chats:
        if chat["id"] == active_chat_id:
            return chat

    raise LookupError(f"Chat mit ID {active_chat_id} wurde nicht gefunden.")


def derive_chat_title(prompt: str) -> str:
    title = " ".join(prompt.strip().split())
    if not title:
        return DEFAULT_CHAT_TITLE
    if len(title) <= MAX_CHAT_TITLE_LENGTH:
        return title

    truncated = title[: MAX_CHAT_TITLE_LENGTH - 3].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}..."


def clear_chat(chat: Chat) -> None:
    chat["messages"] = []


def delete_chat(chat_state: ChatState, chat_id: int) -> None:
    remaining_chats = [chat for chat in chat_state["chats"] if chat["id"] != chat_id]
    if len(remaining_chats) == len(chat_state["chats"]):
        raise LookupError(f"Chat mit ID {chat_id} wurde nicht gefunden.")

    if not remaining_chats:
        chat_state["chats"] = []
        create_next_chat(chat_state)
        return

    chat_state["chats"] = remaining_chats
    if chat_state["active_chat_id"] == chat_id:
        chat_state["active_chat_id"] = remaining_chats[0]["id"]
