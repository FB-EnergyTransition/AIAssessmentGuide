import json

import pytest

from guideos.domains.catalog import (
    DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    DomainCatalogError,
    activate_domain_version,
    list_active_domain_versions,
    list_domain_versions,
    load_active_domain_pack,
    delete_domain_pack_draft,
    load_domain_pack_version,
    pack_draft_payload,
    save_domain_pack_draft,
    save_domain_pack_draft_data,
)
from guideos.domains.loader import DomainPackError, load_domain_pack
from guideos.domains.models import WebSourceSettings


def test_catalog_discovers_bundled_packs():
    refs = list_domain_versions()

    keys = {(ref.domain_id, ref.version) for ref in refs}
    assert ("verein-demo", "1.0.0") in keys
    bundled = {
        (domain.name, version.name)
        for domain in DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT.iterdir() if domain.is_dir()
        for version in domain.iterdir() if (version / "pack.json").exists()
    }
    assert bundled <= keys


def test_missing_manifest_falls_back_to_bundled_active_pack(tmp_path):
    refs = list_active_domain_versions(
        bundled_root=_bundled_root(),
        local_root=tmp_path / "domain_packs",
        catalog_path=tmp_path / "domain_catalog.json",
    )

    assert {ref.domain_id for ref in refs} == {
        domain.name for domain in _bundled_root().iterdir() if domain.is_dir()
    }


def test_catalog_discovers_local_admin_pack(tmp_path):
    local_root = tmp_path / "domain_packs"
    pack = _minimal_pack("local-demo", "1.0.0")
    save_domain_pack_draft_data(pack, local_root=local_root)

    refs = list_domain_versions(
        bundled_root=_bundled_root(),
        local_root=local_root,
        catalog_path=tmp_path / "domain_catalog.json",
    )

    local_ref = next(ref for ref in refs if ref.domain_id == "local-demo")
    assert local_ref.source == "local"
    assert local_ref.valid is True


