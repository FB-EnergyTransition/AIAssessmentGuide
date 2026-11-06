from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from guideos.knowledge.models import KnowledgeDocument, KnowledgeError


KNOWLEDGE_STORAGE_VERSION = 1
_CONTENT_HASH = re.compile(r"[0-9a-f]{64}")
DEFAULT_KNOWLEDGE_STORAGE_PATH = Path(".guideos") / "knowledge.json"


class KnowledgeStoreError(KnowledgeError):
    pass


class KnowledgeStore(Protocol):
    """Persistence for the knowledge base. Documents belong to a domain, not
    to a pack version or a participant."""

    def list(self, domain_id: str) -> list[KnowledgeDocument]: ...

    def save(self, document: KnowledgeDocument) -> None: ...

    def delete(self, document_id: str) -> None: ...

    # Original files are kept once per content hash and removed together with
    # the last document that has this hash.
    def save_original(self, content_hash: str, raw: bytes) -> None: ...

    def load_original(self, content_hash: str) -> bytes | None: ...

    def delete_original(self, content_hash: str) -> None: ...


def not_found(document_id: str) -> LookupError:
    return LookupError(f"Dokument {document_id} wurde nicht gefunden.")


class LocalKnowledgeStore:
    def __init__(self, path: Path = DEFAULT_KNOWLEDGE_STORAGE_PATH):
        self.path = path

    def list(self, domain_id: str) -> list[KnowledgeDocument]:
        return [item for item in self._load() if item.domain_id == domain_id]

    def save(self, document: KnowledgeDocument) -> None:
        documents = self._load()
        for index, existing in enumerate(documents):
            if existing.document_id == document.document_id:
                documents[index] = document
                break
        else:
            documents.append(document)
        self._write(documents)

    def delete(self, document_id: str) -> None:
        documents = self._load()
        remaining = [item for item in documents if item.document_id != document_id]
        if len(remaining) == len(documents):
            raise not_found(document_id)
        self._write(remaining)
        removed = next(item for item in documents if item.document_id == document_id)
        if all(item.content_hash != removed.content_hash for item in remaining):
            self.delete_original(removed.content_hash)

    def save_original(self, content_hash: str, raw: bytes) -> None:
        target = self._original_path(content_hash)
        if target is None:
            raise KnowledgeStoreError("Das Originaldokument konnte nicht gespeichert werden.")
        temp_path = target.with_name(f".{target.name}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(raw)
            os.replace(temp_path, target)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise KnowledgeStoreError(
                "Das Originaldokument konnte nicht gespeichert werden."
            ) from exc

    def load_original(self, content_hash: str) -> bytes | None:
        target = self._original_path(content_hash)
        if target is None or not target.exists():
            return None
        try:
            return target.read_bytes()
        except OSError as exc:
            raise KnowledgeStoreError(
                "Das Originaldokument konnte nicht gelesen werden."
            ) from exc

    def delete_original(self, content_hash: str) -> None:
        target = self._original_path(content_hash)
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                raise KnowledgeStoreError(
                    "Das Originaldokument konnte nicht gelöscht werden."
                ) from exc

    def _original_path(self, content_hash: str) -> Path | None:
        """Files live next to the JSON file; only a SHA-256 hex digest names one."""
        if not _CONTENT_HASH.fullmatch(content_hash):
            return None
        return self.path.with_name(f"{self.path.stem}_files") / content_hash

    def _load(self) -> list[KnowledgeDocument]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise KnowledgeStoreError(
                "Die Datei der Wissensbasis enthält kein gültiges JSON."
            ) from exc
        except OSError as exc:
            raise KnowledgeStoreError(
                "Die Datei der Wissensbasis konnte nicht gelesen werden."
            ) from exc
        if not isinstance(data, dict) or data.get("version") != KNOWLEDGE_STORAGE_VERSION:
            raise KnowledgeStoreError(
                "Die Datei der Wissensbasis hat eine nicht unterstützte Version."
            )
        try:
            return [KnowledgeDocument.model_validate(item) for item in data.get("documents")]
        except (TypeError, ValidationError) as exc:
            raise KnowledgeStoreError(
                "Die Datei der Wissensbasis enthält ungültige Daten."
            ) from exc

    def _write(self, documents: list[KnowledgeDocument]) -> None:
        payload = {
            "version": KNOWLEDGE_STORAGE_VERSION,
            "documents": [item.model_dump(mode="json") for item in documents],
        }
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Compact on purpose: indented embedding vectors would bloat the file.
            temp_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise KnowledgeStoreError(
                "Die Datei der Wissensbasis konnte nicht gespeichert werden."
            ) from exc
