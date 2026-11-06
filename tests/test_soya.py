import json

import pytest

from guideos.domains.catalog import (
    DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    load_domain_pack_version,
    save_domain_pack_draft_data,
)
from guideos.domains.loader import load_domain_pack, validate_domain_pack_data
from guideos.domains.soya import SoyaError, pack_from_soya, pack_to_soya, soya_to_questions


def _bundled(domain_id, version):
    return load_domain_pack(
        DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT / domain_id / version / "pack.json"
    )


def _overlay(document, overlay_type):
    return next(o for o in document["content"]["overlays"] if o["type"] == overlay_type)


def _control(document, field_key):
    form = _overlay(document, "OverlayForm")
    return next(
        e for e in form["ui"]["elements"] if e["scope"] == f"#/properties/{field_key}"
    )


def _all_types_pack():
    return validate_domain_pack_data(
        {
            "domain_id": "all-types",
            "version": "1.0.0",
            "name": "Alle Typen",
            "description": "Demo",
            "default_locale": "de",
            "questions": [
                {"question_id": "q_form", "field_key": "legal_form", "text": "Rechtsform?",
                 "help_text": "Hilfe", "field_type": "select", "widget": "radio", "required": True,
                 "options": [{"value": "gmbh", "label": "GmbH"}, {"value": "og", "label": "OG"}]},
                {"question_id": "channels", "field_key": "channels", "text": "Kanäle?",
                 "field_type": "multiselect", "widget": "dropdown", "required": False,
                 "options": [{"value": "email", "label": "E-Mail"}, {"value": "web", "label": "Web"}],
                 "validation": {"min_count": 1, "max_count": 2}},
                {"question_id": "employees", "field_key": "employees", "text": "Beschäftigte?",
                 "field_type": "integer", "required": True, "allow_unknown": True,
                 "validation": {"minimum": 0, "maximum": 500}},
                {"question_id": "share", "field_key": "share", "text": "Anteil?",
                 "field_type": "number", "required": True, "validation": {"minimum": 0.5}},
                {"question_id": "summary", "field_key": "summary", "text": "Beschreibung?",
                 "field_type": "text", "widget": "textarea", "required": False,
                 "validation": {"min_length": 2, "max_length": 500, "pattern": "^[^<>]*$"}},
                {"question_id": "has_web", "field_key": "has_web", "text": "Website?",
                 "field_type": "boolean", "required": True},
                {"question_id": "url", "field_key": "url", "text": "Adresse?",
                 "field_type": "text", "required": True,
                 "visible_if": {"field_key": "has_web", "operator": "equals", "value": True}},
                {"question_id": "founded", "field_key": "founded", "text": "Gründung?",
                 "field_type": "date", "required": True,
                 "validation": {"min_date": "1900-01-01", "max_date": "2026-12-31"}},
                {"question_id": "online", "field_key": "online", "text": "Online-Anteil?",
                 "field_type": "number", "required": True,
                 "visible_if": {"field_key": "channels", "operator": "contains", "value": "web"}},
                {"question_id": "tools", "field_key": "tools", "text": "Werkzeuge?",
                 "field_type": "text", "required": False,
                 "visible_if": {"field_key": "channels", "operator": "contains_any",
                                "value": ["email", "web"]}},
                {"question_id": "why", "field_key": "why", "text": "Warum unbekannt?",
                 "field_type": "text", "required": False,
                 "visible_if": {"field_key": "employees", "operator": "is_missing"}},
                {"question_id": "which", "field_key": "which", "text": "Welche?",
                 "field_type": "text", "required": False,
                 "visible_if": {"field_key": "legal_form", "operator": "in",
                                "value": ["gmbh", "og"]}},
            ],
            "rules": [],
            "recommendations": [],
        }
    )


