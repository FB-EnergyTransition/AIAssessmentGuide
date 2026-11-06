"""Export and import of domain-pack questions as a SOyA structure.

SOyA (Semantic Overlay Architecture, https://github.com/OwnYourData/soya) describes
a dataset as one base with typed attributes plus overlays. GuideOS maps its questions
to one base, an OverlayValidation (cardinality, valueOption, valueRange, length,
pattern), an OverlayForm (JSON Forms schema and UI) and an OverlayAnnotation
(labels and comments per locale). Rules and recommendations stay GuideOS-specific.
GuideOS details without a SOyA counterpart travel under ``options.guideos`` of the
form controls; the import works without them and falls back to defaults.
"""

import re
from typing import Any

from pydantic import ValidationError

from guideos.domains.loader import DomainPackError, validate_domain_pack_data
from guideos.domains.models import DomainPack, QuestionDefinition

SOYA_TYPES: dict[str, str] = {
    "select": "String",
    "multiselect": "String",
    "integer": "Integer",
    "number": "Float",
    "text": "String",
    "boolean": "Boolean",
    "date": "Date",
    # SOyA has no file type: the upload block travels under options.guideos.
    "file": "String",
}
_SCALAR_TYPES_FROM_SOYA: dict[str, str] = {
    "String": "text",
    "Integer": "integer",
    "Float": "number",
    "Decimal": "number",
    "Boolean": "boolean",
    "Date": "date",
}


class SoyaError(Exception):
    """A SOyA structure that GuideOS cannot map; message in German."""


# --- export -----------------------------------------------------------------


def pack_to_soya(pack: DomainPack) -> dict[str, Any]:
    base_name = _base_name(pack.domain_id)
    locale = pack.default_locale
    attributes = {q.field_key: SOYA_TYPES[q.field_type] for q in pack.questions}
    validation = {q.field_key: _validation_attribute(q) for q in pack.questions}
    annotation: dict[str, Any] = {}
    for question in pack.questions:
        entry: dict[str, Any] = {"label": {locale: question.text}}
        if question.help_text:
            entry["comment"] = {locale: question.help_text}
        annotation[question.field_key] = entry

    return {
        "meta": {"name": base_name},
        "content": {
            "bases": [{"name": base_name, "attributes": attributes}],
            "overlays": [
                {
                    "type": "OverlayValidation",
                    "base": base_name,
                    "name": f"{base_name}ValidationOverlay",
                    "attributes": validation,
                },
                {
                    "type": "OverlayForm",
                    "base": base_name,
                    "name": f"{base_name}FormOverlay",
                    "schema": {
                        "type": "object",
                        "properties": {
                            q.field_key: _schema_property(q) for q in pack.questions
                        },
                        "required": [
                            q.field_key
                            for q in pack.questions
                            if q.required and not q.allow_unknown
                        ],
                    },
                    "ui": {
                        "type": "VerticalLayout",
                        "elements": [_control(q) for q in pack.questions],
                    },
                },
                {
                    "type": "OverlayAnnotation",
                    "base": base_name,
                    "name": f"{base_name}AnnotationOverlay",
                    "class": {"label": {locale: pack.name}},
                    "attributes": annotation,
                },
            ],
        },
    }


