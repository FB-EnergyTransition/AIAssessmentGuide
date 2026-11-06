from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from guideos.conversations.models import Conversation


# Version 2: conversations reference a profile instead of an assessment session.
CONVERSATION_STORAGE_VERSION = 2
DEFAULT_CONVERSATION_STORAGE_PATH = Path(".guideos") / "conversations.json"


class ConversationRepositoryError(Exception):
    pass


class ConversationConflictError(ConversationRepositoryError):
    pass


@runtime_checkable
class ConversationRepository(Protocol):
    def list(self, participant_id: str | None = None) -> list[Conversation]:
        ...

    def get(
        self,
        conversation_id: str,
        participant_id: str | None = None,
    ) -> Conversation:
        ...

    def save(self, conversation: Conversation) -> None:
        ...

    def delete(self, conversation_id: str, participant_id: str | None = None) -> None:
        ...


def _not_found(conversation_id: str) -> LookupError:
    return LookupError(f"Konversation {conversation_id} wurde nicht gefunden.")


class LocalConversationRepository:
    def __init__(self, path: Path = DEFAULT_CONVERSATION_STORAGE_PATH):
        self.path = path

    def list(self, participant_id: str | None = None) -> list[Conversation]:
        conversations = self._load()
        if participant_id is None:
            return conversations
        return [item for item in conversations if item.participant_id == participant_id]

    def get(
        self,
        conversation_id: str,
        participant_id: str | None = None,
    ) -> Conversation:
        for conversation in self.list(participant_id):
            if conversation.conversation_id == conversation_id:
                return conversation
        raise _not_found(conversation_id)

    def save(self, conversation: Conversation) -> None:
        conversations = self._load()
        for index, existing in enumerate(conversations):
            if existing.conversation_id == conversation.conversation_id:
                if conversation.revision != existing.revision:
                    raise ConversationConflictError(
                        "Die Konversation wurde zwischenzeitlich geändert."
                    )
                conversation.revision = existing.revision + 1
                conversations[index] = conversation
                break
        else:
            if conversation.revision != 0:
                raise ConversationConflictError(
                    "Die Konversation wurde zwischenzeitlich erstellt."
                )
            conversation.revision = 1
            conversations.append(conversation)
        self._write(conversations)

    def delete(self, conversation_id: str, participant_id: str | None = None) -> None:
        conversations = self._load()
        remaining = [
            item
            for item in conversations
            if not (
                item.conversation_id == conversation_id
                and (participant_id is None or item.participant_id == participant_id)
            )
        ]
        if len(remaining) == len(conversations):
            raise _not_found(conversation_id)
        self._write(remaining)

    def _load(self) -> list[Conversation]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatei enthält kein gültiges JSON."
            ) from exc
        except OSError as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatei konnte nicht gelesen werden."
            ) from exc
        return self._decode(data)

    def _decode(self, data: Any) -> list[Conversation]:
        if not isinstance(data, dict):
            raise ConversationRepositoryError(
                "Die Konversationsdatei hat ein ungültiges Format."
            )
        if data.get("version") == 1:
            raise ConversationRepositoryError(
                "Die Konversationsdatei hat Version 1 (Chats an Sessions gebunden). "
                "Seit der Umstellung auf Profile wird sie nicht mehr gelesen; "
                "bitte die Datei löschen."
            )
        if data.get("version") != CONVERSATION_STORAGE_VERSION:
            raise ConversationRepositoryError(
                "Die Konversationsdatei hat eine nicht unterstützte Version."
            )
        conversations = data.get("conversations")
        if not isinstance(conversations, list):
            raise ConversationRepositoryError(
                "Die Konversationsdatei enthält keine gültige Liste."
            )
        try:
            decoded = [Conversation.model_validate(item) for item in conversations]
        except ValidationError as exc:
            raise ConversationRepositoryError(
                "Die Konversationsdatei enthält ungültige Daten."
            ) from exc
        ids = [item.conversation_id for item in decoded]
        if len(ids) != len(set(ids)):
            raise ConversationRepositoryError(
                "Die Konversationsdatei enthält doppelte IDs."
            )
        return decoded

    def _write(self, conversations: list[Conversation]) -> None:
        payload = {
            "version": CONVERSATION_STORAGE_VERSION,
            "conversations": [item.model_dump(mode="json") for item in conversations],
        }
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConversationRepositoryError(
                "Die Konversationsdatei konnte nicht gespeichert werden."
            ) from exc


class ParticipantConversationRepository:
    """Restricts a conversation repository to one participant."""

    def __init__(self, repository: ConversationRepository, participant_id: str):
        self.repository = repository
        self.participant_id = participant_id

    def list(self, participant_id: str | None = None) -> list[Conversation]:
        if participant_id not in {None, self.participant_id}:
            return []
        return self.repository.list(self.participant_id)

    def get(
        self,
        conversation_id: str,
        participant_id: str | None = None,
    ) -> Conversation:
        if participant_id not in {None, self.participant_id}:
            raise _not_found(conversation_id)
        return self.repository.get(conversation_id, self.participant_id)

    def save(self, conversation: Conversation) -> None:
        if conversation.participant_id is None:
            conversation.participant_id = self.participant_id
        if conversation.participant_id != self.participant_id:
            raise _not_found(conversation.conversation_id)
        self.repository.save(conversation)

    def delete(self, conversation_id: str, participant_id: str | None = None) -> None:
        if participant_id not in {None, self.participant_id}:
            raise _not_found(conversation_id)
        self.repository.delete(conversation_id, self.participant_id)
