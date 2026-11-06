"""Rule lifecycle (concept step 5): origin, source reference and status on
rules, an activation guard, and the Domain-Admin rule editor."""

import json

import pytest

from guideos.adapters.question_editor import (
    EditorError,
    delete_recommendation,
    delete_rule,
    new_recommendation,
    new_rule,
    pack_with_recommendations,
    pack_with_rules,
    recommendation_from_form,
    pack_with_topics,
    rule_from_form,
    set_rule_status,
    topics_from_text,
    topics_to_text,
    unapproved_rules,
)
from guideos.domains.catalog import (
    DomainCatalogError,
    activate_domain_version,
    pack_draft_payload,
    save_domain_pack_draft_data,
)
from guideos.domains.loader import load_domain_pack, validate_domain_pack_data
from guideos.domains.models import TopicDefinition


def _pack_data(**overrides):
    data = {
        "domain_id": "lifecycle-demo", "version": "1.0.0", "name": "Lebenszyklus", "description": "Test",
        "default_locale": "de",
        "questions": [
            {"question_id": "heating", "field_key": "heating", "text": "Heizung?", "field_type": "select",
             "required": True, "options": [{"value": "public_grants", "label": "Öffentliche Förderungen"}, {"value": "donations", "label": "Spenden und Sponsoring"}]},
            {"question_id": "year", "field_key": "year", "text": "Baujahr?", "field_type": "integer",
             "required": True},
        ],
        "rules": [
            {"rule_id": "fossil", "field_key": "heating", "operator": "in", "value": ["public_grants", "donations"],
             "reason_code": "FOSSIL", "recommendation_ids": ["modernize"],
             "origin": "expert", "source_reference": "Richtlinie, Abschnitt 3", "status": "review"},
        ],
        "recommendations": [
            {"recommendation_id": "modernize", "priority": 1, "title": "Heizung modernisieren", "message": "Prüfen."},
        ],
    }
    data.update(overrides)
    return data


def test_rules_carry_origin_source_and_status_with_implicit_defaults():
    pack = validate_domain_pack_data(_pack_data())
    rule = pack.rules[0]

    assert (rule.origin, rule.source_reference, rule.status) == ("expert", "Richtlinie, Abschnitt 3", "review")
    payload_rule = pack_draft_payload(pack)["rules"][0]
    assert "origin" not in payload_rule and payload_rule["status"] == "review"
    assert payload_rule["source_reference"] == "Richtlinie, Abschnitt 3"
    bundled = load_domain_pack()
    assert all(rule.status == "approved" and rule.origin == "expert" for rule in bundled.rules)
    assert all({"origin", "status"}.isdisjoint(rule) for rule in pack_draft_payload(bundled)["rules"])
    without_source = _pack_data()
    del without_source["rules"][0]["source_reference"]
    assert "source_reference" not in pack_draft_payload(validate_domain_pack_data(without_source))["rules"][0]
    with pytest.raises(Exception):
        validate_domain_pack_data(_pack_data(rules=[{**_pack_data()["rules"][0], "status": "live"}]))


def test_activation_refuses_versions_with_rules_that_are_not_approved(tmp_path):
    local_root = tmp_path / "domain_packs"
    catalog_path = tmp_path / "domain_catalog.json"
    save_domain_pack_draft_data(_pack_data(), local_root=local_root)

    with pytest.raises(DomainCatalogError, match="nicht freigegeben.*fossil"):
        activate_domain_version("lifecycle-demo", "1.0.0", local_root=local_root, catalog_path=catalog_path)

    approved = _pack_data()
    approved["rules"][0]["status"] = "approved"
    save_domain_pack_draft_data(approved, local_root=local_root)
    pack = activate_domain_version("lifecycle-demo", "1.0.0", local_root=local_root, catalog_path=catalog_path)
    assert pack.rules[0].status == "approved"
    assert unapproved_rules(validate_domain_pack_data(_pack_data())) == ["fossil"]
    assert unapproved_rules(pack) == []


def test_set_rule_status_follows_the_review_order():
    pack = validate_domain_pack_data(_pack_data())
    rule = pack.rules[0]  # review

    approved = set_rule_status(rule, "approved")
    back = set_rule_status(approved, "draft")
    reviewed = set_rule_status(back, "review")

    assert (approved.status, back.status, reviewed.status) == ("approved", "draft", "review")
    assert rule.status == "review"  # unchanged input
    with pytest.raises(EditorError, match="Prüfung"):
        set_rule_status(back, "approved")  # draft -> approved skips the review


