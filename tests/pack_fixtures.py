"""Answers for the bundled demo pack verein-demo 1.0.0, in pack order (the
optional question last), shared by the adapter and service tests."""

from guideos.domains.catalog import DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT

DEMO_DOMAIN = "verein-demo"
DEMO_VERSION = "1.0.0"
DEMO_PACK_PATH = DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT / DEMO_DOMAIN / DEMO_VERSION / "pack.json"

PERSONAS = {
    "sportverein_gefoerdert": [
        ("club_type", "sports"),
        ("founded_year", 1962),
        ("members_count", 120),
        ("funding_source", "public_grants"),
        ("volunteer_situation", "tight"),
        ("annual_budget_eur", 28000),
    ],
    "kulturverein_jung": [
        ("club_type", "culture"),
        ("founded_year", 2015),
        ("members_count", 40),
        ("funding_source", "membership_fees"),
        ("volunteer_situation", "sufficient"),
        ("annual_budget_eur", 3000),
    ],
    "hilfsverein_unbekannt": [
        ("club_type", "social"),
        ("founded_year", None),
        ("members_count", 250),
        ("funding_source", "donations"),
        ("volunteer_situation", None),
        ("annual_budget_eur", None),
    ],
    "foerderverein_klein": [
        ("club_type", "other"),
        ("founded_year", 1999),
        ("members_count", 12),
        ("funding_source", "events"),
        ("volunteer_situation", "critical"),
        ("annual_budget_eur", 900),
    ],
}

EXPECTED = {
    "sportverein_gefoerdert": (
        ["GRANTS_DEPENDENT", "VOLUNTEERS_AT_RISK", "OLD_STATUTES", "HIGH_BUDGET_PER_MEMBER"],
        ["volunteer-recruiting", "funding-diversification", "club-consultation"],
    ),
    "kulturverein_jung": ([], []),
    "hilfsverein_unbekannt": (
        ["GRANTS_DEPENDENT", "VOLUNTEERS_AT_RISK", "MISSING_BUDGET"],
        ["collect-budget", "volunteer-recruiting", "funding-diversification"],
    ),
    "foerderverein_klein": (
        ["VOLUNTEERS_AT_RISK", "OLD_STATUTES"],
        ["volunteer-recruiting", "club-consultation"],
    ),
}


# --- A pack with MCP tools for the tool-layer tests -------------------------
# The demo questions plus a gate question, an advisory file question (a cash
# book CSV), a derived answer, a metric rule with figures and completion
# steps. It mirrors what a product pack with tools uses, with generic names.

TOOLS_DOMAIN = "tools-demo"
TOOLS_VERSION = "1.0.0"
TOOLS_FORMATS = ["auto", "csv"]


