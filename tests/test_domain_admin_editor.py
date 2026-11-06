import json
import datetime

import pytest

from guideos.adapters.question_editor import (
    EditorError,
    assert_version_editable,
    condition_from_form,
    delete_question,
    draft_error,
    knowledge_recommendations_from_form,
    move_question,
    new_domain_pack,
    new_question,
    options_from_rows,
    pack_with_questions,
    question_from_form,
    result_chat_from_form,
    validation_from_form,
)
from guideos.domains.catalog import DomainCatalogError, DomainPackRef
from guideos.domains.loader import load_domain_pack
from guideos.domains.models import QuestionDefinition


def _form(**overrides):
    values = {
        "question_id": "legal_form",
        "field_key": "legal_form",
        "text": "Rechtsform?",
        "help_text": "",
        "field_type": "select",
        "widget": None,
        "required": True,
        "allow_unknown": False,
        "option_rows": [{"value": "gmbh", "label": "GmbH"}, {"value": "verein", "label": ""}],
        "visible_field_key": None,
        "visible_operator": "equals",
        "visible_value": "",
    }
    values.update(overrides)
    return values


def test_question_from_form_builds_select_question_with_options():
    question = question_from_form(_form(widget="radio"), earlier_questions=[])

    assert question.field_type == "select"
    assert question.widget == "radio"
    assert [(o.value, o.label) for o in question.options] == [("gmbh", "GmbH"), ("verein", "verein")]
    assert question.help_text is None
    assert question.visible_if is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"question_id": " "}, "Frage-ID"),
        ({"field_key": ""}, "Feldschlüssel"),
        ({"text": "  "}, "Fragetext"),
        ({"option_rows": []}, "mindestens eine Option"),
        ({"field_type": "multiselect", "option_rows": [{"value": "", "label": ""}]},
         "mindestens eine Option"),
    ],
)
def test_question_from_form_rejects_invalid_input_in_german(overrides, message):
    with pytest.raises(EditorError, match=message):
        question_from_form(_form(**overrides), earlier_questions=[])


def test_question_from_form_builds_file_question_with_upload_block():
    question = question_from_form(_form(
        question_id="dataset_file", field_key="dataset_file", text="Messdaten?", field_type="file",
        required=False, option_rows=[], upload_tool=" import_dataset ",
        upload_arguments='{"direction": "export"}', upload_extensions="CSV, txt",
        upload_max_mib=16, use_in_advisory=False,
    ), earlier_questions=[])

    assert question.field_type == "file" and question.options == []
    assert question.upload.tool == "import_dataset"
    assert question.upload.arguments == {"direction": "export"}
    assert question.upload.extensions == ["csv", "txt"]
    assert question.upload.max_bytes == 16 * 1024 * 1024
    assert question.upload.use_in_advisory is False

    defaults = question_from_form(_form(field_type="file", option_rows=[]), earlier_questions=[])
    assert defaults.upload.tool is None and defaults.upload.arguments == {}
    assert defaults.upload.extensions == ["csv", "txt"] and defaults.upload.use_in_advisory is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"upload_arguments": "{nicht json"}, "JSON"),
        ({"upload_arguments": "[1, 2]"}, "JSON-Objekt"),
        ({"upload_arguments": '{"owner": "x"}'}, "owner"),
        ({"upload_extensions": " , "}, "Endung"),
    ],
)
def test_question_from_form_rejects_invalid_upload_settings_in_german(overrides, message):
    with pytest.raises(EditorError, match=message):
        question_from_form(_form(field_type="file", option_rows=[], **overrides), earlier_questions=[])


def test_options_from_rows_skips_blank_rows_defaults_labels_and_rejects_duplicates():
    options = options_from_rows(
        [{"value": " a ", "label": " Alpha "}, {"value": None, "label": None}, {"value": "b", "label": ""}]
    )

    assert [(o.value, o.label) for o in options] == [("a", "Alpha"), ("b", "b")]
    with pytest.raises(EditorError, match="eindeutig"):
        options_from_rows([{"value": "a", "label": "A"}, {"value": "a", "label": "B"}])
    with pytest.raises(EditorError, match="nicht leer"):
        options_from_rows([{"value": "", "label": "Nur Label"}])


def test_validation_from_form_keeps_only_type_relevant_bounds():
    values = {
        "minimum": 1, "maximum": 10, "min_length": 2, "max_length": 5, "pattern": "^x$",
        "min_count": 1, "max_count": 3,
        "min_date": datetime.date(2000, 1, 1), "max_date": None,
    }

    assert validation_from_form("integer", values).model_dump(exclude_none=True) == {
        "minimum": 1, "maximum": 10
    }
    assert validation_from_form("text", values).model_dump(exclude_none=True) == {
        "min_length": 2, "max_length": 5, "pattern": "^x$"
    }
    assert validation_from_form("multiselect", values).model_dump(exclude_none=True) == {
        "min_count": 1, "max_count": 3
    }
    assert validation_from_form("date", values).model_dump(exclude_none=True) == {
        "min_date": "2000-01-01"
    }
    assert validation_from_form("boolean", values).model_dump(exclude_none=True) == {}
    assert validation_from_form("text", {"pattern": "  "}).pattern is None