PERSON_DOCUMENT = {
    "meta": {"name": "Person"},
    "content": {
        "bases": [
            {"name": "Person",
             "attributes": {"name": "String", "dateOfBirth": "Date", "age": "Integer", "sex": "String"}}
        ],
        "overlays": [
            {"type": "OverlayValidation", "base": "Person", "name": "PersonValidationOverlay",
             "attributes": {
                 "name": {"cardinality": "1..1", "length": "[0..20)", "pattern": "^[A-Za-z ,.'-]+$"},
                 "dateOfBirth": {"cardinality": "1..1", "valueRange": "[1900-01-01..*]"},
                 "age": {"cardinality": "0..1", "valueRange": "[0..*]"},
                 "sex": {"cardinality": "0..1", "valueOption": ["male", "female", "other"]},
             }},
            {"type": "OverlayAnnotation", "base": "Person", "name": "PersonAnnotationOverlay",
             "class": {"label": {"en": "Person", "de": ["die Person", "der Mensch"]}},
             "attributes": {
                 "name": {"label": {"en": "Name", "de": "Name"}},
                 "dateOfBirth": {"label": {"en": "Date of Birth", "de": "Geburtsdatum"},
                                 "comment": {"en": "Birthdate of Person"}},
                 "sex": {"label": {"en": "Gender", "de": "Geschlecht"}},
             }},
        ],
    },
}


def test_pack_to_soya_exports_one_base_with_type_mapping():
    document = pack_to_soya(_all_types_pack())

    assert document["meta"]["name"] == "AllTypes"
    bases = document["content"]["bases"]
    assert len(bases) == 1 and bases[0]["name"] == "AllTypes"
    assert bases[0]["attributes"] == {
        "legal_form": "String", "channels": "String", "employees": "Integer", "share": "Float",
        "summary": "String", "has_web": "Boolean", "url": "String", "founded": "Date",
        "online": "Float", "tools": "String", "why": "String", "which": "String",
    }
    assert [o["type"] for o in document["content"]["overlays"]] == [
        "OverlayValidation", "OverlayForm", "OverlayAnnotation"
    ]
    assert all(o["base"] == "AllTypes" for o in document["content"]["overlays"])


def test_pack_to_soya_validation_overlay_encodes_cardinality_ranges_and_options():
    energy = _overlay(pack_to_soya(load_domain_pack()), "OverlayValidation")
    assert energy["attributes"]["club_type"]["cardinality"] == "1..1"
    assert energy["attributes"]["club_type"]["valueOption"] == [
        "sports", "culture", "social", "other"
    ]
    assert energy["attributes"]["founded_year"] == {
        "cardinality": "0..1", "valueRange": "[1800..2026]"
    }
    assert energy["attributes"]["annual_budget_eur"]["cardinality"] == "0..1"

    typed = _overlay(pack_to_soya(_all_types_pack()), "OverlayValidation")["attributes"]
    assert typed["channels"] == {"cardinality": "0..*", "valueOption": ["email", "web"]}
    assert typed["share"] == {"cardinality": "1..1", "valueRange": "[0.5..*]"}
    assert typed["summary"] == {
        "cardinality": "0..1", "length": "[2..500]", "pattern": "^[^<>]*$"
    }
    assert typed["founded"] == {"cardinality": "1..1", "valueRange": "[1900-01-01..2026-12-31]"}
    assert typed["has_web"] == {"cardinality": "1..1"}


def test_pack_to_soya_form_overlay_has_labels_required_and_show_rules():
    document = pack_to_soya(_all_types_pack())
    form = _overlay(document, "OverlayForm")
    schema = form["schema"]

    assert schema["type"] == "object"
    assert schema["properties"]["legal_form"] == {
        "type": "string", "oneOf": [{"const": "gmbh", "title": "GmbH"}, {"const": "og", "title": "OG"}]
    }
    assert schema["properties"]["channels"] == {
        "type": "array", "uniqueItems": True, "minItems": 1, "maxItems": 2,
        "items": {"oneOf": [{"const": "email", "title": "E-Mail"}, {"const": "web", "title": "Web"}]},
    }
    assert schema["properties"]["employees"] == {"type": "integer", "minimum": 0, "maximum": 500}
    assert schema["properties"]["summary"] == {
        "type": "string", "minLength": 2, "maxLength": 500, "pattern": "^[^<>]*$"
    }
    assert schema["properties"]["has_web"] == {"type": "boolean"}
    assert schema["properties"]["founded"] == {"type": "string", "format": "date"}
    assert schema["required"] == ["legal_form", "share", "has_web", "url", "founded", "online"]
    assert form["ui"]["type"] == "VerticalLayout"
    assert _control(document, "legal_form")["options"]["format"] == "radio"
    assert _control(document, "summary")["options"]["multi"] is True
    assert _control(document, "url")["rule"] == {
        "effect": "SHOW",
        "condition": {"scope": "#/properties/has_web", "schema": {"const": True}},
    }
    assert _control(document, "online")["rule"]["condition"]["schema"] == {
        "contains": {"const": "web"}
    }
    assert _control(document, "tools")["rule"]["condition"]["schema"] == {
        "contains": {"enum": ["email", "web"]}
    }
    assert _control(document, "which")["rule"]["condition"]["schema"] == {"enum": ["gmbh", "og"]}
    assert "rule" not in _control(document, "why")