def tools_pack_data(**overrides) -> dict:
    data = {
        "domain_id": TOOLS_DOMAIN, "version": TOOLS_VERSION, "name": "Vereins-Check mit Kassabuch",
        "description": "Testpack mit Werkzeugen.", "default_locale": "de",
        "advisory_guidance": "Der Kontext ist Österreich.",
        "questions": [
            {"question_id": "dataset_available", "field_key": "dataset_available",
             "text": "Hast du das Kassabuch als Datei?", "field_type": "select", "required": True,
             "options": [{"value": "yes", "label": "Ja"}, {"value": "no", "label": "Nein"},
                         {"value": "unknown", "label": "Weiß nicht"}]},
            {"question_id": "dataset_file", "field_key": "dataset_file",
             "text": "Lade dein Kassabuch als CSV-Datei hoch.", "field_type": "file", "required": True,
             "allow_unknown": True,
             "visible_if": {"field_key": "dataset_available", "operator": "equals", "value": "yes"},
             "upload": {"extensions": ["csv", "txt"], "use_in_advisory": True}},
            {"question_id": "records_kept", "field_key": "records_kept",
             "text": "Wie vollständig ist das Kassabuch?", "field_type": "select", "required": True,
             "options": [{"value": "none", "label": "Gar nicht"}, {"value": "partial", "label": "Lückenhaft"},
                         {"value": "complete", "label": "Vollständig"}]},
            {"question_id": "club_type", "field_key": "club_type", "text": "Welche Art von Verein ist es?",
             "field_type": "select", "required": True,
             "options": [{"value": "sports", "label": "Sportverein"}, {"value": "culture", "label": "Kulturverein"},
                         {"value": "social", "label": "Sozial- oder Hilfsverein"}, {"value": "other", "label": "Anderer Verein"}]},
            {"question_id": "founded_year", "field_key": "founded_year", "text": "Gründungsjahr?",
             "field_type": "integer", "required": True, "allow_unknown": True,
             "validation": {"minimum": 1800, "maximum": 2026}},
            {"question_id": "members_count", "field_key": "members_count", "text": "Wie viele Mitglieder?",
             "field_type": "number", "required": True, "validation": {"minimum": 2, "maximum": 100000}},
            {"question_id": "funding_source", "field_key": "funding_source", "text": "Größte Einnahmequelle?",
             "field_type": "select", "required": True,
             "options": [{"value": "membership_fees", "label": "Mitgliedsbeiträge"},
                         {"value": "public_grants", "label": "Öffentliche Förderungen"},
                         {"value": "donations", "label": "Spenden und Sponsoring"},
                         {"value": "events", "label": "Veranstaltungen und Verkauf"},
                         {"value": "mixed", "label": "Gemischt"}, {"value": "other", "label": "Andere"}]},
            {"question_id": "volunteer_situation", "field_key": "volunteer_situation", "text": "Ehrenamt?",
             "field_type": "select", "required": True, "allow_unknown": True,
             "options": [{"value": "sufficient", "label": "Ausreichend"}, {"value": "tight", "label": "Knapp"},
                         {"value": "critical", "label": "Kritisch"}]},
            {"question_id": "annual_budget_eur", "field_key": "annual_budget_eur", "text": "Jahresausgaben in Euro?",
             "field_type": "number", "required": False, "allow_unknown": True,
             "validation": {"minimum": 0, "maximum": 10000000}},
            {"question_id": "second_file", "field_key": "second_file",
             "text": "Optional: das Kassabuch des Vorjahres.", "field_type": "file", "required": False,
             "visible_if": {"field_key": "dataset_available", "operator": "equals", "value": "yes"},
             "upload": {"arguments": {"purpose": "previous_year"}, "extensions": ["csv"], "use_in_advisory": False}},
        ],
        "rules": [
            {"rule_id": "grants-dependent", "field_key": "funding_source", "operator": "in",
             "value": ["public_grants", "donations"], "reason_code": "GRANTS_DEPENDENT",
             "recommendation_ids": ["funding-diversification"]},
            {"rule_id": "volunteers-at-risk", "field_key": "volunteer_situation", "operator": "in",
             "value": ["tight", "critical", None], "reason_code": "VOLUNTEERS_AT_RISK",
             "recommendation_ids": ["volunteer-recruiting"]},
            {"rule_id": "old-statutes", "field_key": "founded_year", "operator": "lt", "value": 2002,
             "reason_code": "OLD_STATUTES", "recommendation_ids": ["club-consultation"]},
            {"rule_id": "missing-budget", "field_key": "annual_budget_eur", "operator": "is_missing",
             "reason_code": "MISSING_BUDGET", "recommendation_ids": ["collect-budget"]},
            {"rule_id": "incomplete-records", "field_key": "records_kept", "operator": "in",
             "value": ["none", "partial"], "reason_code": "INCOMPLETE_RECORDS", "recommendation_ids": ["collect-budget"]},
            {"rule_id": "months-above-threshold", "field_key": "dataset_file", "operator": "gte", "value": 1,
             "metric": {"tool": "analyze_tiers", "pointer": "/totals/months_above",
                        "label": "Monate über der Ausgabenschwelle"},
             "reason_code": "MONTHS_ABOVE_THRESHOLD", "recommendation_ids": ["review-spending"],
             "source_reference": "Demo-Annahme"},
        ],
        "recommendations": [
            {"recommendation_id": "collect-budget", "priority": 1, "title": "Finanzdaten erfassen",
             "message": "Erfasse die jährlichen Ausgaben."},
            {"recommendation_id": "volunteer-recruiting", "priority": 2, "title": "Ehrenamt sichern",
             "message": "Plane die Nachbesetzung."},
            {"recommendation_id": "funding-diversification", "priority": 3, "title": "Einnahmen breiter aufstellen",
             "message": "Prüfe weitere Einnahmequellen."},
            {"recommendation_id": "club-consultation", "priority": 4, "title": "Vereinsberatung einholen",
             "message": "Eine Vereinsservicestelle kann die nächsten Schritte bewerten."},
            {"recommendation_id": "review-spending", "priority": 5, "title": "Ausgabenspitzen prüfen",
             "message": "In einigen Monaten lagen die Ausgaben über der Schwelle.",
             "figures": {"months": {"tool": "analyze_tiers", "pointer": "/totals/months_above", "decimals": 0}},
             "message_with_figures": "In {months} Monaten lagen die Ausgaben über der Schwelle."},
        ],
        "tools": {
            "enabled": True, "dataset_tool": "import_dataset", "delete_tool": "delete_dataset",
            "series_tool": "get_series", "max_calls_per_message": 4, "max_seconds_per_message": 60,
            "after_import": [
                {"tool": "analyze_tiers", "arguments": {"threshold": 10, "minimum": 2}, "note": "Demo-Schwelle."},
                {"tool": "analyze_windows", "note": "Monatsfenster."},
                {"tool": "analyze_characteristics", "note": "Merkmale des Kassabuchs."},
            ],
            "at_completion": [
                {"tool": "calculate_costs", "arguments": {"comparison": "standard"},
                 "arguments_from_answers": {"model": {"field_key": "club_type",
                                                      "map": {"sports": "sports_default", "culture": "culture_default"},
                                                      "default": "generic"}},
                 "note": "Richtwerte dieser Demo."},
            ],
            "derive": [
                {"field_key": "records_kept", "tool": "import_dataset", "value": "complete"},
                {"field_key": "annual_budget_eur", "tool": "analyze_characteristics",
                 "pointer": "/characteristics/totals/annual_eur", "decimals": 0},
            ],
        },
    }
    data.update(overrides)
    return data


def tools_pack(**overrides):
    from guideos.domains.loader import validate_domain_pack_data
    return validate_domain_pack_data(tools_pack_data(**overrides))


# Answers for the tools pack without a file (gate "no"), in question order.
TOOLS_PACK_ANSWERS = [("dataset_available", "no"), ("records_kept", "partial"), *PERSONAS["sportverein_gefoerdert"]]


def install_tools_pack(tmp_path, monkeypatch, **overrides):
    """Make the tools pack the active version of its domain for app.py and
    the REST adapter: saved as a local draft in a temporary catalog that
    replaces the environment's catalog store, then activated."""
    from guideos.domains import catalog
    from guideos.domains.catalog import LocalCatalogStore, activate_domain_version, save_domain_pack_draft

    store = LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json")
    monkeypatch.setattr(catalog, "catalog_store_from_env", lambda **kwargs: store)
    pack = tools_pack(**overrides)
    save_domain_pack_draft(pack, store=store)
    return activate_domain_version(pack.domain_id, pack.version, store=store)