def _form(**overrides):
    values = {
        "rule_id": "old-house", "kind": "recommend", "field_key": "year", "operator": "lt", "value": "1980",
        "divisor_field_key": "", "reason_code": "OLD_HOUSE", "recommendation_ids": ["modernize"],
        "topics": [], "priority": "", "origin": "expert", "source_reference": "", "status": "draft",
    }
    values.update(overrides)
    return values


def test_rule_from_form_parses_values_by_field_type_and_validates_against_the_pack():
    pack = validate_domain_pack_data(_pack_data())

    rule = rule_from_form(_form(), pack, index=None)

    assert rule.value == 1980 and rule.operator == "lt" and rule.status == "draft"
    assert rule.recommendation_ids == ["modernize"] and rule.source_reference is None
    listed = rule_from_form(_form(field_key="heating", operator="in", value="public_grants, donations"), pack, index=None)
    assert listed.value == ["public_grants", "donations"]
    missing = rule_from_form(_form(field_key="year", operator="is_missing", value=""), pack, index=None)
    assert "value" not in missing.model_fields_set
    replaced = rule_from_form(_form(rule_id="fossil"), pack, index=0)
    assert replaced.rule_id == "fossil"


@pytest.mark.parametrize("overrides, message", [
    ({"rule_id": " "}, "Regel-ID"),
    ({"reason_code": ""}, "Reason Code"),
    ({"rule_id": "fossil"}, "bereits"),
    ({"value": "abc"}, "Zahl"),
    ({"recommendation_ids": ["nope"]}, "unbekannte Empfehlungen"),
    ({"kind": "exclude", "recommendation_ids": [], "topics": []}, "mindestens ein Thema"),
    ({"kind": "prioritize", "recommendation_ids": [], "topics": ["x"], "priority": ""}, "priority"),
    ({"priority": "0", "kind": "prioritize", "recommendation_ids": [], "topics": ["x"]}, "ganze Zahl"),
])
def test_rule_from_form_reports_problems_in_german(overrides, message):
    pack = validate_domain_pack_data(_pack_data(topics=[{"topic_id": "x", "label": "X"}]))

    with pytest.raises(EditorError, match=message):
        rule_from_form(_form(**overrides), pack, index=None)


def _tools_pack():
    """A pack with a metric rule, as the loader and as raw JSON data."""
    from tests.pack_fixtures import tools_pack, tools_pack_data

    return tools_pack(), json.loads(json.dumps(tools_pack_data()))


def _metric_form(rule, **overrides):
    """The form values as _render_rule_form collects them for a stored rule."""
    values = _form(
        rule_id=rule.rule_id, kind=rule.kind, field_key=rule.field_key, operator=rule.operator,
        value="" if rule.value is None else str(rule.value), reason_code=rule.reason_code,
        recommendation_ids=list(rule.recommendation_ids), source_reference=rule.source_reference or "",
        status=rule.status, metric_tool=rule.metric.tool if rule.metric else "",
        metric_pointer=rule.metric.pointer if rule.metric else "",
        metric_label=rule.metric.label if rule.metric else "",
    )
    values.update(overrides)
    return values


def test_rule_from_form_round_trips_a_metric_rule():
    pack, raw = _tools_pack()
    index, original = next((i, r) for i, r in enumerate(pack.rules) if r.rule_id == "months-above-threshold")

    rule = rule_from_form(_metric_form(original), pack, index=index)

    assert rule.metric == original.metric
    assert rule.value == 1 and type(rule.value) is int
    assert rule == original
    payload_rule = pack_draft_payload(pack_with_rules(pack, [*pack.rules[:index], rule, *pack.rules[index + 1:]]))
    assert payload_rule["rules"][index]["metric"] == raw["rules"][index]["metric"]
    decimal = rule_from_form(_metric_form(original, value="1,5"), pack, index=index)
    assert decimal.value == 1.5
    listed = rule_from_form(_metric_form(original, operator="in", value="1, 2"), pack, index=index)
    assert listed.value == [1, 2]


@pytest.mark.parametrize("overrides, message", [
    ({"metric_pointer": "totals/months_above"}, "Kennzahl ist ungültig"),
    ({"metric_pointer": ""}, "Werkzeug und JSON-Pointer"),
    ({"value": "abc"}, "Kennzahl muss eine Zahl"),
    ({"field_key": "club_type", "value": "1"}, "Datei-Frage"),
    ({"divisor_field_key": "annual_budget_eur"}, "divisor_field_key"),
    ({"metric_tool": "unbekannt"}, "tools.after_import"),
])
def test_rule_from_form_reports_metric_problems_in_german(overrides, message):
    pack, _ = _tools_pack()
    index, original = next((i, r) for i, r in enumerate(pack.rules) if r.rule_id == "months-above-threshold")

    with pytest.raises(EditorError, match=message):
        rule_from_form(_metric_form(original, **overrides), pack, index=index)