def _target(field_type, options=()):
    return QuestionDefinition.model_validate(
        {
            "question_id": "t", "field_key": "t", "text": "?", "field_type": field_type,
            "required": True,
            "options": [{"value": v, "label": v} for v in options],
        }
    )


def test_condition_from_form_is_missing_has_no_value_in_fields_set():
    condition = condition_from_form("t", "is_missing", "ignored", _target("select", ["a"]))

    assert condition.operator == "is_missing"
    assert "value" not in condition.model_fields_set
    assert condition_from_form(None, "equals", "a", _target("select", ["a"])) is None


def test_condition_from_form_parses_values_by_target_type():
    assert condition_from_form("t", "in", "1990, 2000", _target("integer")).value == [1990, 2000]
    assert condition_from_form("t", "equals", "ja", _target("boolean")).value is True
    assert condition_from_form("t", "equals", "2,5", _target("number")).value == 2.5
    assert condition_from_form("t", "contains_any", "a, b", _target("multiselect", ["a", "b"])).value == ["a", "b"]
    assert condition_from_form("t", "contains", "a", _target("multiselect", ["a"])).value == "a"
    with pytest.raises(EditorError, match="ganze Zahl"):
        condition_from_form("t", "equals", "abc", _target("integer"))
    with pytest.raises(EditorError, match="nicht leer"):
        condition_from_form("t", "equals", " ", _target("select", ["a"]))


def test_question_from_form_attaches_visibility_to_earlier_question():
    earlier = [_target("boolean")]
    question = question_from_form(
        _form(field_type="text", option_rows=[], visible_field_key="t",
              visible_operator="equals", visible_value="nein"),
        earlier_questions=earlier,
    )

    assert question.visible_if.field_key == "t"
    assert question.visible_if.value is False


def test_new_question_gets_unique_key_and_is_valid():
    pack = load_domain_pack()
    question = new_question(pack.questions)

    assert question.question_id == question.field_key
    assert question.field_key not in {q.field_key for q in pack.questions}
    assert draft_error(pack_with_questions(pack, [*pack.questions, question])) is None
    again = new_question([*pack.questions, question])
    assert again.field_key != question.field_key


def test_move_and_delete_question_return_new_lists():
    pack = load_domain_pack()
    keys = [q.field_key for q in pack.questions]

    moved = move_question(pack.questions, 0, 1)
    assert [q.field_key for q in moved] == [keys[1], keys[0], *keys[2:]]
    assert [q.field_key for q in pack.questions] == keys
    assert move_question(pack.questions, 0, -1) == pack.questions
    assert move_question(pack.questions, len(keys) - 1, 1) == pack.questions

    deleted = delete_question(pack.questions, 1)
    assert [q.field_key for q in deleted] == [keys[0], *keys[2:]]


def test_draft_error_reports_rule_reference_in_german():
    pack = load_domain_pack()
    without_heating = [q for q in pack.questions if q.field_key != "funding_source"]

    error = draft_error(pack_with_questions(pack, without_heating))

    assert error is not None and "unbekanntes Feld funding_source" in error


def test_draft_error_accepts_packs_with_derivations_and_metric_rules():
    from tests.pack_fixtures import tools_pack

    assert draft_error(tools_pack()) is None


def test_draft_error_round_trips_is_missing_visibility():
    pack = load_domain_pack()
    questions = list(pack.questions)
    questions[1] = questions[1].model_copy(
        update={"visible_if": condition_from_form("club_type", "is_missing", "", questions[0])}
    )

    assert draft_error(pack_with_questions(pack, questions)) is None


def test_assert_version_editable_refuses_active_and_bundled_versions():
    def ref(version, source, active):
        return DomainPackRef(
            domain_id="demo", version=version, path=None, source=source, active=active, valid=True
        )

    refs = [ref("1.0.0", "bundled", True), ref("1.0.1", "local", False), ref("1.0.2", "local", True)]

    assert_version_editable("demo", "1.0.1", refs)
    assert_version_editable("demo", "1.0.3", refs)
    assert_version_editable("other", "1.0.0", refs)
    with pytest.raises(DomainCatalogError, match="Metadaten"):
        assert_version_editable("demo", "1.0.0", refs)
    with pytest.raises(DomainCatalogError, match="aktiv"):
        assert_version_editable("demo", "1.0.2", refs)