def test_pack_to_soya_annotation_overlay_uses_default_locale():
    document = pack_to_soya(_all_types_pack())
    annotation = _overlay(document, "OverlayAnnotation")

    assert annotation["class"]["label"] == {"de": "Alle Typen"}
    assert annotation["attributes"]["legal_form"] == {
        "label": {"de": "Rechtsform?"}, "comment": {"de": "Hilfe"}
    }
    assert annotation["attributes"]["channels"] == {"label": {"de": "Kanäle?"}}


def test_pack_to_soya_keeps_guideos_specifics_in_control_options():
    document = pack_to_soya(_all_types_pack())

    assert _control(document, "employees")["options"]["guideos"] == {
        "question_id": "employees", "required": True, "allow_unknown": True
    }
    assert _control(document, "legal_form")["options"]["guideos"] == {
        "question_id": "q_form", "required": True, "allow_unknown": False, "widget": "radio"
    }
    assert _control(document, "channels")["options"]["guideos"]["widget"] == "dropdown"
    assert _control(document, "why")["options"]["guideos"]["visible_if"] == {
        "field_key": "employees", "operator": "is_missing"
    }


@pytest.mark.parametrize(
    "pack",
    [
        load_domain_pack(),
        _all_types_pack(),
    ],
    ids=["demo", "all-types"],
)
def test_soya_round_trip_keeps_questions(pack):
    document = json.loads(json.dumps(pack_to_soya(pack), ensure_ascii=False))

    assert soya_to_questions(document, locale=pack.default_locale) == pack.questions


def test_soya_to_questions_without_overlays_uses_defaults():
    document = {
        "meta": {"name": "Minimal"},
        "content": {"bases": [{"name": "Minimal", "attributes": {"count": "Integer", "note": "String"}}]},
    }

    questions = soya_to_questions(document)

    assert [(q.question_id, q.field_type, q.text, q.required, q.widget) for q in questions] == [
        ("count", "integer", "count", False, None),
        ("note", "text", "note", False, None),
    ]


def test_soya_to_questions_imports_spec_person_example():
    questions = {q.field_key: q for q in soya_to_questions(PERSON_DOCUMENT, locale="de")}

    assert list(questions) == ["name", "dateOfBirth", "age", "sex"]
    name = questions["name"]
    assert name.field_type == "text" and name.required is True
    assert name.validation.model_dump(exclude_none=True) == {
        "min_length": 0, "max_length": 19, "pattern": "^[A-Za-z ,.'-]+$"
    }
    birth = questions["dateOfBirth"]
    assert birth.field_type == "date" and birth.text == "Geburtsdatum"
    assert birth.help_text == "Birthdate of Person"
    assert birth.validation.min_date == "1900-01-01" and birth.validation.max_date is None
    age = questions["age"]
    assert age.field_type == "integer" and age.required is False and age.validation.minimum == 0
    sex = questions["sex"]
    assert sex.field_type == "select" and sex.text == "Geschlecht"
    assert [(o.value, o.label) for o in sex.options] == [
        ("male", "male"), ("female", "female"), ("other", "other")
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["content"].__setitem__("bases", []), "keine Basis"),
        (lambda d: d["content"]["bases"].append({"name": "B", "attributes": {}}), "Mehrere Basen"),
        (lambda d: d["content"]["bases"][0]["attributes"].__setitem__("when", "DateTime"),
         "DateTime"),
        (lambda d: d["content"]["overlays"].append(
            {"type": "OverlayForm", "base": "Person", "schema": {"type": "object", "properties": {}},
             "ui": {"type": "VerticalLayout", "elements": [
                 {"type": "Control", "scope": "#/properties/age",
                  "rule": {"effect": "HIDE", "condition": {"scope": "#/properties/sex",
                                                           "schema": {"const": "male"}}}}]}}),
         "SHOW"),
    ],
)
def test_soya_to_questions_rejects_unsupported_structures_in_german(mutate, message):
    document = json.loads(json.dumps(PERSON_DOCUMENT))
    mutate(document)

    with pytest.raises(SoyaError, match=message):
        soya_to_questions(document)


