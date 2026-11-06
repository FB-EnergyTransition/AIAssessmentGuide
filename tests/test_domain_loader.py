import json

import pytest

from guideos.domains.loader import DomainPackError, load_domain_pack


def test_load_energy_demo_pack_validates_content_and_hash():
    pack = load_domain_pack()

    assert pack.domain_id == "verein-demo"
    assert pack.version == "1.0.0"
    assert pack.default_locale == "de"
    assert [question.field_key for question in pack.questions] == [
        "club_type",
        "founded_year",
        "members_count",
        "funding_source",
        "volunteer_situation",
        "annual_budget_eur",
    ]
    assert len(pack.content_hash) == 64


def test_load_domain_pack_rejects_unknown_fields(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(
            {
                "domain_id": "demo",
                "version": "1.0.0",
                "name": "Demo",
                "description": "Demo",
                "default_locale": "de",
                "questions": [],
                "rules": [],
                "recommendations": [],
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DomainPackError, match="ungültig"):
        load_domain_pack(path)


def test_load_domain_pack_rejects_invalid_references(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(
            {
                "domain_id": "demo",
                "version": "1.0.0",
                "name": "Demo",
                "description": "Demo",
                "default_locale": "de",
                "questions": [],
                "rules": [
                    {
                        "rule_id": "missing-field",
                        "field_key": "unknown",
                        "operator": "equals",
                        "value": "x",
                        "reason_code": "MISSING",
                        "recommendation_ids": ["unknown"],
                    }
                ],
                "recommendations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DomainPackError, match="unknown"):
        load_domain_pack(path)


def test_load_domain_pack_rejects_non_semantic_version(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(
            {
                "domain_id": "demo",
                "version": "latest",
                "name": "Demo",
                "description": "Demo",
                "default_locale": "de",
                "questions": [],
                "rules": [],
                "recommendations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DomainPackError, match="Version"):
        load_domain_pack(path)


def test_load_domain_pack_accepts_visible_if_reference(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(_pack_with_visible_if()), encoding="utf-8")

    pack = load_domain_pack(path)

    assert pack.questions[1].visible_if.field_key == "club_type"
    assert pack.questions[1].visible_if.operator == "equals"
    assert pack.questions[1].visible_if.value == "house"


@pytest.mark.parametrize(
    ("visible_if", "message"),
    [
        (
            {"field_key": "unknown", "operator": "equals", "value": "house"},
            "unbekanntes",
        ),
        (
            {"field_key": "funding_source", "operator": "equals", "value": "public_grants"},
            "frühere",
        ),
        (
            {"field_key": "club_type", "operator": "equals", "value": ["house"]},
            "Einzelwert",
        ),
        (
            {"field_key": "club_type", "operator": "in", "value": "house"},
            "Liste",
        ),
        (
            {"field_key": "club_type", "operator": "is_missing", "value": "x"},
            "keinen Vergleichswert",
        ),
    ],
)
def test_load_domain_pack_rejects_invalid_visible_if(tmp_path, visible_if, message):
    data = _pack_with_visible_if()
    data["questions"][1]["visible_if"] = visible_if
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DomainPackError, match=message):
        load_domain_pack(path)


def _pack_with_visible_if():
    return {
        "domain_id": "demo",
        "version": "1.0.0",
        "name": "Demo",
        "description": "Demo",
        "default_locale": "de",
        "questions": [
            {
                "question_id": "club_type",
                "field_key": "club_type",
                "text": "Gebäudetyp?",
                "field_type": "select",
                "required": True,
                "options": [{"value": "house", "label": "Haus"}],
            },
            {
                "question_id": "funding_source",
                "field_key": "funding_source",
                "text": "Heizung?",
                "field_type": "select",
                "required": True,
                "visible_if": {
                    "field_key": "club_type",
                    "operator": "equals",
                    "value": "house",
                },
                "options": [{"value": "public_grants", "label": "Öffentliche Förderungen"}],
            },
        ],
        "rules": [],
        "recommendations": [],
    }


def test_recommendation_source_reference_is_optional_and_loaded(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(
            {
                "domain_id": "demo",
                "version": "1.0.0",
                "name": "Demo",
                "description": "Demo",
                "default_locale": "de",
                "questions": [],
                "rules": [],
                "recommendations": [
                    {
                        "recommendation_id": "with-source",
                        "priority": 1,
                        "title": "Mit Quelle",
                        "message": "Text",
                        "source_reference": "OIB-Richtlinie 6 (2019)",
                    },
                    {
                        "recommendation_id": "without-source",
                        "priority": 2,
                        "title": "Ohne Quelle",
                        "message": "Text",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    pack = load_domain_pack(path)

    assert pack.recommendations[0].source_reference == "OIB-Richtlinie 6 (2019)"
    assert pack.recommendations[1].source_reference is None


def test_load_domain_pack_bytes_hashes_the_raw_content():
    from guideos.domains.loader import DEFAULT_DOMAIN_PACK_PATH, load_domain_pack_bytes

    raw = DEFAULT_DOMAIN_PACK_PATH.read_bytes()

    pack = load_domain_pack_bytes(raw)

    assert pack.domain_id == "verein-demo"
    assert pack.content_hash == load_domain_pack().content_hash


def test_load_domain_pack_accepts_is_missing_with_null_value(tmp_path):
    data = _pack_with_visible_if()
    data["questions"][1]["visible_if"] = {
        "field_key": "club_type",
        "operator": "is_missing",
        "value": None,
    }
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    pack = load_domain_pack(path)

    assert pack.questions[1].visible_if.operator == "is_missing"


def _question(**overrides):
    question = {
        "question_id": "q",
        "field_key": "q",
        "text": "Frage?",
        "field_type": "text",
        "required": True,
    }
    question.update(overrides)
    return question


def _pack_with_questions(questions, rules=None, recommendations=None):
    return {
        "domain_id": "demo",
        "version": "1.0.0",
        "name": "Demo",
        "description": "Demo",
        "default_locale": "de",
        "questions": questions,
        "rules": rules or [],
        "recommendations": recommendations or [],
    }


def _load(tmp_path, data):
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return load_domain_pack(path)


ALL_TYPE_QUESTIONS = [
    _question(
        question_id="legal_form", field_key="legal_form", field_type="select",
        widget="radio", options=[{"value": "gmbh", "label": "GmbH"}],
    ),
    _question(
        question_id="channels", field_key="channels", field_type="multiselect",
        options=[{"value": "email", "label": "E-Mail"}, {"value": "web", "label": "Web"}],
        validation={"min_count": 1, "max_count": 2},
    ),
    _question(
        question_id="employees", field_key="employees", field_type="integer",
        validation={"minimum": 0, "maximum": 100},
    ),
    _question(
        question_id="share", field_key="share", field_type="number",
        validation={"minimum": 0, "maximum": 100},
    ),
    _question(
        question_id="name", field_key="name", field_type="text", widget="textarea",
        validation={"min_length": 2, "max_length": 80, "pattern": "^[A-Za-zäöüß ]+$"},
    ),
    _question(question_id="active", field_key="active", field_type="boolean"),
    _question(
        question_id="founded", field_key="founded", field_type="date",
        validation={"min_date": "1900-01-01", "max_date": "2026-12-31"},
    ),
]


def test_load_domain_pack_accepts_all_field_types_with_widgets_and_validation(tmp_path):
    pack = _load(tmp_path, _pack_with_questions(ALL_TYPE_QUESTIONS))

    types = [question.field_type for question in pack.questions]
    assert types == ["select", "multiselect", "integer", "number", "text", "boolean", "date"]
    assert pack.questions[0].widget == "radio"
    assert pack.questions[1].widget is None
    assert pack.questions[1].validation.max_count == 2
    assert pack.questions[4].widget == "textarea"
    assert pack.questions[4].validation.pattern == "^[A-Za-zäöüß ]+$"
    assert pack.questions[6].validation.min_date == "1900-01-01"


def test_pre_existing_bundled_packs_load_unchanged_without_widgets():
    from guideos.domains.catalog import list_domain_versions, load_domain_pack_ref

    refs = [
        ref
        for ref in list_domain_versions()
        if ref.source == "bundled" and ref.domain_id == "verein-demo"
    ]
    assert refs
    for ref in refs:
        pack = load_domain_pack_ref(ref)
        assert all(question.widget is None for question in pack.questions)
        assert {question.field_type for question in pack.questions} <= {
            "select", "integer", "number"
        }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"field_type": "select"}, "mindestens eine Option"),
        ({"field_type": "multiselect", "options": []}, "mindestens eine Option"),
        ({"field_type": "integer", "options": [{"value": "a", "label": "A"}]}, "keine Optionen"),
        ({"field_type": "integer", "widget": "radio"}, "kein Widget"),
        (
            {"field_type": "select", "widget": "textarea",
             "options": [{"value": "a", "label": "A"}]},
            "kein Widget",
        ),
        ({"field_type": "text", "validation": {"minimum": 1}}, "keine Validierung minimum"),
        ({"field_type": "integer", "validation": {"min_length": 1}}, "keine Validierung min_length"),
        (
            {"field_type": "select", "validation": {"max_count": 1},
             "options": [{"value": "a", "label": "A"}]},
            "keine Validierung max_count",
        ),
        ({"field_type": "date", "validation": {"pattern": "x"}}, "keine Validierung pattern"),
        ({"field_type": "integer", "validation": {"minimum": 10, "maximum": 5}}, "Untergrenze"),
        ({"field_type": "text", "validation": {"min_length": 5, "max_length": 2}}, "Untergrenze"),
        (
            {"field_type": "multiselect", "validation": {"min_count": 3, "max_count": 1},
             "options": [{"value": "a", "label": "A"}]},
            "Untergrenze",
        ),
        (
            {"field_type": "date", "validation": {"min_date": "2020-01-01", "max_date": "2019-01-01"}},
            "Untergrenze",
        ),
        ({"field_type": "text", "validation": {"pattern": "("}}, "ungültiges pattern"),
        ({"field_type": "date", "validation": {"min_date": "1.1.2020"}}, "JJJJ-MM-TT"),
    ],
)
def test_load_domain_pack_rejects_type_mismatches(tmp_path, overrides, message):
    data = _pack_with_questions([_question(**overrides)])

    with pytest.raises(DomainPackError, match=message):
        _load(tmp_path, data)


def _rule(**overrides):
    rule = {
        "rule_id": "r",
        "field_key": "q",
        "operator": "equals",
        "value": "x",
        "reason_code": "R",
    }
    rule.update(overrides)
    return rule


def _typed_pack(field_type, *, rule=None, visible_if=None, options=None):
    first = _question(
        question_id="first", field_key="first", field_type=field_type,
        options=options or ([{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
                            if field_type in ("select", "multiselect") else []),
    )
    second = _question(question_id="second", field_key="second", field_type="text")
    if visible_if is not None:
        second["visible_if"] = {"field_key": "first", **visible_if}
    rules = []
    if rule is not None:
        rules.append(_rule(**{"field_key": "first", **rule}))
    return _pack_with_questions([first, second], rules=rules)


@pytest.mark.parametrize(
    ("field_type", "rule", "visible_if", "message"),
    [
        ("multiselect", None, {"operator": "equals", "value": "a"}, "nur für Einzelwert"),
        ("multiselect", {"operator": "in", "value": ["a"]}, None, "nur für Einzelwert"),
        ("select", None, {"operator": "contains", "value": "a"}, "nur für multiselect"),
        ("multiselect", None, {"operator": "contains", "value": ["a"]}, "Einzelwert"),
        ("multiselect", {"operator": "contains_any", "value": "a"}, None, "Liste"),
        ("select", {"operator": "lt", "value": 1}, None, "nur für integer, number oder date"),
        ("text", {"operator": "gte", "value": 1}, None, "nur für integer, number oder date"),
        ("boolean", {"operator": "lt", "value": 1}, None, "nur für integer, number oder date"),
        ("integer", {"operator": "gte", "value": "abc"}, None, "Zahl"),
        ("date", {"operator": "lt", "value": "2020"}, None, "JJJJ-MM-TT"),
        (
            "text",
            {"operator": "gte", "value": 1, "field_key": "second", "divisor_field_key": "first"},
            None,
            "numerisch",
        ),
        ("boolean", None, {"operator": "equals", "value": "ja"}, "true oder false"),
        ("boolean", {"operator": "equals", "value": 1}, None, "true oder false"),
    ],
)
def test_load_domain_pack_rejects_operator_field_type_mismatch(
    tmp_path, field_type, rule, visible_if, message
):
    data = _typed_pack(field_type, rule=rule, visible_if=visible_if)

    with pytest.raises(DomainPackError, match=message):
        _load(tmp_path, data)


def test_load_domain_pack_accepts_contains_operators_and_typed_rules(tmp_path):
    data = _typed_pack(
        "multiselect",
        rule={"operator": "contains_any", "value": ["a", "b"]},
        visible_if={"operator": "contains", "value": "a"},
    )
    data["questions"].append(
        _question(question_id="founded", field_key="founded", field_type="date")
    )
    data["questions"].append(
        _question(question_id="active", field_key="active", field_type="boolean")
    )
    data["rules"].append(
        _rule(rule_id="old", field_key="founded", operator="lt", value="2000-01-01")
    )
    data["rules"].append(_rule(rule_id="yes", field_key="active", operator="equals", value=True))

    pack = _load(tmp_path, data)

    assert pack.questions[1].visible_if.operator == "contains"
    assert [rule.operator for rule in pack.rules] == ["contains_any", "lt", "equals"]


def test_web_sources_are_optional_and_loaded(tmp_path):
    plain = _load(tmp_path, _pack_with_questions([_question()]))
    data = _pack_with_questions([_question()])
    data["web_sources"] = {"enabled": True, "allowed_domains": ["klimaaktiv.at", "oib.or.at"]}

    configured = _load(tmp_path, data)

    assert plain.web_sources is None
    assert configured.web_sources.enabled is True
    assert configured.web_sources.allowed_domains == ["klimaaktiv.at", "oib.or.at"]


@pytest.mark.parametrize(
    "domain",
    ["https://klimaaktiv.at", "klimaaktiv.at/heizung", "Klimaaktiv.at", "klimaaktiv", " ", ".at"],
)
def test_web_sources_reject_entries_that_are_not_plain_host_names(tmp_path, domain):
    data = _pack_with_questions([_question()])
    data["web_sources"] = {"enabled": True, "allowed_domains": [domain]}

    with pytest.raises(DomainPackError, match="allowed_domains"):
        _load(tmp_path, data)


def test_advisory_guidance_is_optional_and_loaded(tmp_path):
    plain = _load(tmp_path, _pack_with_questions([_question()]))
    data = _pack_with_questions([_question()])
    data["advisory_guidance"] = "Der Kontext ist Österreich."

    assert plain.advisory_guidance is None
    assert _load(tmp_path, data).advisory_guidance == "Der Kontext ist Österreich."
    with pytest.raises(DomainPackError):
        _load(tmp_path, {**data, "advisory_guidance": ""})


def test_legacy_rule_score_is_read_and_ignored(tmp_path):
    from guideos.domains.catalog import pack_draft_payload
    from tests.pack_fixtures import DEMO_PACK_PATH

    # Older pack versions carry a per-rule score; it is accepted, dropped and never serialized again.
    with_score = _load(tmp_path, _pack_with_questions([_question()], rules=[_rule(score=3)]))
    without_score = _load(tmp_path, _pack_with_questions([_question()], rules=[_rule()]))

    assert "score" not in with_score.rules[0].model_dump()
    assert with_score.rules == without_score.rules
    assert "score" not in pack_draft_payload(with_score)["rules"][0]
    # The bundled demo pack is written without the key.
    raw = json.loads(DEMO_PACK_PATH.read_text(encoding="utf-8"))
    assert all("score" not in rule for rule in raw["rules"])


def test_tools_block_is_optional_and_validated():
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    base = {
        "domain_id": "tools-demo", "version": "1.0.0", "name": "Tools", "description": "Test",
        "default_locale": "de",
        "questions": [{"question_id": "q", "field_key": "q", "text": "Frage?", "field_type": "boolean",
                       "required": True}],
        "rules": [], "recommendations": [],
    }
    without = validate_domain_pack_data(base)
    assert without.tools is None
    assert "tools" not in pack_draft_payload(without)

    with_tools = validate_domain_pack_data({**base, "tools": {"enabled": True}})
    assert with_tools.tools.enabled is True
    assert with_tools.tools.dataset_tool == "import_dataset"
    assert with_tools.tools.delete_tool == "delete_dataset"
    assert with_tools.tools.max_calls_per_message == 6
    assert with_tools.tools.max_seconds_per_message == 90
    assert pack_draft_payload(with_tools)["tools"]["enabled"] is True

    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**base, "tools": {"enabled": True, "max_calls_per_message": 11}})
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**base, "tools": {"enabled": True, "dataset_tool": ""}})


def test_tools_after_import_settings_are_optional_and_validated():
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    base = {
        "domain_id": "tools-demo", "version": "1.0.0", "name": "Tools", "description": "Test",
        "default_locale": "de",
        "questions": [{"question_id": "q", "field_key": "q", "text": "Frage?", "field_type": "boolean",
                       "required": True}],
        "rules": [], "recommendations": [],
    }
    plain = validate_domain_pack_data({**base, "tools": {"enabled": True}})
    assert plain.tools.after_import is None
    assert "after_import" not in pack_draft_payload(plain)["tools"]

    with_analysis = validate_domain_pack_data({**base, "tools": {
        "enabled": True,
        "after_import": {"tool": "analyze_dataset", "arguments": {"threshold_kw": 10},
                         "note": "Hinweis."},
    }})
    assert with_analysis.tools.after_import.tool == "analyze_dataset"
    assert with_analysis.tools.after_import.arguments == {"threshold_kw": 10}
    assert pack_draft_payload(with_analysis)["tools"]["after_import"]["note"] == "Hinweis."
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**base, "tools": {"enabled": True, "after_import": {"tool": ""}}})
    with pytest.raises(DomainPackError):
        validate_domain_pack_data({**base, "tools": {"enabled": True, "after_import": {
            "tool": "x", "arguments": {"dataset_id": "nein"}}}})


def _file_pack(upload=None, *, tools=None, questions=None):
    """A pack with one file question (plus a boolean one) and a tools block."""
    file_question = _question(
        question_id="dataset_file", field_key="dataset_file", field_type="file", required=False,
        upload=upload if upload is not None else {},
    )
    data = _pack_with_questions(questions or [_question(field_type="boolean"), file_question])
    data["tools"] = {"enabled": True} if tools is None else tools
    return data


def test_file_question_loads_with_upload_defaults(tmp_path):
    from guideos.domains.catalog import pack_draft_payload

    pack = _load(tmp_path, _file_pack())

    question = pack.questions[1]
    assert question.field_type == "file"
    assert question.upload.tool is None
    assert question.upload.arguments == {}
    assert question.upload.extensions == ["csv", "txt"]
    assert question.upload.max_bytes == 64 * 1024 * 1024
    assert question.upload.use_in_advisory is True
    payload = pack_draft_payload(pack)
    assert "upload" not in payload["questions"][0]
    assert payload["questions"][1]["upload"]["use_in_advisory"] is True


def test_file_question_upload_settings_are_loaded(tmp_path):
    pack = _load(tmp_path, _file_pack({
        "tool": "import_dataset", "arguments": {"direction": "export"},
        "extensions": ["CSV"], "max_bytes": 1024, "use_in_advisory": False,
    }))

    upload = pack.questions[1].upload
    assert upload.tool == "import_dataset"
    assert upload.arguments == {"direction": "export"}
    assert upload.extensions == ["csv"]
    assert upload.max_bytes == 1024
    assert upload.use_in_advisory is False


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_file_pack(questions=[_question(field_type="file", required=False)]), "benötigt.*upload"),
        (_file_pack(questions=[_question(field_type="text", upload={})]), "erlaubt.*upload"),
        (_file_pack({"arguments": {"owner": "x"}}), "owner"),
        (_file_pack({"arguments": {"content": "x"}}), "content"),
        (_file_pack({"extensions": []}), "ungültig"),
        (_file_pack({"extensions": [".csv"]}), "Endung"),
        (_file_pack({"max_bytes": 0}), "ungültig"),
        (_file_pack(questions=[_question(field_type="file", widget="input", upload={})]), "kein Widget"),
        (_file_pack(questions=[_question(field_type="file", upload={},
                                         options=[{"value": "a", "label": "A"}])]), "keine Optionen"),
        (_file_pack(questions=[_question(field_type="file", upload={},
                                         validation={"minimum": 1})]), "keine Validierung"),
    ],
)
def test_file_question_rejects_invalid_definitions(tmp_path, data, message):
    with pytest.raises(DomainPackError, match=message):
        _load(tmp_path, data)


def test_file_question_without_tools_block_is_rejected(tmp_path):
    data = _file_pack()
    del data["tools"]

    with pytest.raises(DomainPackError, match="tools"):
        _load(tmp_path, data)

    # The block is needed for the dataset tools; whether the model may call
    # tools in the advisory (enabled) is a separate matter.
    pack = _load(tmp_path, _file_pack(tools={"enabled": False, "dataset_tool": "x"}))
    assert pack.tools.enabled is False


def test_file_questions_allow_only_one_advisory_dataset(tmp_path):
    first = _question(question_id="a", field_key="a", field_type="file", required=False, upload={})
    second = _question(question_id="b", field_key="b", field_type="file", required=False, upload={})
    with pytest.raises(DomainPackError, match="use_in_advisory"):
        _load(tmp_path, _file_pack(questions=[first, second]))

    second["upload"] = {"use_in_advisory": False}
    pack = _load(tmp_path, _file_pack(questions=[first, second]))
    assert [question.upload.use_in_advisory for question in pack.questions] == [True, False]


def test_file_questions_accept_only_is_missing_conditions(tmp_path):
    file_question = _question(question_id="first", field_key="first", field_type="file",
                              required=False, upload={})
    second = _question(question_id="second", field_key="second", field_type="text",
                       visible_if={"field_key": "first", "operator": "is_missing"})
    rule = _rule(field_key="first", operator="is_missing", value=None)
    data = _file_pack(questions=[file_question, second])
    data["rules"] = [rule]
    pack = _load(tmp_path, data)
    assert pack.rules[0].operator == "is_missing"

    second["visible_if"] = {"field_key": "first", "operator": "equals", "value": "x"}
    with pytest.raises(DomainPackError, match="nur is_missing"):
        _load(tmp_path, _file_pack(questions=[file_question, second]))

    data = _file_pack(questions=[file_question])
    data["rules"] = [_rule(field_key="first", operator="lt", value=1)]
    with pytest.raises(DomainPackError, match="nur is_missing"):
        _load(tmp_path, data)


def _analysis_pack(tools):
    """A pack with a boolean question, a number question and an advisory file
    question, for derivations and metric rules."""
    questions = [
        _question(question_id="dataset_file", field_key="dataset_file", field_type="file", required=False,
                  upload={}),
        _question(question_id="pv", field_key="pv", field_type="boolean"),
        _question(question_id="kwh", field_key="kwh", field_type="number"),
    ]
    data = _pack_with_questions(questions)
    data["tools"] = tools
    return data


def test_after_import_accepts_a_list_of_steps_and_keeps_the_single_shape(tmp_path):
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    single = validate_domain_pack_data(_analysis_pack({
        "enabled": True, "after_import": {"tool": "analyze_dataset", "arguments": {"threshold_kw": 10}},
    }))
    assert [step.tool for step in single.tools.analysis_steps()] == ["analyze_dataset"]
    assert pack_draft_payload(single)["tools"]["after_import"]["tool"] == "analyze_dataset"
    assert "derive" not in pack_draft_payload(single)["tools"]

    listed = validate_domain_pack_data(_analysis_pack({
        "enabled": True,
        "after_import": [{"tool": "analyze_tiers", "note": "Stufen."},
                         {"tool": "analyze_characteristics"}],
    }))
    assert [step.tool for step in listed.tools.analysis_steps()] == [
        "analyze_tiers", "analyze_characteristics"]
    assert isinstance(pack_draft_payload(listed)["tools"]["after_import"], list)
    assert validate_domain_pack_data(_analysis_pack({"enabled": True})).tools.analysis_steps() == []
    with pytest.raises(DomainPackError, match="mehrfach"):
        validate_domain_pack_data(_analysis_pack({
            "enabled": True, "after_import": [{"tool": "a"}, {"tool": "a"}]}))


def test_derivations_are_validated_against_questions_and_analysis_tools():
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    tools = {
        "enabled": True,
        "after_import": [{"tool": "analyze_characteristics"}],
        "derive": [
            {"field_key": "kwh", "tool": "analyze_characteristics", "pointer": "/characteristics/energy/annual_kwh",
             "decimals": 0, "note": "Aus zwölf Monaten."},
            {"field_key": "pv", "tool": "analyze_characteristics", "pointer": "/characteristics/pv/hint",
             "map": {"medium": True}, "mode": "propose", "confidence_pointer": "/characteristics/pv/hint",
             "when": {"pointer": "/characteristics/pv/hint", "operator": "in", "value": ["medium", "high"]}},
            {"field_key": "pv", "tool": "import_dataset", "value": False},
        ],
    }
    pack = validate_domain_pack_data(_analysis_pack(tools))
    first, second, third = pack.tools.derive
    assert first.mode == "answer" and first.decimals == 0 and first.pointer.startswith("/")
    assert second.mode == "propose" and second.map == {"medium": True} and second.when.operator == "in"
    assert third.pointer is None and third.value is False
    assert pack_draft_payload(pack)["tools"]["derive"][2] == {"field_key": "pv", "tool": "import_dataset",
                                                               "value": False, "mode": "answer"}

    def rejects(derivation, message):
        with pytest.raises(DomainPackError, match=message):
            validate_domain_pack_data(_analysis_pack({**tools, "derive": [derivation]}))

    rejects({"field_key": "nope", "tool": "import_dataset", "value": 1}, "unbekanntes Feld")
    rejects({"field_key": "dataset_file", "tool": "import_dataset", "value": 1}, "Datei-Fragen")
    rejects({"field_key": "kwh", "tool": "analyze_dataset", "pointer": "/x"}, "weder dataset_tool")
    rejects({"field_key": "kwh", "tool": "import_dataset"}, "ungültig")
    rejects({"field_key": "kwh", "tool": "import_dataset", "pointer": "/x", "value": 1}, "ungültig")
    rejects({"field_key": "kwh", "tool": "import_dataset", "pointer": "x"}, "ungültig")
    rejects({"field_key": "kwh", "tool": "import_dataset", "pointer": "/x",
             "when": {"pointer": "/y", "operator": "in", "value": 1}}, "eine Liste")
    rejects({"field_key": "kwh", "tool": "import_dataset", "pointer": "/x",
             "when": {"pointer": "/y", "operator": "gte"}}, "Einzelwert")


def test_metric_rules_compare_tool_results_of_the_first_analysis():
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    data = _analysis_pack({"enabled": True, "after_import": [{"tool": "analyze_tiers"}]})
    data["rules"] = [_rule(rule_id="peaks", field_key="dataset_file", operator="gte", value=1,
                           metric={"tool": "analyze_tiers", "pointer": "/totals/months_above"})]
    data["recommendations"] = [{"recommendation_id": "rec", "priority": 1, "title": "T", "message": "M"}]
    data["rules"][0]["recommendation_ids"] = ["rec"]
    pack = validate_domain_pack_data(data)
    assert pack.rules[0].metric.tool == "analyze_tiers"
    assert pack_draft_payload(pack)["rules"][0]["metric"] == {"tool": "analyze_tiers",
                                                               "pointer": "/totals/months_above"}
    plain = validate_domain_pack_data(_analysis_pack({"enabled": True}))
    assert "metric" not in pack_draft_payload(plain)["rules"][0] if plain.rules else True

    def rejects(rule, message, tools=None):
        broken = {**data, "rules": [{**data["rules"][0], **rule}]}
        if tools is not None:
            broken["tools"] = tools
        with pytest.raises(DomainPackError, match=message):
            validate_domain_pack_data(broken)

    rejects({"field_key": "kwh"}, "Datei-Frage")
    rejects({"metric": {"tool": "import_dataset", "pointer": "/x"}}, "nicht in")
    rejects({"operator": "is_missing", "value": None}, "nur equals, in, lt oder gte")
    rejects({"operator": "in", "value": 1}, "eine Liste")
    rejects({"metric": {"tool": "analyze_tiers", "pointer": "x"}}, "ungültig")
    data_without_tools = {**data, "tools": None}
    with pytest.raises(DomainPackError):
        validate_domain_pack_data(data_without_tools)


def _figures_pack(recommendation=None, tools=None):
    """A pack whose metric rule recommendation carries figures from the first analysis."""
    data = _analysis_pack(tools if tools is not None else {
        "enabled": True, "after_import": [{"tool": "analyze_tiers"}]})
    data["rules"] = [_rule(rule_id="peaks", field_key="dataset_file", operator="gte", value=1,
                           metric={"tool": "analyze_tiers", "pointer": "/totals/months_above"},
                           recommendation_ids=["rec"])]
    data["recommendations"] = [recommendation or {
        "recommendation_id": "rec", "priority": 1, "title": "Spitzen senken",
        "message": "In mindestens einem Monat liegt die Spitze über der Schwelle.",
        "figures": {
            "months_above": {"tool": "analyze_tiers", "pointer": "/totals/months_above", "decimals": 0},
            "months_total": {"tool": "analyze_tiers", "pointer": "/totals/months_total"},
        },
        "message_with_figures": "In {months_above} von {months_total} Monaten liegt die Spitze über der Schwelle.",
    }]
    return data


def test_recommendation_figures_need_a_template_and_known_tools():
    from guideos.domains.loader import validate_domain_pack_data

    pack = validate_domain_pack_data(_figures_pack())
    recommendation = pack.recommendations[0]
    assert recommendation.figures["months_above"].tool == "analyze_tiers"
    assert recommendation.figures["months_above"].decimals == 0
    assert recommendation.figures["months_total"].decimals is None
    assert recommendation.message_with_figures.startswith("In {months_above}")
    plain = validate_domain_pack_data(_analysis_pack({"enabled": True}))
    assert all(item.figures is None and item.message_with_figures is None for item in plain.recommendations)

    base = _figures_pack()["recommendations"][0]

    def rejects(recommendation, message, tools=None):
        with pytest.raises(DomainPackError, match=message):
            validate_domain_pack_data(_figures_pack({**base, **recommendation}, tools=tools))

    rejects({"message_with_figures": None}, "gemeinsam")
    rejects({"figures": None}, "gemeinsam")
    rejects({"figures": {"months_above": {"tool": "analyze_dataset", "pointer": "/totals/months_above"},
                         "months_total": base["figures"]["months_total"]}}, "weder dataset_tool")
    rejects({"figures": {"months_above": {"tool": "analyze_tiers", "pointer": "totals"},
                         "months_total": base["figures"]["months_total"]}}, "ungültig")
    rejects({"figures": {"months_above": {"tool": "analyze_tiers", "pointer": "/t", "decimals": 7},
                         "months_total": base["figures"]["months_total"]}}, "ungültig")
    without_tools = _figures_pack()
    without_tools["tools"] = None
    without_tools["rules"] = []
    with pytest.raises(DomainPackError, match="Pack-Block tools"):
        validate_domain_pack_data(without_tools)


def test_recommendation_template_placeholders_must_match_declared_figures():
    from guideos.domains.loader import validate_domain_pack_data

    base = _figures_pack()["recommendations"][0]

    def rejects(recommendation, message):
        with pytest.raises(DomainPackError, match=message):
            validate_domain_pack_data(_figures_pack({**base, **recommendation}))

    rejects({"message_with_figures": "In {months_above} von {monate} Monaten."}, "Platzhalter.*monate")
    rejects({"message_with_figures": "In {months_above} Monaten."}, "months_total.*nicht verwendet")
    rejects({"figures": {"Months": {"tool": "analyze_tiers", "pointer": "/t"}},
             "message_with_figures": "{Months}"}, "ungültig")
    # Braces in the generic message are plain text, not placeholders.
    pack = validate_domain_pack_data(_figures_pack({**base, "message": "Allgemein {irgendwas}."}))
    assert pack.recommendations[0].message == "Allgemein {irgendwas}."


def test_tools_at_completion_steps_take_arguments_from_answers():
    from guideos.domains.catalog import pack_draft_payload
    from guideos.domains.loader import validate_domain_pack_data

    data = _analysis_pack({
        "enabled": True,
        "after_import": [{"tool": "analyze_tiers"}],
        "at_completion": [{
            "tool": "calculate_costs", "arguments": {"include_flex": False},
            "arguments_from_answers": {"comparison_tariff_ids": {
                "field_key": "pv", "map": {"true": ["region-a-2025", "region-a-2026"]},
                "default": ["region-b-2025"]}},
            "note": "Kosten nach Region.",
        }],
    })
    data["rules"] = [_rule(rule_id="costs", field_key="dataset_file", operator="gte", value=1,
                           metric={"tool": "calculate_costs", "pointer": "/totals/months_included"},
                           recommendation_ids=["rec"])]
    data["recommendations"] = [{
        "recommendation_id": "rec", "priority": 1, "title": "T", "message": "Allgemein.",
        "figures": {"eur": {"tool": "calculate_costs", "pointer": "/totals/total_eur", "decimals": 2}},
        "message_with_figures": "{eur} €.",
    }]
    pack = validate_domain_pack_data(data)
    assert [step.tool for step in pack.tools.analysis_steps()] == ["analyze_tiers"]
    [step] = pack.tools.completion_steps()
    assert step.tool == "calculate_costs" and step.arguments == {"include_flex": False}
    spec = step.arguments_from_answers["comparison_tariff_ids"]
    assert (spec.field_key, spec.default) == ("pv", ["region-b-2025"])
    assert spec.map == {"true": ["region-a-2025", "region-a-2026"]}
    payload = pack_draft_payload(pack)
    assert payload["tools"]["at_completion"][0]["arguments_from_answers"]["comparison_tariff_ids"]["field_key"] == "pv"
    assert "arguments_from_answers" not in payload["tools"]["after_import"][0]
    plain = validate_domain_pack_data(_analysis_pack({"enabled": True, "after_import": [{"tool": "a"}]}))
    assert plain.tools.completion_steps() == [] and "at_completion" not in pack_draft_payload(plain)["tools"]

    def rejects(step, message):
        broken = {**data, "tools": {**data["tools"], "at_completion": [step]}}
        with pytest.raises(DomainPackError, match=message):
            validate_domain_pack_data(broken)

    rejects({"tool": "analyze_tiers"}, "mehrfach")
    rejects({"tool": "calculate_costs", "arguments_from_answers": {"x": {"field_key": "nope"}}}, "unbekanntes Feld")
    rejects({"tool": "calculate_costs", "arguments_from_answers": {"x": {"field_key": "dataset_file"}}}, "Datei-Frage")
    rejects({"tool": "calculate_costs", "arguments_from_answers": {"owner": {"field_key": "pv"}}}, "ungültig")
    rejects({"tool": "calculate_costs", "arguments": {"a": 1},
             "arguments_from_answers": {"a": {"field_key": "pv"}}}, "ungültig")