def test_new_delete_and_pack_helpers_for_rules_and_topics():
    pack = validate_domain_pack_data(_pack_data())

    created = new_rule(pack)
    assert created.rule_id not in {r.rule_id for r in pack.rules} and created.status == "draft"
    assert created.field_key == "heating"
    with_rule = pack_with_rules(pack, [*pack.rules, created])
    assert [r.rule_id for r in with_rule.rules] == ["fossil", created.rule_id]
    assert pack_with_rules(pack, delete_rule(with_rule.rules, 1)).rules == pack.rules

    topics = topics_from_text("heating_system: Heizsystem\n\nenvelope : Gebäudehülle")
    assert [(t.topic_id, t.label) for t in topics] == [("heating_system", "Heizsystem"), ("envelope", "Gebäudehülle")]
    assert topics_to_text(topics) == "heating_system: Heizsystem\nenvelope: Gebäudehülle"
    assert topics_from_text("") == [] and pack_with_topics(pack, []).topics is None
    assert pack_with_topics(pack, topics).topics == [TopicDefinition(topic_id="heating_system", label="Heizsystem")] + [topics[1]]
    with pytest.raises(EditorError, match="topic_id"):
        topics_from_text("Heizung ohne Doppelpunkt")
    with pytest.raises(EditorError, match="doppelt"):
        topics_from_text("a: A\na: B")


# --- recommendation catalog ---------------------------------------------------


def _recommendation_form(**overrides):
    values = {
        "recommendation_id": "insulate", "priority": "2", "title": "Dämmung prüfen",
        "message": "Lass die Gebäudehülle prüfen.", "source_reference": "",
    }
    values.update(overrides)
    return values


def test_recommendation_from_form_validates_and_replaces():
    pack = validate_domain_pack_data(_pack_data())

    created = recommendation_from_form(_recommendation_form(), pack, index=None)
    assert (created.recommendation_id, created.priority, created.title) == ("insulate", 2, "Dämmung prüfen")
    assert created.message == "Lass die Gebäudehülle prüfen." and created.source_reference is None

    replaced = recommendation_from_form(
        _recommendation_form(recommendation_id="modernize", priority="3", source_reference=" Leitfaden "),
        pack, index=0,
    )
    assert replaced.recommendation_id == "modernize" and replaced.priority == 3
    assert replaced.source_reference == "Leitfaden"
    with_both = pack_with_recommendations(pack, [replaced, created])
    assert [r.recommendation_id for r in with_both.recommendations] == ["modernize", "insulate"]
    assert with_both.rules == pack.rules


@pytest.mark.parametrize("overrides, message", [
    ({"recommendation_id": " "}, "Empfehlungs-ID"),
    ({"recommendation_id": "modernize"}, "bereits"),
    ({"priority": "0"}, "ganze Zahl ab 1"),
    ({"priority": "abc"}, "ganze Zahl ab 1"),
    ({"title": ""}, "Titel"),
    ({"message": "  "}, "Text der Empfehlung"),
])
def test_recommendation_from_form_reports_problems_in_german(overrides, message):
    pack = validate_domain_pack_data(_pack_data())

    with pytest.raises(EditorError, match=message):
        recommendation_from_form(_recommendation_form(**overrides), pack, index=None)


def test_renaming_a_referenced_recommendation_surfaces_the_loader_message():
    pack = validate_domain_pack_data(_pack_data())

    with pytest.raises(EditorError, match="unbekannte Empfehlungen"):
        recommendation_from_form(_recommendation_form(recommendation_id="renamed"), pack, index=0)


def test_delete_recommendation_refuses_referenced_ones():
    pack = validate_domain_pack_data(_pack_data())
    created = new_recommendation(pack)
    assert created.recommendation_id not in {r.recommendation_id for r in pack.recommendations}
    assert created.priority >= 1 and created.title and created.message
    extended = pack_with_recommendations(pack, [*pack.recommendations, created])

    with pytest.raises(EditorError, match="modernize.*fossil"):
        delete_recommendation(extended, 0)
    assert delete_recommendation(extended, 1) == pack.recommendations
    assert pack_with_recommendations(extended, delete_recommendation(extended, 1)) == pack


# --- AppTest: Domain-Admin rule editor -------------------------------------

from streamlit.testing.v1 import AppTest  # noqa: E402

from guideos.domains.catalog import LocalCatalogStore, list_domain_versions, load_domain_pack_version  # noqa: E402