def test_new_domain_pack_is_empty_valid_and_starts_at_version_0_1_0():
    pack = new_domain_pack(" mein-check ", " Mein Check ", " Kurzbeschreibung ", [])

    assert (pack.domain_id, pack.version, pack.name) == ("mein-check", "0.1.0", "Mein Check")
    assert (pack.description, pack.default_locale) == ("Kurzbeschreibung", "de")
    assert pack.questions == [] and pack.rules == [] and pack.recommendations == []
    assert pack.web_sources is None
    assert draft_error(pack) is None


@pytest.mark.parametrize(
    "domain_id", ["", "Mein-Check", "mein check", "mein_check", "../geheim", "-check", "check-"]
)
def test_new_domain_pack_rejects_unusable_domain_ids_in_german(domain_id):
    with pytest.raises(EditorError, match="Domain-ID"):
        new_domain_pack(domain_id, "Mein Check", "", [])


def test_new_domain_pack_requires_a_name_and_an_unused_domain_id():
    existing = DomainPackRef(
        domain_id="verein-demo", version="1.0.0", path=None, source="bundled", active=True,
        valid=True,
    )

    with pytest.raises(EditorError, match="Name"):
        new_domain_pack("mein-check", "  ", "", [existing])
    with pytest.raises(EditorError, match="verein-demo existiert bereits"):
        new_domain_pack("verein-demo", "Kopie", "", [existing])


# --- AppTest: Domain-Admin question editor ---

from streamlit.testing.v1 import AppTest  # noqa: E402

from guideos.domains.catalog import (  # noqa: E402
    DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT,
    LocalCatalogStore,
    list_domain_versions,
    load_domain_pack_version,
)


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
    ref = next(
        r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.0.0"
    )
    _select(app, "Domain-Version").set_value(ref).run()
    assert not app.error
    return app


def _select(app, label):
    return next(item for item in app.selectbox if item.label == label)


def _button(app, label):
    return next(item for item in app.button if item.label == label)


def _input(app, label):
    return next(item for item in app.text_input if item.label == label)


def _errors(app):
    return [item.value for item in app.error]


def test_admin_questions_tab_lists_questions_in_pack_order(admin_app):
    options = _select(admin_app, "Frage bearbeiten").options

    assert [option.split(". ")[1].split(":")[0] for option in options] == [
        "club_type", "founded_year", "members_count",
        "funding_source", "volunteer_situation", "annual_budget_eur",
    ]
    assert _select(admin_app, "Feldtyp").value == "select"
    assert _input(admin_app, "Fragetext").value == "Welche Art von Verein ist es?"


def test_admin_new_question_is_appended_and_selected(admin_app):
    _button(admin_app, "Neue Frage").click().run()

    select = _select(admin_app, "Frage bearbeiten")
    assert len(select.options) == 7
    assert select.value == 6
    assert _input(admin_app, "Frage-ID").value == "frage_7"
    assert not _errors(admin_app)


def test_admin_apply_form_updates_question_text(admin_app):
    _input(admin_app, "Fragetext").set_value("Welcher Vereinstyp?")
    _button(admin_app, "Frage übernehmen").click().run()

    assert not _errors(admin_app)
    assert _input(admin_app, "Fragetext").value == "Welcher Vereinstyp?"
    assert "Welcher Vereinstyp?" in _select(admin_app, "Frage bearbeiten").options[0]


def test_admin_move_question_down_changes_order(admin_app):
    _button(admin_app, "Nach unten").click().run()

    options = _select(admin_app, "Frage bearbeiten").options
    assert options[0].startswith("1. founded_year")
    assert options[1].startswith("2. club_type")
    assert _select(admin_app, "Frage bearbeiten").value == 1