def test_activation_selects_local_pack(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    save_domain_pack_draft_data(
        _minimal_pack("local-demo", "1.0.0"),
        local_root=local_root,
    )

    activated = activate_domain_version(
        "local-demo",
        "1.0.0",
        bundled_root=_bundled_root(),
        local_root=local_root,
        catalog_path=catalog_path,
    )
    loaded = load_active_domain_pack(
        "local-demo",
        bundled_root=_bundled_root(),
        local_root=local_root,
        catalog_path=catalog_path,
    )

    assert activated.domain_id == "local-demo"
    assert loaded.domain_id == "local-demo"
    assert json.loads(catalog_path.read_text(encoding="utf-8"))["active_versions"][
        "local-demo"
    ] == "1.0.0"


def test_load_domain_pack_version_loads_inactive_version(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    save_domain_pack_draft_data(
        _minimal_pack("local-demo", "1.0.0"),
        local_root=local_root,
    )
    save_domain_pack_draft_data(
        _minimal_pack("local-demo", "1.1.0"),
        local_root=local_root,
    )
    activate_domain_version(
        "local-demo",
        "1.1.0",
        bundled_root=_bundled_root(),
        local_root=local_root,
        catalog_path=catalog_path,
    )

    loaded = load_domain_pack_version(
        "local-demo",
        "1.0.0",
        bundled_root=_bundled_root(),
        local_root=local_root,
        catalog_path=catalog_path,
    )

    assert loaded.domain_id == "local-demo"
    assert loaded.version == "1.0.0"


def test_invalid_local_pack_cannot_be_activated(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    invalid = _minimal_pack("broken-demo", "1.0.0")
    invalid["rules"] = [
        {
            "rule_id": "unknown",
            "field_key": "missing",
            "operator": "equals",
            "value": "x",
            "reason_code": "BROKEN",
            "recommendation_ids": [],
        }
    ]
    save_domain_pack_draft_data(invalid, local_root=local_root)

    with pytest.raises(DomainPackError):
        activate_domain_version(
            "broken-demo",
            "1.0.0",
            bundled_root=_bundled_root(),
            local_root=local_root,
            catalog_path=catalog_path,
        )


def test_catalog_rejects_missing_domain_identity(tmp_path):
    with pytest.raises(DomainCatalogError):
        save_domain_pack_draft_data({}, local_root=tmp_path / "domain_packs")


def _bundled_root():
    return DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT


def _minimal_pack(domain_id: str, version: str):
    return {
        "domain_id": domain_id,
        "version": version,
        "name": "Local Demo",
        "description": "Demo",
        "default_locale": "de",
        "questions": [
            {
                "question_id": "kind",
                "field_key": "kind",
                "text": "Art?",
                "field_type": "select",
                "required": True,
                "options": [{"value": "one", "label": "Eins"}],
            }
        ],
        "rules": [],
        "recommendations": [],
    }


class InMemoryCatalogStore:
    """Catalog store double: drafts and active versions live in memory."""

    def __init__(self):
        self.active = None
        self.drafts = {}

    def read_active_versions(self):
        return None if self.active is None else dict(self.active)

    def write_active_versions(self, versions):
        self.active = dict(versions)

    def list_drafts(self):
        from guideos.domains.catalog import DraftPack

        return [
            DraftPack(domain_id=d, version=v, raw=raw, location=f"memory:{d}/{v}")
            for (d, v), raw in sorted(self.drafts.items())
        ]

    def save_draft(self, domain_id, version, data):
        self.drafts[(domain_id, version)] = (
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        return f"memory:{domain_id}/{version}"

    def delete_draft(self, domain_id, version):
        del self.drafts[(domain_id, version)]


def test_catalog_uses_an_injected_store_for_drafts_and_activation():
    store = InMemoryCatalogStore()
    save_domain_pack_draft_data(_minimal_pack("store-demo", "1.0.0"), store=store)

    refs = list_domain_versions(bundled_root=_bundled_root(), store=store)
    local_ref = next(ref for ref in refs if ref.domain_id == "store-demo")
    assert local_ref.source == "local"
    assert local_ref.valid is True
    assert local_ref.path is None
    assert local_ref.location == "memory:store-demo/1.0.0"

    activated = activate_domain_version(
        "store-demo", "1.0.0", bundled_root=_bundled_root(), store=store
    )
    loaded = load_active_domain_pack("store-demo", bundled_root=_bundled_root(), store=store)

    assert activated.domain_id == "store-demo"
    assert loaded.version == "1.0.0"
    assert store.active["store-demo"] == "1.0.0"
    assert store.active["verein-demo"] == "1.0.0"
    assert len(loaded.content_hash) == 64


def test_catalog_store_from_env_prefers_postgres(monkeypatch, tmp_path):
    from guideos.domains.catalog import (
        LocalCatalogStore,
        catalog_store_from_env,
    )
    from guideos.domains.postgres_catalog import PostgresCatalogStore

    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    local = catalog_store_from_env(
        local_root=tmp_path / "packs", catalog_path=tmp_path / "catalog.json"
    )
    assert isinstance(local, LocalCatalogStore)

    monkeypatch.setenv("GUIDEOS_DATABASE_URL", "postgresql://user:pw@db/guideos")
    assert isinstance(catalog_store_from_env(), PostgresCatalogStore)


def test_draft_serialization_only_contains_web_sources_when_configured(tmp_path):
    pack = load_domain_pack()
    configured = pack.model_copy(
        update={"web_sources": WebSourceSettings(enabled=True, allowed_domains=["klimaaktiv.at"])}
    )

    assert "web_sources" not in pack_draft_payload(pack)
    assert "content_hash" not in pack_draft_payload(pack)
    assert pack_draft_payload(configured)["web_sources"] == {
        "enabled": True,
        "allowed_domains": ["klimaaktiv.at"],
    }
    save_domain_pack_draft(configured.model_copy(update={"version": "9.0.0"}), local_root=tmp_path)
    reloaded = load_domain_pack(tmp_path / "verein-demo" / "9.0.0" / "pack.json")
    assert reloaded.web_sources == configured.web_sources


def test_deleting_a_draft_removes_only_that_version(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    for version in ("1.0.0", "1.0.1"):
        save_domain_pack_draft_data(_minimal_pack("local-demo", version), local_root=local_root)
    save_domain_pack_draft_data(_minimal_pack("einzel-demo", "1.0.0"), local_root=local_root)

    delete_domain_pack_draft(
        "local-demo", "1.0.0", bundled_root=_bundled_root(),
        local_root=local_root, catalog_path=catalog_path,
    )
    delete_domain_pack_draft(
        "einzel-demo", "1.0.0", bundled_root=_bundled_root(),
        local_root=local_root, catalog_path=catalog_path,
    )

    keys = {
        (ref.domain_id, ref.version)
        for ref in list_domain_versions(
            bundled_root=_bundled_root(), local_root=local_root, catalog_path=catalog_path
        )
        if ref.source == "local"
    }
    assert keys == {("local-demo", "1.0.1")}
    assert not (local_root / "local-demo" / "1.0.0").exists()
    assert not (local_root / "einzel-demo").exists()
    assert (local_root / "local-demo" / "1.0.1" / "pack.json").exists()


def test_deleting_refuses_bundled_active_and_unknown_versions(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    paths = {"bundled_root": _bundled_root(), "local_root": local_root, "catalog_path": catalog_path}
    save_domain_pack_draft_data(_minimal_pack("local-demo", "1.0.0"), local_root=local_root)
    activate_domain_version("local-demo", "1.0.0", **paths)

    with pytest.raises(DomainCatalogError, match="ist aktiv"):
        delete_domain_pack_draft("local-demo", "1.0.0", **paths)
    with pytest.raises(DomainCatalogError, match="Gebündelte Packs"):
        delete_domain_pack_draft("verein-demo", "1.0.0", **paths)
    with pytest.raises(DomainCatalogError, match="wurde nicht gefunden"):
        delete_domain_pack_draft("local-demo", "9.9.9", **paths)
    assert (local_root / "local-demo" / "1.0.0" / "pack.json").exists()


def test_deleting_a_draft_works_through_an_injected_store():
    store = InMemoryCatalogStore()
    save_domain_pack_draft_data(_minimal_pack("store-demo", "1.0.0"), store=store)

    delete_domain_pack_draft("store-demo", "1.0.0", bundled_root=_bundled_root(), store=store)

    assert store.drafts == {}


def test_draft_serialization_only_contains_advisory_guidance_when_set(tmp_path):
    pack = load_domain_pack().model_copy(update={"advisory_guidance": None})
    configured = pack.model_copy(update={"advisory_guidance": "Der Kontext ist Österreich."})

    assert "advisory_guidance" not in pack_draft_payload(pack)
    assert pack_draft_payload(configured)["advisory_guidance"] == "Der Kontext ist Österreich."
    save_domain_pack_draft(configured.model_copy(update={"version": "9.0.0"}), local_root=tmp_path)
    reloaded = load_domain_pack(tmp_path / "verein-demo" / "9.0.0" / "pack.json")
    assert reloaded.advisory_guidance == "Der Kontext ist Österreich."


def test_pack_draft_payload_leaves_recommendation_figures_implicit():
    from guideos.domains.loader import validate_domain_pack_data

    bundled = load_domain_pack()
    assert all(
        {"figures", "message_with_figures"}.isdisjoint(item)
        for item in pack_draft_payload(bundled)["recommendations"]
    )
    data = {
        "domain_id": "figures-demo", "version": "1.0.0", "name": "Kennzahlen", "description": "Test",
        "default_locale": "de",
        "questions": [{"question_id": "dataset_file", "field_key": "dataset_file", "text": "Datei?",
                       "field_type": "file", "required": False, "upload": {}}],
        "rules": [{"rule_id": "peaks", "field_key": "dataset_file", "operator": "gte", "value": 1,
                   "metric": {"tool": "analyze_tiers", "pointer": "/totals/months_above"},
                   "reason_code": "PEAKS", "recommendation_ids": ["rec"]}],
        "recommendations": [{
            "recommendation_id": "rec", "priority": 1, "title": "T", "message": "Allgemein.",
            "figures": {"months_above": {"tool": "analyze_tiers", "pointer": "/totals/months_above",
                                         "decimals": 0}},
            "message_with_figures": "In {months_above} Monaten.",
        }, {"recommendation_id": "plain", "priority": 2, "title": "P", "message": "Ohne Kennzahlen."}],
        "tools": {"enabled": True, "after_import": [{"tool": "analyze_tiers"}]},
    }
    payload = pack_draft_payload(validate_domain_pack_data(data))
    assert payload["recommendations"][0]["figures"] == {
        "months_above": {"tool": "analyze_tiers", "pointer": "/totals/months_above", "decimals": 0}}
    assert payload["recommendations"][0]["message_with_figures"] == "In {months_above} Monaten."
    assert {"figures", "message_with_figures"}.isdisjoint(payload["recommendations"][1])
    assert validate_domain_pack_data(payload).recommendations[0].figures is not None