@pytest.fixture
def admin_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ADMIN_ENABLED", "1")
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Domain-Admin").run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.0.0")
    next(item for item in app.selectbox if item.label == "Domain-Version").set_value(ref).run()
    assert not app.error
    return app


def _button(app, label):
    return next(item for item in app.button if item.label == label)


def test_admin_rule_editor_changes_status_and_guards_activation(admin_app, tmp_path):
    select = next(item for item in admin_app.selectbox if item.label == "Regel bearbeiten")
    select.set_value(0).run()
    assert any(item.label == "Status" for item in admin_app.selectbox)

    _button(admin_app, "Zurück auf Entwurf").click().run()
    _button(admin_app, "Regeln als Draft speichern").click().run()

    assert not [item.value for item in admin_app.error]
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert draft.rules[0].status == "draft" and draft.rules[1].status == "approved"
    with pytest.raises(DomainCatalogError, match="nicht freigegeben"):
        activate_domain_version("verein-demo", "1.0.1")

    _button(admin_app, "Zur Prüfung geben").click().run()
    _button(admin_app, "Freigeben").click().run()
    _button(admin_app, "Regeln als Draft speichern").click().run()

    assert load_domain_pack_version("verein-demo", "1.0.1").rules[0].status == "approved"
    assert activate_domain_version("verein-demo", "1.0.1").version == "1.0.1"


def test_admin_rule_form_and_topics_are_saved_to_the_draft(admin_app):
    select = next(item for item in admin_app.selectbox if item.label == "Regel bearbeiten")
    select.set_value(0).run()
    next(item for item in admin_app.text_input if item.label == "Fachquelle").set_value("klimaaktiv Leitfaden, S. 4")
    _button(admin_app, "Regel übernehmen").click().run()
    next(item for item in admin_app.text_area if item.label.startswith("Themen")).set_value("heating_system: Heizsystem")
    _button(admin_app, "Themen übernehmen").click().run()

    _button(admin_app, "Regeln als Draft speichern").click().run()

    assert not [item.value for item in admin_app.error]
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert draft.rules[0].source_reference == "klimaaktiv Leitfaden, S. 4"
    assert [t.topic_id for t in draft.topics] == ["heating_system"]


def test_admin_rule_form_keeps_the_metric_of_a_rule(admin_app, tmp_path):
    from guideos.domains.catalog import save_domain_pack_draft

    pack, raw = _tools_pack()
    save_domain_pack_draft(pack, local_root=tmp_path / "packs")
    admin_app.run()
    ref = next(r for r in list_domain_versions() if r.domain_id == pack.domain_id and r.version == pack.version)
    next(item for item in admin_app.selectbox if item.label == "Domain-Version").set_value(ref).run()
    index = next(i for i, r in enumerate(pack.rules) if r.rule_id == "months-above-threshold")
    next(item for item in admin_app.selectbox if item.label == "Regel bearbeiten").set_value(index).run()
    assert next(item for item in admin_app.text_input if item.label == "Werkzeug (Kennzahl)").value == "analyze_tiers"

    _button(admin_app, "Regel übernehmen").click().run()
    _button(admin_app, "Regeln als Draft speichern").click().run()

    assert not [item.value for item in admin_app.error]
    draft = load_domain_pack_version(pack.domain_id, "1.0.1")
    assert draft.rules[index] == pack.rules[index]
    assert pack_draft_payload(draft)["rules"][index]["metric"] == raw["rules"][index]["metric"]


def _recommendation_select(app):
    return next(item for item in app.selectbox if item.label == "Empfehlung bearbeiten")


def test_admin_recommendation_form_is_saved_and_offered_to_rules(admin_app):
    before = len(load_domain_pack_version("verein-demo", "1.0.0").recommendations)
    _button(admin_app, "Neue Empfehlung").click().run()
    admin_app.run()
    assert len(_recommendation_select(admin_app).options) == before + 1
    next(item for item in admin_app.text_input if item.label == "Empfehlungs-ID").set_value("insulate")
    next(item for item in admin_app.text_input if item.label == "Titel").set_value("Dämmung prüfen")
    next(item for item in admin_app.text_area if item.label == "Nachricht").set_value("Lass die Hülle prüfen.")
    next(item for item in admin_app.text_input if item.label == "Priorität (Empfehlung)").set_value("2")
    _button(admin_app, "Empfehlung übernehmen").click().run()
    admin_app.run()

    assert not [item.value for item in admin_app.error]
    offered = next(item for item in admin_app.multiselect if item.label == "Empfehlungen (Pflicht-Empfehlung)")
    assert "insulate" in offered.options

    _button(admin_app, "Empfehlungen als Draft speichern").click().run()

    assert not [item.value for item in admin_app.error]
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert len(draft.recommendations) == before + 1
    saved = draft.recommendations[-1]
    assert (saved.recommendation_id, saved.priority, saved.title, saved.message) == (
        "insulate", 2, "Dämmung prüfen", "Lass die Hülle prüfen."
    )