def test_admin_delete_question_referenced_by_rule_blocks_save(admin_app, tmp_path):
    _select(admin_app, "Frage bearbeiten").set_value(3).run()
    _button(admin_app, "Frage löschen").click().run()

    assert any("unbekanntes Feld funding_source" in text for text in _errors(admin_app))
    _button(admin_app, "Fragen als Draft speichern").click().run()
    assert not (tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json").exists()


def test_admin_save_questions_creates_next_patch_draft(admin_app, tmp_path):
    bundled = load_domain_pack()
    _input(admin_app, "Fragetext").set_value("Welcher Vereinstyp?")
    _button(admin_app, "Frage übernehmen").click().run()

    _button(admin_app, "Fragen als Draft speichern").click().run()

    assert not _errors(admin_app)
    assert any("Draft gespeichert" in item.value for item in admin_app.success)
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert draft.questions[0].text == "Welcher Vereinstyp?"
    assert draft.questions[1:] == bundled.questions[1:]
    assert load_domain_pack().questions[0].text == bundled.questions[0].text


def test_admin_refuses_to_save_over_active_version(admin_app, tmp_path):
    _input(admin_app, "Version").set_value("1.0.0")
    _button(admin_app, "Fragen als Draft speichern").click().run()

    assert any("Metadaten" in text for text in _errors(admin_app))
    assert not (tmp_path / "packs").exists()


def test_admin_discard_changes_restores_stored_questions(admin_app):
    _button(admin_app, "Neue Frage").click().run()
    assert len(_select(admin_app, "Frage bearbeiten").options) == 7

    _button(admin_app, "Änderungen verwerfen").click().run()

    assert len(_select(admin_app, "Frage bearbeiten").options) == 6


def test_admin_field_type_change_shows_type_specific_inputs(admin_app):
    assert any(item.label == "Darstellung" for item in admin_app.selectbox)
    assert not any(item.label == "Mindestlänge" for item in admin_app.number_input)

    _select(admin_app, "Feldtyp").set_value("text").run()

    assert any(item.label == "Mindestlänge" for item in admin_app.number_input)
    assert any(item.label == "Muster (regulärer Ausdruck)" for item in admin_app.text_input)
    _select(admin_app, "Feldtyp").set_value("date").run()
    assert any(item.label == "Frühestes Datum" for item in admin_app.date_input)
    _select(admin_app, "Feldtyp").set_value("file").run()
    assert any(item.label == "Importwerkzeug (leer: dataset_tool des Packs)" for item in admin_app.text_input)
    assert any(item.label == "Erlaubte Endungen (kommagetrennt)" for item in admin_app.text_input)
    assert any(item.label == "Datensatz für die Beratung verwenden" for item in admin_app.checkbox)
    assert not any(item.label == "Darstellung" for item in admin_app.selectbox)


# --- AppTest: SOyA tab ---

from guideos.domains.soya import pack_to_soya  # noqa: E402


def _soya_bytes(document):
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _upload(app, data, name="import.soya.json"):
    uploader = next(item for item in app.file_uploader if item.label.startswith("SOyA-Datei"))
    uploader.upload(name, data, "application/json")


def test_admin_soya_tab_offers_download(admin_app):
    downloads = admin_app.get("download_button")

    assert any("SOyA-Struktur herunterladen" in item.proto.label for item in downloads)


def test_admin_soya_import_creates_draft_with_kept_rules(admin_app, tmp_path):
    bundled = load_domain_pack()
    document = pack_to_soya(bundled)
    annotation = next(o for o in document["content"]["overlays"] if o["type"] == "OverlayAnnotation")
    annotation["attributes"]["club_type"]["label"]["de"] = "Welcher Vereinstyp?"
    _upload(admin_app, _soya_bytes(document))

    _button(admin_app, "Import als Draft speichern").click().run()

    assert not _errors(admin_app)
    assert any("Draft gespeichert" in item.value for item in admin_app.success)
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert draft.questions[0].text == "Welcher Vereinstyp?"
    assert draft.questions[1:] == bundled.questions[1:]
    assert draft.rules == bundled.rules
    assert draft.recommendations == bundled.recommendations


def test_admin_soya_import_reports_invalid_json_in_german(admin_app, tmp_path):
    _upload(admin_app, b"{ kein json")

    _button(admin_app, "Import als Draft speichern").click().run()

    assert any("kein gültiges JSON" in text for text in _errors(admin_app))
    assert not (tmp_path / "packs").exists()


def test_admin_soya_import_warns_about_dropped_rules(admin_app):
    document = pack_to_soya(load_domain_pack())
    del document["content"]["bases"][0]["attributes"]["funding_source"]
    for overlay in document["content"]["overlays"]:
        overlay.get("attributes", {}).pop("funding_source", None)
    form = next(o for o in document["content"]["overlays"] if o["type"] == "OverlayForm")
    form["schema"]["properties"].pop("funding_source")
    form["ui"]["elements"] = [
        e for e in form["ui"]["elements"] if e["scope"] != "#/properties/funding_source"
    ]
    _upload(admin_app, _soya_bytes(document))

    _button(admin_app, "Import als Draft speichern").click().run()

    assert any("grants-dependent" in item.value for item in admin_app.warning)
    draft = load_domain_pack_version("verein-demo", "1.0.1")
    assert "funding_source" not in {q.field_key for q in draft.questions}


def test_admin_soya_import_refuses_active_target_version(admin_app, tmp_path):
    _upload(admin_app, _soya_bytes(pack_to_soya(load_domain_pack())))
    _input(admin_app, "Ziel-Version").set_value("1.0.0")

    _button(admin_app, "Import als Draft speichern").click().run()

    assert any("Metadaten" in text for text in _errors(admin_app))
    assert not (tmp_path / "packs").exists()


# --- AppTest: Wissen tab and web sources ---

from guideos.knowledge.factory import knowledge_service_from_env  # noqa: E402
from guideos.providers.registry import get_provider  # noqa: E402


def _knowledge():
    return knowledge_service_from_env(get_provider("ollama"))


def _upload_knowledge(app, name, data, mime="text/markdown"):
    uploader = next(item for item in app.file_uploader if item.label.startswith("Dokument hochladen"))
    uploader.upload(name, data, mime)


def _successes(app):
    return [item.value for item in app.success]


def test_admin_knowledge_tab_starts_empty_and_names_the_search_mode(admin_app):
    assert any("noch keine Dokumente" in item.value for item in admin_app.info)
    assert any("lexikalisch" in item.value for item in admin_app.caption)


def test_admin_adds_lists_and_removes_a_knowledge_document(admin_app):
    _upload_knowledge(
        admin_app, "leitfaden.md", "# Förderung\n\nDie Jugendarbeit wird gefördert.".encode()
    )
    _input(admin_app, "Titel (optional)").set_value("Leitfaden Förderung")
    _button(admin_app, "Dokument hinzufügen").click().run()

    assert not _errors(admin_app)
    assert any("Leitfaden Förderung" in text for text in _successes(admin_app))
    [document] = _knowledge().list_documents("verein-demo")
    assert (document.title, document.filename, document.kind) == (
        "Leitfaden Förderung", "leitfaden.md", "document"
    )
    assert admin_app.dataframe[-1].value["Titel"].tolist() == ["Leitfaden Förderung"]

    _button(admin_app, "Dokument hinzufügen").click().run()
    assert any("bereits in der Wissensbasis" in text for text in _errors(admin_app))

    _button(admin_app, "Ausgewähltes Dokument entfernen").click().run()
    assert _knowledge().list_documents("verein-demo") == []


def test_admin_knowledge_upload_requires_a_file(admin_app):
    _button(admin_app, "Dokument hinzufügen").click().run()

    assert "Bitte zuerst eine Datei auswählen." in _errors(admin_app)


def test_admin_knowledge_search_shows_matching_passages(admin_app):
    _knowledge().add_document(
        "verein-demo", "leitfaden.md",
        "# Förderung\n\nDie Jugendarbeit wird gefördert.".encode(), "http://fake",
    )
    admin_app.run()

    _input(admin_app, "Suche testen").set_value("Jugendarbeit")
    _button(admin_app, "Suchen").click().run()

    assert any("D1" in item.value and "Dokument „Förderung“" in item.value
               for item in admin_app.markdown)
    assert any("Die Jugendarbeit wird gefördert." in item.value for item in admin_app.caption)

    _input(admin_app, "Suche testen").set_value("Photovoltaik")
    _button(admin_app, "Suchen").click().run()
    assert any("Keine passenden Abschnitte" in item.value for item in admin_app.info)


def test_admin_adds_a_web_page_and_warns_while_the_pack_ignores_web_sources(
    admin_app, monkeypatch
):
    class Response:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amount):
            return "<title>Förderung</title><p>Vereine werden gefördert.</p>".encode()

        def geturl(self):
            return "https://www.klimaaktiv.at/heizung"

    monkeypatch.setattr(
        "guideos.knowledge.ingestion.request.urlopen", lambda req, timeout: Response()
    )
    assert any("verwendet keine Webquellen" in item.value for item in admin_app.warning)

    _input(admin_app, "Adresse der Webseite").set_value("https://www.klimaaktiv.at/heizung")
    _button(admin_app, "Webseite abrufen und hinzufügen").click().run()

    assert not _errors(admin_app)
    [document] = _knowledge().list_documents("verein-demo")
    assert (document.kind, document.title, document.url) == (
        "web", "Förderung", "https://www.klimaaktiv.at/heizung"
    )

    _input(admin_app, "Adresse der Webseite").set_value("file:///etc/passwd")
    _button(admin_app, "Webseite abrufen und hinzufügen").click().run()
    assert any("http- oder https-Adresse" in text for text in _errors(admin_app))