def test_pack_from_soya_keeps_rules_for_existing_fields_and_warns_for_dropped():
    base = load_domain_pack()
    document = pack_to_soya(base)
    del document["content"]["bases"][0]["attributes"]["funding_source"]
    for overlay in document["content"]["overlays"]:
        overlay.get("attributes", {}).pop("funding_source", None)
    form = _overlay(document, "OverlayForm")
    form["schema"]["properties"].pop("funding_source")
    form["ui"]["elements"] = [
        e for e in form["ui"]["elements"] if e["scope"] != "#/properties/funding_source"
    ]

    pack, warnings = pack_from_soya(
        document, domain_id="verein-demo", version="1.3.0", name=base.name,
        description=base.description, base_pack=base,
    )

    assert [q.field_key for q in pack.questions] == [
        "club_type", "founded_year", "members_count", "volunteer_situation",
        "annual_budget_eur",
    ]
    assert "grants-dependent" not in {rule.rule_id for rule in pack.rules}
    assert len(pack.rules) == len(base.rules) - 1
    assert pack.recommendations == base.recommendations
    assert warnings == ["Regel grants-dependent wurde verworfen: Feld funding_source existiert nicht mehr."]
    assert pack.version == "1.3.0"


def test_pack_from_soya_result_saves_and_reloads_as_draft(tmp_path):
    pack, warnings = pack_from_soya(
        PERSON_DOCUMENT, domain_id="person-demo", version="1.0.0", name="Person",
        description="Aus SOyA importiert",
    )

    assert warnings == []
    local_root = tmp_path / "packs"
    save_domain_pack_draft_data(
        pack.model_dump(mode="json", exclude={"content_hash"}), local_root=local_root
    )
    loaded = load_domain_pack_version(
        "person-demo", "1.0.0", local_root=local_root, catalog_path=tmp_path / "catalog.json"
    )
    assert [q.field_key for q in loaded.questions] == ["name", "dateOfBirth", "age", "sex"]


def test_soya_document_is_json_serialisable_with_umlauts():
    text = json.dumps(pack_to_soya(load_domain_pack()), ensure_ascii=False)

    assert "gegründet" in text
    assert json.loads(text)["content"]["bases"][0]["name"] == "VereinDemo"


def _file_pack():
    return validate_domain_pack_data({
        "domain_id": "file-demo", "version": "1.0.0", "name": "Datei", "description": "Demo",
        "default_locale": "de",
        "questions": [
            {"question_id": "pv", "field_key": "pv", "text": "PV?", "field_type": "boolean", "required": True},
            {"question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
             "help_text": "CSV aus dem Portal", "field_type": "file", "required": False, "upload": {}},
            {"question_id": "feed_in", "field_key": "feed_in", "text": "Zweite Datei?",
             "field_type": "file", "required": False,
             "visible_if": {"field_key": "pv", "operator": "equals", "value": True},
             "upload": {"tool": "import_dataset", "arguments": {"direction": "export"},
                        "extensions": ["csv"], "max_bytes": 1024, "use_in_advisory": False}},
        ],
        "rules": [], "recommendations": [], "tools": {"enabled": False},
    })


def test_pack_to_soya_exports_file_questions_as_strings_with_guideos_upload_block():
    pack = _file_pack()

    document = pack_to_soya(pack)

    base = document["content"]["bases"][0]
    assert base["attributes"]["dataset_file"] == "String"
    guideos = _control(document, "feed_in")["options"]["guideos"]
    assert guideos["field_type"] == "file"
    assert guideos["upload"] == {"tool": "import_dataset", "arguments": {"direction": "export"},
                                 "extensions": ["csv"], "max_bytes": 1024, "use_in_advisory": False}
    assert _control(document, "dataset_file")["options"]["guideos"]["upload"]["use_in_advisory"] is True
    assert "upload" not in _control(document, "pv")["options"]["guideos"]

    round_trip = json.loads(json.dumps(document, ensure_ascii=False))
    assert soya_to_questions(round_trip, locale="de") == pack.questions