def test_admin_refuses_to_delete_a_referenced_recommendation(admin_app):
    pack = load_domain_pack_version("verein-demo", "1.0.0")
    index = next(i for i, r in enumerate(pack.recommendations) if r.recommendation_id == "funding-diversification")
    _recommendation_select(admin_app).set_value(index).run()

    _button(admin_app, "Empfehlung löschen").click().run()

    errors = [item.value for item in admin_app.error]
    assert errors and "grants-dependent" in errors[0]
    assert len(_recommendation_select(admin_app).options) == len(pack.recommendations)


# --- figures in recommendation texts (Kennzahlen) -----------------------------


def _figures_pack_data():
    data = _pack_data()
    data["questions"].append({"question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
                              "field_type": "file", "required": False, "upload": {}})
    data["tools"] = {"enabled": True, "after_import": [{"tool": "analyze_tiers"},
                                                      {"tool": "analyze_characteristics"}]}
    return data


def test_recommendation_form_parses_figures_and_template():
    from guideos.adapters.question_editor import figures_from_text, figures_to_text

    pack = validate_domain_pack_data(_figures_pack_data())
    values = _recommendation_form(
        message_with_figures="In {months_above} von {months_total} Monaten darüber; Ladevorgänge: {confidence}.",
        figures=(
            "months_above: analyze_tiers /totals/months_above 0\n"
            "\n"
            "months_total: analyze_tiers /totals/months_total\n"
            'confidence: analyze_characteristics /characteristics/charging/confidence '
            '{"high": "hoch", "medium": "mittel"}\n'
        ),
    )

    created = recommendation_from_form(values, pack, index=None)

    assert created.message_with_figures.startswith("In {months_above}")
    assert created.figures["months_above"].model_dump() == {
        "tool": "analyze_tiers", "pointer": "/totals/months_above", "decimals": 0, "map": None}
    assert created.figures["months_total"].decimals is None
    assert created.figures["confidence"].map == {"high": "hoch", "medium": "mittel"}
    # Round trip through the text shown in the form.
    assert figures_from_text(figures_to_text(created.figures)) == created.figures
    assert figures_to_text(None) == ""
    assert figures_to_text(created.figures).splitlines()[0] == "months_above: analyze_tiers /totals/months_above 0"
    assert figures_to_text(created.figures).splitlines()[2].endswith(' {"high": "hoch", "medium": "mittel"}')


def test_recommendation_form_keeps_entry_without_figures_unchanged():
    pack = validate_domain_pack_data(_figures_pack_data())

    created = recommendation_from_form(_recommendation_form(message_with_figures="  ", figures=""), pack, index=None)

    assert created.figures is None and created.message_with_figures is None
    assert "figures" not in pack_draft_payload(pack_with_recommendations(pack, [created]))["recommendations"][0]


@pytest.mark.parametrize("overrides, message", [
    ({"figures": "months_above analyze_tiers /t"}, "name: werkzeug /pointer"),
    ({"figures": "Monate: analyze_tiers /t"}, "Kleinbuchstaben"),
    ({"figures": "months_above: analyze_tiers"}, "name: werkzeug /pointer"),
    ({"figures": "months_above: analyze_tiers totals"}, "mit / beginnen"),
    ({"figures": "months_above: analyze_tiers /t x"}, "Dezimalstellen"),
    ({"figures": "months_above: analyze_tiers /t 0 {nicht json}"}, "Zuordnung"),
    ({"figures": "months_above: analyze_tiers /t\nmonths_above: analyze_tiers /u"}, "doppelt"),
    ({"figures": "months_above: analyze_tiers /t"}, "nur gemeinsam"),
    ({"message_with_figures": "In {months_above} Monaten."}, "nur gemeinsam"),
    ({"figures": "months_above: analyze_tiers /t", "message_with_figures": "In {monate} Monaten."},
     "Platzhalter ohne Kennzahl"),
    ({"figures": "months_above: analyze_dataset /t", "message_with_figures": "In {months_above} Monaten."},
     "weder dataset_tool"),
])
def test_recommendation_form_rejects_malformed_figure_lines(overrides, message):
    pack = validate_domain_pack_data(_figures_pack_data())

    with pytest.raises(EditorError, match=message):
        recommendation_from_form(_recommendation_form(**overrides), pack, index=None)