def test_admin_metadata_saves_web_sources_into_the_draft(admin_app, tmp_path):
    checkbox = next(item for item in admin_app.checkbox if item.label.startswith("Webquellen"))
    checkbox.check()
    _input(admin_app, "Erlaubte Web-Domains (kommagetrennt, leer = alle)").set_value(
        "Klimaaktiv.at, oib.or.at "
    )
    _button(admin_app, "Als Draft speichern").click().run()

    assert not _errors(admin_app)
    draft = load_domain_pack(tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json")
    assert draft.web_sources.enabled is True
    assert draft.web_sources.allowed_domains == ["klimaaktiv.at", "oib.or.at"]


def test_admin_metadata_keeps_drafts_without_web_sources_unchanged(admin_app, tmp_path):
    _button(admin_app, "Als Draft speichern").click().run()

    saved = json.loads(
        (tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json").read_text(encoding="utf-8")
    )
    assert "web_sources" not in saved


def _select_draft_without_advisory_guidance(app, tmp_path):
    """The bundled demo pack carries advisory_guidance; save a local draft
    1.5.0 without it and select that version."""
    from guideos.domains.catalog import save_domain_pack_draft

    save_domain_pack_draft(
        load_domain_pack().model_copy(update={"version": "1.5.0", "advisory_guidance": None}),
        local_root=tmp_path / "packs",
    )
    app.run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.5.0")
    _select(app, "Domain-Version").set_value(ref).run()


def test_admin_metadata_saves_advisory_guidance_into_the_draft(admin_app, tmp_path):
    _select_draft_without_advisory_guidance(admin_app, tmp_path)
    area = next(item for item in admin_app.text_area if item.label.startswith("Beratungsvorgaben"))
    assert area.value == ""
    area.set_value("  Der Kontext ist Österreich.  ")
    _button(admin_app, "Als Draft speichern").click().run()

    assert not _errors(admin_app)
    draft = load_domain_pack(tmp_path / "packs" / "verein-demo" / "1.5.1" / "pack.json")
    assert draft.advisory_guidance == "Der Kontext ist Österreich."


def test_admin_metadata_keeps_drafts_without_advisory_guidance_unchanged(admin_app, tmp_path):
    _select_draft_without_advisory_guidance(admin_app, tmp_path)
    _button(admin_app, "Als Draft speichern").click().run()

    saved = json.loads(
        (tmp_path / "packs" / "verein-demo" / "1.5.1" / "pack.json").read_text(encoding="utf-8")
    )
    assert "advisory_guidance" not in saved


def test_admin_soya_import_keeps_the_advisory_guidance_of_the_selected_version(
    admin_app, tmp_path
):
    from guideos.domains.catalog import save_domain_pack_draft

    save_domain_pack_draft(
        load_domain_pack().model_copy(
            update={"version": "1.5.0", "advisory_guidance": "Der Kontext ist Österreich."}
        ),
        local_root=tmp_path / "packs",
    )
    admin_app.run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.5.0")
    _select(admin_app, "Domain-Version").set_value(ref).run()
    _upload(admin_app, _soya_bytes(pack_to_soya(load_domain_pack())))

    _button(admin_app, "Import als Draft speichern").click().run()

    assert not _errors(admin_app)
    assert (
        load_domain_pack_version("verein-demo", "1.5.1").advisory_guidance
        == "Der Kontext ist Österreich."
    )


def test_admin_soya_import_keeps_the_web_sources_of_the_selected_version(admin_app, tmp_path):
    from guideos.domains.catalog import save_domain_pack_draft
    from guideos.domains.models import WebSourceSettings

    web_sources = WebSourceSettings(enabled=True, allowed_domains=["klimaaktiv.at"])
    save_domain_pack_draft(
        load_domain_pack().model_copy(update={"version": "1.5.0", "web_sources": web_sources}),
        local_root=tmp_path / "packs",
    )
    admin_app.run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.5.0")
    _select(admin_app, "Domain-Version").set_value(ref).run()
    _upload(admin_app, _soya_bytes(pack_to_soya(load_domain_pack())))

    _button(admin_app, "Import als Draft speichern").click().run()

    assert not _errors(admin_app)
    assert load_domain_pack_version("verein-demo", "1.5.1").web_sources == web_sources


# --- AppTest: create an empty pack ---


def _create_pack(app, domain_id, name):
    _input(app, "Domain-ID des neuen Packs").set_value(domain_id)
    _input(app, "Name des neuen Packs").set_value(name)
    _button(app, "Neues Domain-Pack erstellen").click().run()


def test_admin_creates_an_empty_pack_and_selects_it(admin_app):
    _create_pack(admin_app, "mein-check", "Mein Check")

    assert not _errors(admin_app)
    assert any("mein-check/0.1.0" in text for text in _successes(admin_app))
    draft = load_domain_pack_version("mein-check", "0.1.0")
    assert draft.name == "Mein Check" and draft.questions == []
    selected = _select(admin_app, "Domain-Version").value
    assert (selected.domain_id, selected.version, selected.source) == (
        "mein-check", "0.1.0", "local"
    )
    assert _input(admin_app, "Name").value == "Mein Check"
    assert _input(admin_app, "Domain-ID des neuen Packs").value == ""


def test_admin_refuses_to_create_a_pack_with_an_existing_or_invalid_domain_id(admin_app):
    _create_pack(admin_app, "verein-demo", "Kopie")
    assert any("existiert bereits" in text for text in _errors(admin_app))

    _create_pack(admin_app, "Mein Check", "Mein Check")
    assert any("Kleinbuchstaben" in text for text in _errors(admin_app))
    bundled = {path.name for path in DEFAULT_BUNDLED_DOMAIN_PACKS_ROOT.iterdir() if path.is_dir()}
    assert {ref.domain_id for ref in list_domain_versions()} == bundled


# --- AppTest: delete a draft version ---


def _delete_selected_version(app):
    next(item for item in app.checkbox if item.label.startswith("Ich möchte")).check().run()
    _button(app, "Version löschen").click().run()


def test_admin_deletes_the_selected_draft_after_confirmation(admin_app):
    _create_pack(admin_app, "mein-check", "Mein Check")
    assert _button(admin_app, "Version löschen").disabled is True

    _delete_selected_version(admin_app)

    assert not _errors(admin_app)
    assert any("mein-check/0.1.0 wurde gelöscht" in text for text in _successes(admin_app))
    assert "mein-check" not in {ref.domain_id for ref in list_domain_versions()}
    assert _select(admin_app, "Domain-Version").value.domain_id != "mein-check"


def test_admin_offers_no_deletion_for_bundled_and_active_versions(admin_app):
    assert all(item.label != "Version löschen" for item in admin_app.button)
    assert any("Gebündelte Packs" in item.value for item in admin_app.caption)

    _create_pack(admin_app, "mein-check", "Mein Check")
    _button(admin_app, "Ausgewählte Version aktivieren").click().run()
    # Activation reruns the script and resets the selection; start from a clean tree.
    admin_app.run()
    active = next(ref for ref in list_domain_versions() if ref.domain_id == "mein-check")
    _select(admin_app, "Domain-Version").set_value(active).run()

    assert active.active is True
    assert all(item.label != "Version löschen" for item in admin_app.button)
    assert any("aktiv" in item.value and "andere Version" in item.value for item in admin_app.caption)


def test_admin_refuses_to_delete_a_version_that_still_has_sessions(admin_app, tmp_path):
    from guideos.assessment.models import AssessmentSession
    from guideos.assessment.repository import LocalAssessmentRepository

    _create_pack(admin_app, "mein-check", "Mein Check")
    draft = load_domain_pack_version("mein-check", "0.1.0")
    LocalAssessmentRepository(tmp_path / "assessments.json").save(
        AssessmentSession.create(
            domain_id="mein-check", domain_version="0.1.0",
            domain_content_hash=draft.content_hash, locale="de",
        )
    )

    _delete_selected_version(admin_app)

    assert any("noch 1 Assessment-Session" in text for text in _errors(admin_app))
    assert load_domain_pack_version("mein-check", "0.1.0").name == "Mein Check"


def _add_knowledge(domain_id):
    return _knowledge().add_document(
        domain_id, "leitfaden.md", "# Förderung\n\nText zur Förderung.".encode(), "http://fake"
    )


def test_admin_deletes_the_knowledge_base_with_the_last_version_of_a_domain(admin_app):
    _create_pack(admin_app, "mein-check", "Mein Check")
    _add_knowledge("mein-check")
    kept = _add_knowledge("verein-demo")
    admin_app.run()
    assert any(
        "letzte Version" in item.value and "1 Dokument(e)" in item.value
        for item in admin_app.caption
    )

    _delete_selected_version(admin_app)

    assert not _errors(admin_app)
    assert any(
        "mein-check/0.1.0 wurde gelöscht" in text and "1 Dokument(e)" in text
        for text in _successes(admin_app)
    )
    assert _knowledge().list_documents("mein-check") == []
    assert _knowledge().list_documents("verein-demo") == [kept]


def test_admin_keeps_the_knowledge_base_while_other_versions_of_the_domain_exist(
    admin_app, tmp_path
):
    from guideos.domains.catalog import save_domain_pack_draft

    save_domain_pack_draft(
        load_domain_pack().model_copy(update={"version": "1.5.0"}), local_root=tmp_path / "packs"
    )
    document = _add_knowledge("verein-demo")
    admin_app.run()
    draft = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.5.0")
    _select(admin_app, "Domain-Version").set_value(draft).run()
    assert not any("letzte Version" in item.value for item in admin_app.caption)

    _delete_selected_version(admin_app)

    assert not _errors(admin_app)
    assert "1.5.0" not in {r.version for r in list_domain_versions() if r.domain_id == "verein-demo"}
    assert _knowledge().list_documents("verein-demo") == [document]


def test_admin_metadata_saves_the_compact_result_chat_into_the_draft(admin_app, tmp_path):
    checkbox = next(item for item in admin_app.checkbox if item.label.startswith("Empfehlungen im Chat"))
    assert checkbox.value is False
    checkbox.check()
    _button(admin_app, "Als Draft speichern").click().run()

    assert not _errors(admin_app)
    draft = load_domain_pack(tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json")
    assert draft.result_chat.recommendations == "compact"


def test_admin_metadata_keeps_drafts_without_result_chat_block_unchanged(admin_app, tmp_path):
    _button(admin_app, "Als Draft speichern").click().run()

    saved = json.loads(
        (tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json").read_text(encoding="utf-8")
    )
    assert "result_chat" not in saved


def test_result_chat_from_form_keeps_the_block_implicit_while_unset():
    assert result_chat_from_form(False, "", "", "") is None
    compact = result_chat_from_form(True, "", " ", "")
    assert compact.recommendations == "compact" and compact.intro is None and compact.offer is None
    texts = result_chat_from_form(False, " Dein Check ist fertig: ", "", "Hinweise ergänzen?")
    assert texts.recommendations == "detailed"
    assert (texts.intro, texts.updated_intro, texts.offer) == (
        "Dein Check ist fertig:", None, "Hinweise ergänzen?"
    )
    with pytest.raises(EditorError, match="500"):
        result_chat_from_form(False, "x" * 501, "", "")


def test_admin_metadata_saves_result_chat_texts_into_the_draft(admin_app, tmp_path):
    intro = next(item for item in admin_app.text_area if item.label.startswith("Einleitung im Chat"))
    updated = next(item for item in admin_app.text_area if item.label.startswith("Einleitung nach"))
    offer = next(item for item in admin_app.text_area if item.label.startswith("Angebot für KI-Empfehlungen"))
    assert (intro.value, updated.value, offer.value) == ("", "", "")
    intro.set_value("Dein Check ist fertig:")
    offer.set_value("Soll ich Hinweise ergänzen? Antworte mit Ja.")
    _button(admin_app, "Als Draft speichern").click().run()

    assert not _errors(admin_app)
    draft = load_domain_pack(tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json")
    assert draft.result_chat.recommendations == "detailed"
    assert draft.result_chat.intro == "Dein Check ist fertig:"
    assert draft.result_chat.updated_intro is None
    assert draft.result_chat.offer == "Soll ich Hinweise ergänzen? Antworte mit Ja."
    saved = json.loads((tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json").read_text(encoding="utf-8"))
    assert "updated_intro" not in saved["result_chat"]


def test_knowledge_recommendations_from_form_keeps_the_default_implicit():
    from guideos.domains.models import KnowledgeRecommendationSettings

    assert knowledge_recommendations_from_form(True, 5) is None
    assert knowledge_recommendations_from_form(False, 5) == KnowledgeRecommendationSettings(enabled=False)
    assert knowledge_recommendations_from_form(True, 2) == KnowledgeRecommendationSettings(max_count=2)
    assert knowledge_recommendations_from_form(False, 1) == KnowledgeRecommendationSettings(
        enabled=False, max_count=1
    )


def _knowledge_widgets(app):
    checkbox = next(item for item in app.checkbox if item.label.startswith("Zusätzliche KI-Empfehlungen"))
    number = next(item for item in app.number_input if item.label.startswith("Höchstzahl KI-Empfehlungen"))
    return checkbox, number


def test_admin_metadata_saves_knowledge_recommendation_limits_into_the_draft(admin_app, tmp_path):
    from guideos.domains.models import KnowledgeRecommendationSettings

    checkbox, number = _knowledge_widgets(admin_app)
    assert checkbox.value is True and number.value == 5
    checkbox.uncheck()
    number.set_value(2)
    _button(admin_app, "Als Draft speichern").click().run()

    assert not _errors(admin_app)
    draft = load_domain_pack(tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json")
    assert draft.knowledge_recommendations == KnowledgeRecommendationSettings(enabled=False, max_count=2)


def test_admin_metadata_keeps_drafts_without_knowledge_recommendations_unchanged(admin_app, tmp_path):
    _button(admin_app, "Als Draft speichern").click().run()

    saved = json.loads(
        (tmp_path / "packs" / "verein-demo" / "1.0.1" / "pack.json").read_text(encoding="utf-8")
    )
    assert "knowledge_recommendations" not in saved


def test_admin_soya_import_keeps_knowledge_settings_tools_and_topics_of_the_selected_version(
    admin_app, tmp_path
):
    from guideos.domains.catalog import save_domain_pack_draft
    from guideos.domains.models import KnowledgeRecommendationSettings, ToolSettings, TopicDefinition

    settings = KnowledgeRecommendationSettings(max_count=3)
    tools = ToolSettings(enabled=True)
    topics = [TopicDefinition(topic_id="heating", label="Heizung")]
    save_domain_pack_draft(
        load_domain_pack().model_copy(update={
            "version": "1.5.0", "knowledge_recommendations": settings, "tools": tools, "topics": topics,
        }),
        local_root=tmp_path / "packs",
    )
    admin_app.run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.5.0")
    _select(admin_app, "Domain-Version").set_value(ref).run()
    _upload(admin_app, _soya_bytes(pack_to_soya(load_domain_pack())))

    _button(admin_app, "Import als Draft speichern").click().run()

    assert not _errors(admin_app)
    imported = load_domain_pack_version("verein-demo", "1.5.1")
    assert imported.knowledge_recommendations == settings
    assert imported.tools == tools and imported.topics == topics