def _base_name(domain_id: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", domain_id)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "GuideOS"


def _validation_attribute(question: QuestionDefinition) -> dict[str, Any]:
    minimum = "1" if question.required and not question.allow_unknown else "0"
    maximum = "*" if question.field_type == "multiselect" else "1"
    entry: dict[str, Any] = {"cardinality": f"{minimum}..{maximum}"}
    validation = question.validation
    if question.field_type in ("integer", "number"):
        if validation.minimum is not None or validation.maximum is not None:
            entry["valueRange"] = _range(validation.minimum, validation.maximum)
    elif question.field_type == "date":
        if validation.min_date is not None or validation.max_date is not None:
            entry["valueRange"] = _range(validation.min_date, validation.max_date)
    elif question.field_type == "text":
        if validation.min_length is not None or validation.max_length is not None:
            entry["length"] = _range(validation.min_length, validation.max_length)
        if validation.pattern is not None:
            entry["pattern"] = validation.pattern
    if question.options:
        entry["valueOption"] = [option.value for option in question.options]
    return entry


def _range(lower: Any, upper: Any) -> str:
    return f"[{_bound(lower)}..{_bound(upper)}]"


def _bound(value: Any) -> str:
    if value is None:
        return "*"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _schema_property(question: QuestionDefinition) -> dict[str, Any]:
    validation = question.validation
    one_of = [{"const": o.value, "title": o.label} for o in question.options]
    field_type = question.field_type
    if field_type == "select":
        return {"type": "string", "oneOf": one_of}
    if field_type == "multiselect":
        prop: dict[str, Any] = {"type": "array", "uniqueItems": True}
        if validation.min_count is not None:
            prop["minItems"] = validation.min_count
        if validation.max_count is not None:
            prop["maxItems"] = validation.max_count
        prop["items"] = {"oneOf": one_of}
        return prop
    if field_type in ("integer", "number"):
        prop = {"type": field_type}
        if validation.minimum is not None:
            prop["minimum"] = _number(validation.minimum)
        if validation.maximum is not None:
            prop["maximum"] = _number(validation.maximum)
        return prop
    if field_type == "text":
        prop = {"type": "string"}
        if validation.min_length is not None:
            prop["minLength"] = validation.min_length
        if validation.max_length is not None:
            prop["maxLength"] = validation.max_length
        if validation.pattern is not None:
            prop["pattern"] = validation.pattern
        return prop
    if field_type == "boolean":
        return {"type": "boolean"}
    if field_type == "file":
        return {"type": "string", "format": "data-url"}
    return {"type": "string", "format": "date"}


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _control(question: QuestionDefinition) -> dict[str, Any]:
    control: dict[str, Any] = {
        "type": "Control",
        "scope": f"#/properties/{question.field_key}",
        "label": question.text,
    }
    options: dict[str, Any] = {}
    if question.field_type == "select" and question.widget == "radio":
        options["format"] = "radio"
    if question.field_type == "text" and question.widget == "textarea":
        options["multi"] = True
    guideos: dict[str, Any] = {
        "question_id": question.question_id,
        "required": question.required,
        "allow_unknown": question.allow_unknown,
    }
    if question.widget is not None:
        guideos["widget"] = question.widget
    if question.field_type == "file" and question.upload is not None:
        guideos["field_type"] = "file"
        guideos["upload"] = question.upload.model_dump(mode="json")
    condition = question.visible_if
    if condition is not None:
        if condition.operator == "is_missing":
            guideos["visible_if"] = {"field_key": condition.field_key, "operator": "is_missing"}
        else:
            control["rule"] = {
                "effect": "SHOW",
                "condition": {
                    "scope": f"#/properties/{condition.field_key}",
                    "schema": _condition_schema(condition.operator, condition.value),
                },
            }
    options["guideos"] = guideos
    control["options"] = options
    return control


def _condition_schema(operator: str, value: Any) -> dict[str, Any]:
    if operator == "equals":
        return {"const": value}
    if operator == "in":
        return {"enum": list(value)}
    if operator == "contains":
        return {"contains": {"const": value}}
    return {"contains": {"enum": list(value)}}


# --- import -----------------------------------------------------------------


def soya_to_questions(document: dict[str, Any], *, locale: str = "de") -> list[QuestionDefinition]:
    content = document.get("content") if isinstance(document, dict) else None
    if not isinstance(content, dict):
        raise SoyaError("Die SOyA-Struktur enthält keinen content-Abschnitt.")
    bases = content.get("bases") or []
    if not bases:
        raise SoyaError("Die SOyA-Struktur enthält keine Basis.")
    if len(bases) > 1:
        raise SoyaError("Mehrere Basen werden nicht unterstützt.")
    base = bases[0]
    base_name = str(base.get("name", ""))
    attributes = base.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise SoyaError("Die Basis enthält keine Attribute.")

    overlays = [o for o in content.get("overlays") or [] if _overlay_matches(o, base_name)]
    validation = _first_overlay(overlays, "OverlayValidation")
    form = _first_overlay(overlays, "OverlayForm")
    annotation = _first_overlay(overlays, "OverlayAnnotation")
    validation_attrs = (validation or {}).get("attributes") or {}
    annotation_attrs = (annotation or {}).get("attributes") or {}
    schema = (form or {}).get("schema") or {}
    properties = schema.get("properties") or {}
    controls = _controls(form)

    ordered = [key for key in controls if key in attributes]
    ordered += [key for key in attributes if key not in ordered]
    questions = []
    for key in ordered:
        questions.append(
            _question_from_soya(
                key,
                attributes[key],
                validation_attrs.get(key) or {},
                properties.get(key) or {},
                schema.get("required") if "required" in schema and form is not None else None,
                controls.get(key) or {},
                annotation_attrs.get(key) or {},
                locale,
            )
        )
    return questions


def pack_from_soya(
    document: dict[str, Any],
    *,
    domain_id: str,
    version: str,
    name: str,
    description: str,
    default_locale: str = "de",
    base_pack: DomainPack | None = None,
) -> tuple[DomainPack, list[str]]:
    questions = soya_to_questions(document, locale=default_locale)
    field_keys = {question.field_key for question in questions}
    warnings: list[str] = []
    rules: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    if base_pack is not None:
        for rule in base_pack.rules:
            missing = [
                key for key in (rule.field_key, rule.divisor_field_key)
                if key is not None and key not in field_keys
            ]
            if missing:
                warnings.append(
                    f"Regel {rule.rule_id} wurde verworfen: Feld {missing[0]} existiert nicht mehr."
                )
                continue
            rules.append(rule.model_dump(mode="json"))
        recommendations = [item.model_dump(mode="json") for item in base_pack.recommendations]
    data = {
        "domain_id": domain_id,
        "version": version,
        "name": name,
        "description": description,
        "default_locale": default_locale,
        "questions": [question.model_dump(mode="json") for question in questions],
        "rules": rules,
        "recommendations": recommendations,
    }
    return validate_domain_pack_data(data), warnings


def _overlay_matches(overlay: Any, base_name: str) -> bool:
    if not isinstance(overlay, dict):
        return False
    base = overlay.get("base")
    if base is None or not base_name:
        return True
    base = str(base)
    return base == base_name or base.endswith("/" + base_name)


def _first_overlay(overlays: list[dict[str, Any]], overlay_type: str) -> dict[str, Any] | None:
    return next((o for o in overlays if o.get("type") == overlay_type), None)


def _controls(form: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    controls: dict[str, dict[str, Any]] = {}
    if form is None:
        return controls

    def walk(element: Any) -> None:
        if not isinstance(element, dict):
            return
        if element.get("type") == "Control" and isinstance(element.get("scope"), str):
            key = element["scope"].rsplit("/", 1)[-1]
            controls.setdefault(key, element)
        for child in element.get("elements") or []:
            walk(child)

    walk(form.get("ui"))
    return controls


def _question_from_soya(
    key: str,
    soya_type: Any,
    validation: dict[str, Any],
    prop: dict[str, Any],
    required_keys: list[str] | None,
    control: dict[str, Any],
    annotation: dict[str, Any],
    locale: str,
) -> QuestionDefinition:
    options = _options(validation, prop)
    minimum_count, maximum_count = _parse_cardinality(validation.get("cardinality"))
    multi = prop.get("type") == "array" or maximum_count is None or maximum_count > 1
    stored_type = ((control.get("options") or {}).get("guideos") or {}).get("field_type")
    if stored_type == "file":
        field_type = "file"
    elif options:
        field_type = "multiselect" if multi else "select"
    else:
        if soya_type not in _SCALAR_TYPES_FROM_SOYA:
            raise SoyaError(f"Attribut {key}: SOyA-Typ {soya_type} wird nicht unterstützt.")
        if multi:
            raise SoyaError(
                f"Attribut {key}: mehrwertige Attribute ohne Optionen werden nicht unterstützt."
            )
        field_type = _SCALAR_TYPES_FROM_SOYA[soya_type]

    control_options = control.get("options") or {}
    guideos = control_options.get("guideos") or {}
    if "required" in guideos:
        required = bool(guideos["required"])
    elif required_keys is not None:
        required = key in required_keys
    else:
        required = minimum_count >= 1
    allow_unknown = bool(guideos.get("allow_unknown", False))
    widget = guideos.get("widget")
    if widget is None:
        if control_options.get("format") == "radio":
            widget = "radio"
        elif control_options.get("multi") is True:
            widget = "textarea"

    data: dict[str, Any] = {
        "question_id": guideos.get("question_id") or key,
        "field_key": key,
        "text": _localized(annotation.get("label"), locale) or control.get("label") or key,
        "help_text": _localized(annotation.get("comment"), locale),
        "field_type": field_type,
        "widget": widget,
        "required": required,
        "allow_unknown": allow_unknown,
        "options": options,
        "validation": _validation_from_soya(key, field_type, validation, prop),
    }
    visible_if = _visible_if(key, control, guideos)
    if visible_if is not None:
        data["visible_if"] = visible_if
    if field_type == "file":
        data["upload"] = guideos.get("upload") if isinstance(guideos.get("upload"), dict) else {}
    try:
        return QuestionDefinition.model_validate(data)
    except ValidationError as exc:
        raise SoyaError(f"Attribut {key} ergibt keine gültige Frage: {exc}") from exc


def _options(validation: dict[str, Any], prop: dict[str, Any]) -> list[dict[str, str]]:
    one_of = prop.get("oneOf") or (prop.get("items") or {}).get("oneOf") or []
    titles = {
        str(item.get("const")): str(item.get("title", item.get("const")))
        for item in one_of
        if isinstance(item, dict) and "const" in item
    }
    values = validation.get("valueOption")
    if values is None:
        values = list(titles)
    return [{"value": str(value), "label": titles.get(str(value), str(value))} for value in values]


def _localized(value: Any, locale: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        chosen = value.get(locale)
        if chosen is None and value:
            chosen = next(iter(value.values()))
        value = chosen
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value not in (None, "") else None


def _validation_from_soya(
    key: str, field_type: str, validation: dict[str, Any], prop: dict[str, Any]
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if field_type in ("integer", "number", "date"):
        lower, upper = _parse_range(key, validation.get("valueRange"), field_type)
        if lower is None:
            lower = prop.get("minimum")
        if upper is None:
            upper = prop.get("maximum")
        if field_type == "date":
            data["min_date"], data["max_date"] = lower, upper
        else:
            data["minimum"], data["maximum"] = lower, upper
    elif field_type == "text":
        lower, upper = _parse_range(key, validation.get("length"), "integer")
        data["min_length"] = lower if lower is not None else prop.get("minLength")
        data["max_length"] = upper if upper is not None else prop.get("maxLength")
        data["pattern"] = validation.get("pattern") or prop.get("pattern")
    elif field_type == "multiselect":
        data["min_count"] = prop.get("minItems")
        data["max_count"] = prop.get("maxItems")
    return {name: value for name, value in data.items() if value is not None}


def _parse_cardinality(text: Any) -> tuple[int, int | None]:
    if text is None:
        return 0, 1
    match = re.fullmatch(r"\s*(\d+)\s*\.\.\s*(\d+|\*)\s*", str(text))
    if not match:
        raise SoyaError(f"Ungültige cardinality: {text}")
    upper = match.group(2)
    return int(match.group(1)), None if upper == "*" else int(upper)


def _parse_range(key: str, text: Any, field_type: str) -> tuple[Any, Any]:
    if text is None:
        return None, None
    raw = str(text).strip()
    if len(raw) < 4 or raw[0] not in "[(" or raw[-1] not in "])" or ".." not in raw:
        raise SoyaError(f"Attribut {key}: ungültiger Bereich {text}.")
    lower_text, _, upper_text = raw[1:-1].partition("..")
    lower = _range_value(lower_text.strip(), field_type)
    upper = _range_value(upper_text.strip(), field_type)
    if raw[0] == "(" and lower is not None:
        lower = _shift(key, lower, field_type, 1)
    if raw[-1] == ")" and upper is not None:
        upper = _shift(key, upper, field_type, -1)
    return lower, upper


def _range_value(text: str, field_type: str) -> Any:
    if text == "*":
        return None
    if field_type == "date":
        return text
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError as exc:
            raise SoyaError(f"Ungültiger Bereichswert: {text}") from exc


def _shift(key: str, value: Any, field_type: str, offset: int) -> Any:
    if field_type == "integer" and isinstance(value, int):
        return value + offset
    raise SoyaError(
        f"Attribut {key}: exklusive Bereichsgrenzen werden nur für ganze Zahlen unterstützt."
    )


def _visible_if(key: str, control: dict[str, Any], guideos: dict[str, Any]) -> dict[str, Any] | None:
    stored = guideos.get("visible_if")
    if isinstance(stored, dict):
        return {"field_key": stored.get("field_key"), "operator": "is_missing"}
    rule = control.get("rule")
    if not isinstance(rule, dict):
        return None
    if rule.get("effect") != "SHOW":
        raise SoyaError(f"Attribut {key}: nur SHOW-Regeln werden unterstützt.")
    condition = rule.get("condition") or {}
    scope = str(condition.get("scope", ""))
    schema = condition.get("schema") or {}
    field_key = scope.rsplit("/", 1)[-1]
    if "const" in schema:
        return {"field_key": field_key, "operator": "equals", "value": schema["const"]}
    if "enum" in schema:
        return {"field_key": field_key, "operator": "in", "value": list(schema["enum"])}
    contains = schema.get("contains")
    if isinstance(contains, dict) and "const" in contains:
        return {"field_key": field_key, "operator": "contains", "value": contains["const"]}
    if isinstance(contains, dict) and "enum" in contains:
        return {
            "field_key": field_key, "operator": "contains_any", "value": list(contains["enum"])
        }
    raise SoyaError(f"Attribut {key}: die Sichtbarkeitsregel wird nicht unterstützt.")


__all__ = [
    "DomainPackError",
    "SOYA_TYPES",
    "SoyaError",
    "pack_from_soya",
    "pack_to_soya",
    "soya_to_questions",
]
