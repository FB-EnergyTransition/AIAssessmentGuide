from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from guideos.domains.loader import DomainPackError, load_domain_pack, load_domain_pack_bytes
from guideos.domains.models import DomainPack


CATALOG_VERSION = 1
DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"
DEFAULT_LOCAL_DOMAIN_PACKS_ROOT = Path(".guideos") / "domain_packs"
DEFAULT_DOMAIN_CATALOG_PATH = Path(".guideos") / "domain_catalog.json"

DomainPackSource = Literal["bundled", "local"]


@dataclass(frozen=True)
class DomainPackRef:
    domain_id: str
    version: str
    path: Path | None
    source: DomainPackSource
    active: bool
    valid: bool
    name: str | None = None
    error: str | None = None
    location: str = ""
    raw: bytes | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DraftPack:
    """A local admin draft as stored by a catalog store."""

    domain_id: str
    version: str
    raw: bytes
    location: str


class DomainCatalogError(Exception):
    pass


class CatalogStore(Protocol):
    """Persistence for admin drafts and the active-version manifest."""

    def read_active_versions(self) -> dict[str, str] | None: ...

    def write_active_versions(self, versions: dict[str, str]) -> None: ...

    def list_drafts(self) -> list[DraftPack]: ...

    def save_draft(self, domain_id: str, version: str, data: dict[str, Any]) -> str: ...

    def delete_draft(self, domain_id: str, version: str) -> None: ...


class LocalCatalogStore:
    """File-backed store: drafts under a local root, manifest as JSON."""

    def __init__(
        self,
        local_root: Path = DEFAULT_LOCAL_DOMAIN_PACKS_ROOT,
        catalog_path: Path = DEFAULT_DOMAIN_CATALOG_PATH,
    ):
        self.local_root = local_root
        self.catalog_path = catalog_path

    def read_active_versions(self) -> dict[str, str] | None:
        if not self.catalog_path.exists():
            return None
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainCatalogError("Der Domain-Katalog ist ungültig.") from exc
        if not isinstance(data, dict) or data.get("version") != CATALOG_VERSION:
            raise DomainCatalogError("Der Domain-Katalog hat eine nicht unterstützte Version.")
        active_versions = data.get("active_versions")
        if not isinstance(active_versions, dict):
            raise DomainCatalogError("Der Domain-Katalog enthält keine aktiven Versionen.")
        return {
            str(domain_id): str(version)
            for domain_id, version in active_versions.items()
        }

    def write_active_versions(self, versions: dict[str, str]) -> None:
        _write_json(
            self.catalog_path,
            {"version": CATALOG_VERSION, "active_versions": versions},
        )

    def list_drafts(self) -> list[DraftPack]:
        drafts = []
        for path in _pack_paths(self.local_root):
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            identity = _read_identity(raw)
            if identity is None:
                continue
            drafts.append(DraftPack(identity[0], identity[1], raw, str(path)))
        return drafts

    def save_draft(self, domain_id: str, version: str, data: dict[str, Any]) -> str:
        path = self.local_root / domain_id / version / "pack.json"
        _write_json(path, data)
        return str(path)

    def delete_draft(self, domain_id: str, version: str) -> None:
        deleted = False
        for path in _pack_paths(self.local_root):
            try:
                if _read_identity(path.read_bytes()) != (domain_id, version):
                    continue
                path.unlink()
            except OSError as exc:
                raise DomainCatalogError("Der Draft konnte nicht gelöscht werden.") from exc
            deleted = True
            for directory in (path.parent, path.parent.parent):
                try:
                    directory.rmdir()
                except OSError:
                    break
        if not deleted:
            raise _draft_not_found(domain_id, version)


def catalog_store_from_env(
    *,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
) -> CatalogStore:
    database_url = os.environ.get("GUIDEOS_DATABASE_URL")
    if database_url and local_root is None and catalog_path is None:
        from guideos.domains.postgres_catalog import PostgresCatalogStore

        return PostgresCatalogStore(database_url)
    return LocalCatalogStore(
        local_root or DEFAULT_LOCAL_DOMAIN_PACKS_ROOT,
        catalog_path or DEFAULT_DOMAIN_CATALOG_PATH,
    )


def list_domain_versions(
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> list[DomainPackRef]:
    store = _resolve_store(store, local_root, catalog_path)
    active_versions = _active_versions(store, bundled_root)
    refs = [
        *_discover_bundled_refs(bundled_root, active_versions),
        *_discover_local_refs(store, active_versions),
    ]
    return sorted(refs, key=lambda item: (item.domain_id, _version_key(item.version), item.source))


def list_active_domain_versions(
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> list[DomainPackRef]:
    active = [item for item in list_domain_versions(
        bundled_root=bundled_root,
        local_root=local_root,
        catalog_path=catalog_path,
        store=store,
    ) if item.active and item.valid]
    preferred: dict[str, DomainPackRef] = {}
    for ref in active:
        existing = preferred.get(ref.domain_id)
        if existing is None or ref.source == "local":
            preferred[ref.domain_id] = ref
    return sorted(preferred.values(), key=lambda item: item.name or item.domain_id)


def load_active_domain_pack(
    domain_id: str,
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> DomainPack:
    refs = [
        item for item in list_active_domain_versions(
            bundled_root=bundled_root,
            local_root=local_root,
            catalog_path=catalog_path,
            store=store,
        )
        if item.domain_id == domain_id
    ]
    if not refs:
        raise DomainCatalogError(f"Keine aktive Domain-Version für {domain_id}.")
    return load_domain_pack_ref(refs[0])


def load_domain_pack_version(
    domain_id: str,
    version: str,
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> DomainPack:
    ref = _find_ref(domain_id, version, bundled_root, _resolve_store(store, local_root, catalog_path))
    return load_domain_pack_ref(ref)


def load_domain_pack_ref(ref: DomainPackRef) -> DomainPack:
    """Load a discovered pack: bundled packs from disk, drafts from their store."""
    if ref.raw is not None:
        return load_domain_pack_bytes(ref.raw)
    if ref.path is None:
        raise DomainCatalogError(
            f"Domain-Version {ref.domain_id}/{ref.version} hat keine ladbare Quelle."
        )
    return load_domain_pack(ref.path)


def save_domain_pack_draft(
    pack: DomainPack,
    *,
    local_root: Path | None = None,
    store: CatalogStore | None = None,
) -> str:
    return _resolve_store(store, local_root, None).save_draft(
        pack.domain_id, pack.version, pack_draft_payload(pack)
    )


def pack_draft_payload(pack: DomainPack) -> dict[str, Any]:
    """Pack data as stored in a draft. Unset web_sources, advisory_guidance
    and knowledge_recommendations are left out so packs without them
    serialize, and therefore hash, exactly as before the fields existed."""
    payload = pack.model_dump(mode="json", exclude={"content_hash"})
    for optional in ("web_sources", "advisory_guidance", "knowledge_recommendations", "topics",
                     "tools", "result_chat"):
        if payload[optional] is None:
            del payload[optional]
    if "tools" in payload:
        if payload["tools"].get("after_import") is None:
            payload["tools"].pop("after_import", None)
        if payload["tools"].get("at_completion") is None:
            payload["tools"].pop("at_completion", None)
        # Steps arrived before arguments_from_answers; unset ones stay implicit.
        steps = payload["tools"].get("after_import")
        for step in [*(steps if isinstance(steps, list) else [steps] if steps else []),
                     *(payload["tools"].get("at_completion") or [])]:
            if not step.get("arguments_from_answers"):
                step.pop("arguments_from_answers", None)
            else:
                for spec in step["arguments_from_answers"].values():
                    for key in [key for key, value in spec.items() if value is None and key != "default"]:
                        del spec[key]
        if not payload["tools"].get("derive"):
            payload["tools"].pop("derive", None)
        else:
            payload["tools"]["derive"] = [
                {key: value for key, value in item.items() if value is not None}
                for item in payload["tools"]["derive"]
            ]
    if "result_chat" in payload:
        # The chat texts arrived after the compact switch; unset ones stay implicit.
        payload["result_chat"] = {
            key: value for key, value in payload["result_chat"].items() if value is not None
        }
    for question in payload["questions"]:
        # The upload block exists only for file questions; the same reason.
        if question["upload"] is None:
            del question["upload"]
    for recommendation in payload["recommendations"]:
        # Figures in recommendation texts arrived later; unset ones stay implicit.
        for optional in ("figures", "message_with_figures"):
            if recommendation[optional] is None:
                del recommendation[optional]
        for figure in (recommendation.get("figures") or {}).values():
            for key in [key for key, value in figure.items() if value is None]:
                del figure[key]
    for rule in payload["rules"]:
        # Rule kinds arrived later; the defaults stay implicit for the same reason.
        if rule["kind"] == "recommend":
            del rule["kind"]
        if not rule["topics"]:
            del rule["topics"]
        if rule["priority"] is None:
            del rule["priority"]
        if rule["metric"] is None:
            del rule["metric"]
        else:
            rule["metric"] = {key: value for key, value in rule["metric"].items() if value is not None}
        if rule["origin"] == "expert":
            del rule["origin"]
        if rule["source_reference"] is None:
            del rule["source_reference"]
        if rule["status"] == "approved":
            del rule["status"]
    return payload


def save_domain_pack_draft_data(
    data: dict[str, Any],
    *,
    local_root: Path | None = None,
    store: CatalogStore | None = None,
) -> str:
    domain_id = _string_field(data, "domain_id")
    version = _string_field(data, "version")
    return _resolve_store(store, local_root, None).save_draft(domain_id, version, data)


def delete_domain_pack_draft(
    domain_id: str,
    version: str,
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> None:
    """Remove one draft version. Bundled packs live in the repository and an
    active version would leave its domain without an assessment, so both are
    refused. Sessions recorded against the version are the caller's concern."""
    store = _resolve_store(store, local_root, catalog_path)
    matches = [
        item for item in list_domain_versions(bundled_root=bundled_root, store=store)
        if item.domain_id == domain_id and item.version == version
    ]
    drafts = [item for item in matches if item.source == "local"]
    if matches and not drafts:
        raise DomainCatalogError(
            "Gebündelte Packs lassen sich nicht im Admin löschen; sie liegen im "
            "Repository unter domain_packs/."
        )
    if not drafts:
        raise _draft_not_found(domain_id, version)
    if drafts[0].active:
        raise DomainCatalogError(
            f"Version {domain_id}/{version} ist aktiv. Aktiviere zuerst eine andere Version."
        )
    store.delete_draft(domain_id, version)


def _draft_not_found(domain_id: str, version: str) -> DomainCatalogError:
    return DomainCatalogError(f"Draft {domain_id}/{version} wurde nicht gefunden.")


def activate_domain_version(
    domain_id: str,
    version: str,
    *,
    bundled_root: Path = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    local_root: Path | None = None,
    catalog_path: Path | None = None,
    store: CatalogStore | None = None,
) -> DomainPack:
    store = _resolve_store(store, local_root, catalog_path)
    ref = _find_ref(domain_id, version, bundled_root, store)
    pack = load_domain_pack_ref(ref)
    unapproved = [rule.rule_id for rule in pack.rules if rule.status != "approved"]
    if unapproved:
        raise DomainCatalogError(
            f"Version {domain_id}/{version} kann nicht aktiviert werden, solange Regeln "
            f"nicht freigegeben sind: {', '.join(unapproved)}."
        )
    active_versions = _active_versions(store, bundled_root)
    active_versions[domain_id] = version
    store.write_active_versions(active_versions)
    return pack


def _resolve_store(
    store: CatalogStore | None,
    local_root: Path | None,
    catalog_path: Path | None,
) -> CatalogStore:
    if store is not None:
        return store
    return catalog_store_from_env(local_root=local_root, catalog_path=catalog_path)


def _find_ref(
    domain_id: str,
    version: str,
    bundled_root: Path,
    store: CatalogStore,
) -> DomainPackRef:
    matches = [
        item for item in list_domain_versions(bundled_root=bundled_root, store=store)
        if item.domain_id == domain_id and item.version == version
    ]
    if not matches:
        raise DomainCatalogError(f"Domain-Version {domain_id}/{version} wurde nicht gefunden.")
    local = [item for item in matches if item.source == "local"]
    return local[0] if local else matches[0]


def _active_versions(store: CatalogStore, bundled_root: Path) -> dict[str, str]:
    versions = store.read_active_versions()
    if versions is None:
        return _default_active_versions(bundled_root)
    return versions


def _default_active_versions(root: Path) -> dict[str, str]:
    versions: dict[str, list[str]] = {}
    for path in _pack_paths(root):
        try:
            identity = _read_identity(path.read_bytes())
        except OSError:
            continue
        if identity is None:
            continue
        domain_id, version = identity
        versions.setdefault(domain_id, []).append(version)
    return {
        domain_id: sorted(items, key=_version_key)[-1]
        for domain_id, items in versions.items()
    }


def _discover_bundled_refs(
    root: Path,
    active_versions: dict[str, str],
) -> list[DomainPackRef]:
    refs = []
    for path in _pack_paths(root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        identity = _read_identity(raw)
        if identity is None:
            continue
        refs.append(
            _build_ref(identity[0], identity[1], raw, "bundled", path, str(path), active_versions)
        )
    return refs


def _discover_local_refs(
    store: CatalogStore,
    active_versions: dict[str, str],
) -> list[DomainPackRef]:
    return [
        _build_ref(
            draft.domain_id, draft.version, draft.raw, "local", None, draft.location, active_versions
        )
        for draft in store.list_drafts()
    ]


def _build_ref(
    domain_id: str,
    version: str,
    raw: bytes,
    source: DomainPackSource,
    path: Path | None,
    location: str,
    active_versions: dict[str, str],
) -> DomainPackRef:
    active = active_versions.get(domain_id) == version
    try:
        pack = load_domain_pack_bytes(raw)
    except DomainPackError as exc:
        return DomainPackRef(
            domain_id=domain_id,
            version=version,
            path=path,
            source=source,
            active=active,
            valid=False,
            error=str(exc),
            location=location,
            raw=None if path else raw,
        )
    return DomainPackRef(
        domain_id=domain_id,
        version=version,
        path=path,
        source=source,
        active=active,
        valid=True,
        name=pack.name,
        location=location,
        raw=None if path else raw,
    )


def _pack_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*/*/pack.json"))


def _read_identity(raw: bytes) -> tuple[str, str] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    domain_id = data.get("domain_id")
    version = data.get("version")
    if not isinstance(domain_id, str) or not isinstance(version, str):
        return None
    return domain_id, version


def _string_field(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DomainCatalogError(f"Das Feld {field} ist erforderlich.")
    return value.strip()


def serialize_pack_data(data: dict[str, Any]) -> str:
    """Canonical draft serialization shared by all stores (hash-stable)."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(serialize_pack_data(data), encoding="utf-8")
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise DomainCatalogError("Die Domain-Konfiguration konnte nicht gespeichert werden.") from exc


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)
