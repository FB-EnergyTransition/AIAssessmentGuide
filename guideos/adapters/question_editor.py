"""Pure helpers for the Domain-Admin question editor (no Streamlit calls)."""

import json
import re
from datetime import date
from typing import Any

from guideos.domains.catalog import DomainCatalogError, DomainPackRef, pack_draft_payload
from guideos.domains.loader import DomainPackError, validate_domain_pack_data
from guideos.domains.models import (
    ALLOWED_VALIDATION,
    ALLOWED_WIDGETS,
    FIGURE_NAME,
    DomainPack,
    FigureSource,
    KnowledgeRecommendationSettings,
    MetricSource,
    QuestionDefinition,
    QuestionOption,
    RecommendationDefinition,
    ResultChatSettings,
    RuleDefinition,
    TopicDefinition,
    UploadDefinition,
    ValidationDefinition,
    VisibilityCondition,
)

FIELD_TYPE_LABELS: dict[str, str] = {
    "select": "Einfachauswahl",
    "multiselect": "Mehrfachauswahl",
    "integer": "Ganzzahl",
    "number": "Dezimalzahl",
    "text": "Text",
    "boolean": "Ja/Nein",
    "date": "Datum",
    "file": "Datei-Upload",
}
DEFAULT_UPLOAD_EXTENSIONS = "csv, txt"
DEFAULT_UPLOAD_MIB = 64
WIDGET_LABELS: dict[str, str] = {
    "dropdown": "Auswahlliste",
    "radio": "Optionsfelder",
    "checkboxes": "Kontrollkästchen",
    "input": "Einzeilig",
    "textarea": "Mehrzeilig",
}
WIDGETS_BY_TYPE: dict[str, list[str]] = {
    "select": ["dropdown", "radio"],
    "multiselect": ["checkboxes", "dropdown"],
    "text": ["input", "textarea"],
}
OPERATOR_LABELS: dict[str, str] = {
    "equals": "ist gleich",
    "in": "ist eines von",
    "is_missing": "ist unbekannt",
    "contains": "enthält",
    "contains_any": "enthält eines von",
}
RULE_OPERATOR_LABELS: dict[str, str] = {
    **OPERATOR_LABELS,
    "lt": "kleiner als",
    "gte": "größer oder gleich",
}
RULE_KIND_LABELS: dict[str, str] = {
    "recommend": "Pflicht-Empfehlung",
    "exclude": "Ausschluss",
    "prioritize": "Priorisierung",
}
RULE_STATUS_LABELS: dict[str, str] = {
    "draft": "Entwurf",
    "review": "In Prüfung",
    "approved": "Freigegeben",
}
RULE_ORIGIN_LABELS: dict[str, str] = {"expert": "Experte", "imported": "Übernommen"}
# Entwurf -> Prüfung -> freigegeben; zurück auf Entwurf geht immer.
RULE_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"review"},
    "review": {"approved", "draft"},
    "approved": {"draft"},
}
LIST_OPERATORS = {"in", "contains_any"}
NUMERIC_TYPES = {"integer", "number"}
CHOICE_TYPES = {"select", "multiselect"}


class EditorError(ValueError):
    """A form input that cannot become a valid question; message in German."""


def question_from_form(
    values: dict[str, Any],
    *,
    earlier_questions: list[QuestionDefinition],
) -> QuestionDefinition:
    question_id = _required_text(values.get("question_id"), "Die Frage-ID darf nicht leer sein.")
    field_key = _required_text(values.get("field_key"), "Der Feldschlüssel darf nicht leer sein.")
    text = _required_text(values.get("text"), "Der Fragetext darf nicht leer sein.")
    field_type = values["field_type"]
    widget = values.get("widget") or None
    if widget is not None and widget not in ALLOWED_WIDGETS.get(field_type, set()):
        widget = None

    options = options_from_rows(values.get("option_rows") or []) if field_type in CHOICE_TYPES else []
    if field_type in CHOICE_TYPES and not options:
        raise EditorError("Auswahlfragen benötigen mindestens eine Option.")

    visible_if = None
    visible_field_key = values.get("visible_field_key")
    if visible_field_key:
        target = next(
            (question for question in earlier_questions if question.field_key == visible_field_key),
            None,
        )
        if target is None:
            raise EditorError("Die Sichtbarkeit darf nur auf eine frühere Frage verweisen.")
        visible_if = condition_from_form(
            visible_field_key, values.get("visible_operator") or "equals",
            values.get("visible_value"), target,
        )

    data = {
        "question_id": question_id,
        "field_key": field_key,
        "text": text,
        "help_text": _optional_text(values.get("help_text")),
        "field_type": field_type,
        "widget": widget,
        "required": bool(values.get("required")),
        "allow_unknown": bool(values.get("allow_unknown")),
        "options": options,
        "validation": validation_from_form(field_type, values),
    }
    if visible_if is not None:
        data["visible_if"] = visible_if
    if field_type == "file":
        data["upload"] = upload_from_form(values)
    return QuestionDefinition.model_validate(data)


def upload_from_form(values: dict[str, Any]) -> UploadDefinition:
    """The upload block of a file question from the form: tool name, fixed
    arguments as JSON object text, extensions as comma-separated text and
    the size limit in MiB."""
    arguments_text = _optional_text(values.get("upload_arguments"))
    arguments: dict[str, Any] = {}
    if arguments_text is not None:
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError as exc:
            raise EditorError("Die festen Argumente müssen gültiges JSON sein.") from exc
        if not isinstance(arguments, dict):
            raise EditorError("Die festen Argumente müssen ein JSON-Objekt sein, zum Beispiel "
                              '{"direction": "export"}.')
    extensions = [
        part.strip().lower().lstrip(".")
        for part in str(values.get("upload_extensions") or DEFAULT_UPLOAD_EXTENSIONS).split(",")
        if part.strip()
    ]
    if not extensions:
        raise EditorError("Bitte mindestens eine erlaubte Endung angeben, zum Beispiel csv.")
    max_mib = values.get("upload_max_mib")
    try:
        max_bytes = int(float(max_mib) * 1024 * 1024) if max_mib not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise EditorError("Die Größengrenze muss eine Zahl in MiB sein.") from exc
    data: dict[str, Any] = {
        "tool": _optional_text(values.get("upload_tool")),
        "arguments": arguments,
        "extensions": extensions,
        "use_in_advisory": bool(values.get("use_in_advisory", True)),
    }
    if max_bytes is not None:
        data["max_bytes"] = max_bytes
    try:
        upload = UploadDefinition.model_validate(data)
    except ValueError as exc:
        raise EditorError(f"Die Upload-Einstellungen sind ungültig: {exc}") from exc
    reserved = {"owner", "filename", "content", "format_hint", "dataset_id"} & set(upload.arguments)
    if reserved:
        raise EditorError(
            f"Die festen Argumente dürfen {', '.join(sorted(reserved))} nicht enthalten; "
            "GuideOS setzt diese Argumente selbst."
        )
    return upload


def options_from_rows(rows: list[dict[str, Any]]) -> list[QuestionOption]:
    options: list[QuestionOption] = []
    seen: set[str] = set()
    for row in rows:
        value = _optional_text(row.get("value"))
        label = _optional_text(row.get("label"))
        if value is None and label is None:
            continue
        if value is None:
            raise EditorError("Optionswerte dürfen nicht leer sein.")
        if value in seen:
            raise EditorError(f"Optionswerte müssen eindeutig sein: {value}")
        seen.add(value)
        options.append(QuestionOption(value=value, label=label or value))
    return options


def validation_from_form(field_type: str, values: dict[str, Any]) -> ValidationDefinition:
    allowed = ALLOWED_VALIDATION.get(field_type, set())
    data: dict[str, Any] = {}
    for key in allowed:
        value = values.get(key)
        if isinstance(value, str):
            value = _optional_text(value)
        elif isinstance(value, date):
            value = value.isoformat()
        if value is not None:
            data[key] = value
    return ValidationDefinition.model_validate(data)


def condition_from_form(
    field_key: str | None,
    operator: str,
    raw_value: Any,
    target: QuestionDefinition,
) -> VisibilityCondition | None:
    if not field_key:
        return None
    if operator == "is_missing":
        return VisibilityCondition(field_key=field_key, operator="is_missing")
    value = comparison_value(operator, raw_value, target.field_type)
    return VisibilityCondition(field_key=field_key, operator=operator, value=value)


def comparison_value(operator: str, raw_value: Any, field_type: str) -> Any:
    """The typed comparison value of a condition or rule from form text;
    list operators take comma-separated values."""
    text = "" if raw_value is None else str(raw_value)
    if operator in LIST_OPERATORS:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            raise EditorError("Der Vergleichswert darf nicht leer sein.")
        return [_parse_condition_value(part, field_type) for part in parts]
    if not text.strip():
        raise EditorError("Der Vergleichswert darf nicht leer sein.")
    return _parse_condition_value(text.strip(), field_type)


NEW_PACK_VERSION = "0.1.0"


def new_domain_pack(
    domain_id: str,
    name: str,
    description: str,
    refs: list[DomainPackRef],
) -> DomainPack:
    """An empty pack for a domain that does not exist yet."""
    domain_id = domain_id.strip()
    name = name.strip()
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", domain_id):
        raise EditorError(
            "Die Domain-ID darf nur Kleinbuchstaben, Ziffern und einzelne Bindestriche "
            "enthalten, zum Beispiel sanierungs-check."
        )
    if any(ref.domain_id == domain_id for ref in refs):
        raise EditorError(
            f"Die Domain-ID {domain_id} existiert bereits. Neue Versionen entstehen im "
            "Tab Metadaten."
        )
    if not name:
        raise EditorError("Der Name des neuen Packs darf nicht leer sein.")
    return DomainPack(
        domain_id=domain_id,
        version=NEW_PACK_VERSION,
        name=name,
        description=description.strip(),
        default_locale="de",
        questions=[],
        rules=[],
        recommendations=[],
    )


def new_question(questions: list[QuestionDefinition]) -> QuestionDefinition:
    taken = {question.field_key for question in questions} | {
        question.question_id for question in questions
    }
    index = len(questions) + 1
    while f"frage_{index}" in taken:
        index += 1
    key = f"frage_{index}"
    return QuestionDefinition(
        question_id=key, field_key=key, text="Neue Frage", field_type="text", required=True
    )


def move_question(
    questions: list[QuestionDefinition], index: int, offset: int
) -> list[QuestionDefinition]:
    target = index + offset
    if index < 0 or target < 0 or index >= len(questions) or target >= len(questions):
        return list(questions)
    moved = list(questions)
    moved[index], moved[target] = moved[target], moved[index]
    return moved


def delete_question(questions: list[QuestionDefinition], index: int) -> list[QuestionDefinition]:
    return [question for position, question in enumerate(questions) if position != index]


def pack_with_questions(pack: DomainPack, questions: list[QuestionDefinition]) -> DomainPack:
    return pack.model_copy(update={"questions": list(questions), "content_hash": ""}, deep=True)


def draft_error(pack: DomainPack) -> str | None:
    """German validation error of the serialized draft, or None when it is valid."""
    try:
        validate_domain_pack_data(pack_draft_payload(pack))
    except DomainPackError as exc:
        return str(exc)
    return None


def assert_version_editable(domain_id: str, version: str, refs: list[DomainPackRef]) -> None:
    for ref in refs:
        if ref.domain_id != domain_id or ref.version != version:
            continue
        if ref.active or ref.source == "bundled":
            raise DomainCatalogError(
                f"Version {domain_id}/{version} ist aktiv oder gebündelt und kann nicht "
                "überschrieben werden. Bitte im Tab Metadaten eine neue Version vergeben."
            )


def _parse_condition_value(text: str, field_type: str) -> Any:
    if field_type == "integer":
        try:
            return int(text)
        except ValueError as exc:
            raise EditorError("Der Vergleichswert muss eine ganze Zahl sein.") from exc
    if field_type == "number":
        try:
            return float(text.replace(",", "."))
        except ValueError as exc:
            raise EditorError("Der Vergleichswert muss eine Zahl sein.") from exc
    if field_type == "boolean":
        word = text.lower()
        if word in {"ja", "true", "yes"}:
            return True
        if word in {"nein", "false", "no"}:
            return False
        raise EditorError("Der Vergleichswert muss ja oder nein sein.")
    return text


def _required_text(value: Any, message: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise EditorError(message)
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --- rules and topics ----------------------------------------------------------


def rule_from_form(values: dict[str, Any], pack: DomainPack, index: int | None) -> RuleDefinition:
    """A rule from the form values, validated with the whole pack so that the
    loader's German messages surface at once. index names the rule being
    replaced, None appends."""
    rule_id = _required_text(values.get("rule_id"), "Die Regel-ID darf nicht leer sein.")
    others = [rule for position, rule in enumerate(pack.rules) if position != index]
    if any(rule.rule_id == rule_id for rule in others):
        raise EditorError(f"Die Regel-ID {rule_id} existiert bereits.")
    reason_code = _required_text(values.get("reason_code"), "Der Reason Code darf nicht leer sein.")
    field_key = _required_text(values.get("field_key"), "Bitte ein Feld auswählen.")
    question = next((q for q in pack.questions if q.field_key == field_key), None)
    if question is None:
        raise EditorError(f"Das Feld {field_key} gibt es in diesem Pack nicht.")
    operator = _required_text(values.get("operator"), "Bitte einen Operator auswählen.")
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "kind": values.get("kind") or "recommend",
        "field_key": field_key,
        "operator": operator,
        "divisor_field_key": _optional_text(values.get("divisor_field_key")),
        "reason_code": reason_code,
        "recommendation_ids": [item for item in values.get("recommendation_ids") or [] if item],
        "topics": [item for item in values.get("topics") or [] if item],
        "origin": values.get("origin") or "expert",
        "source_reference": _optional_text(values.get("source_reference")),
        "status": values.get("status") or "draft",
    }
    priority_text = _optional_text(values.get("priority"))
    if priority_text is not None:
        try:
            data["priority"] = int(priority_text)
        except ValueError as exc:
            raise EditorError("Die Priorität muss eine ganze Zahl ab 1 sein.") from exc
        if data["priority"] < 1:
            raise EditorError("Die Priorität muss eine ganze Zahl ab 1 sein.")
    metric = metric_from_form(values)
    if metric is not None:
        data["metric"] = metric
    if operator != "is_missing":
        data["value"] = (
            metric_comparison_value(operator, values.get("value")) if metric is not None
            else comparison_value(operator, values.get("value"), question.field_type)
        )
    try:
        rule = RuleDefinition.model_validate(data)
    except ValueError as exc:
        raise EditorError(f"Die Regel ist ungültig: {exc}") from exc
    rules = list(pack.rules)
    if index is None:
        rules.append(rule)
    else:
        rules[index] = rule
    error = draft_error(pack_with_rules(pack, rules))
    if error:
        raise EditorError(error)
    return rule


def metric_from_form(values: dict[str, Any]) -> MetricSource | None:
    """The metric of a rule from the form: None while tool and pointer are
    empty, otherwise both are required (the label stays optional)."""
    tool = _optional_text(values.get("metric_tool"))
    pointer = _optional_text(values.get("metric_pointer"))
    if tool is None and pointer is None:
        return None
    if tool is None or pointer is None:
        raise EditorError("Eine Kennzahl braucht Werkzeug und JSON-Pointer.")
    try:
        return MetricSource(tool=tool, pointer=pointer, label=_optional_text(values.get("metric_label")))
    except ValueError as exc:
        raise EditorError(f"Die Kennzahl ist ungültig: {exc}") from exc


def metric_comparison_value(operator: str, raw_value: Any) -> Any:
    """The comparison value of a metric rule: numbers, whatever the type of
    the file question the rule hangs on; list operators take comma-separated
    values. Whole numbers stay int so stored packs round-trip unchanged."""
    text = "" if raw_value is None else str(raw_value)
    if operator in LIST_OPERATORS:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            raise EditorError("Der Vergleichswert darf nicht leer sein.")
        return [_parse_metric_value(part) for part in parts]
    if not text.strip():
        raise EditorError("Der Vergleichswert darf nicht leer sein.")
    return _parse_metric_value(text.strip())


def _parse_metric_value(text: str) -> int | float:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text.replace(",", "."))
    except ValueError as exc:
        raise EditorError("Der Vergleichswert einer Kennzahl muss eine Zahl sein.") from exc


def set_rule_status(rule: RuleDefinition, status: str) -> RuleDefinition:
    """The rule with a new status along the review order; returns a copy."""
    if status not in RULE_STATUS_TRANSITIONS.get(rule.status, set()):
        raise EditorError(
            f"Von {RULE_STATUS_LABELS[rule.status]} nach {RULE_STATUS_LABELS.get(status, status)} "
            "geht es nicht direkt; eine Regel geht erst in Prüfung, dann in die Freigabe."
        )
    return rule.model_copy(update={"status": status})


def new_rule(pack: DomainPack) -> RuleDefinition:
    taken = {rule.rule_id for rule in pack.rules}
    number = len(pack.rules) + 1
    while f"regel-{number}" in taken:
        number += 1
    if not pack.questions:
        raise EditorError("Lege zuerst eine Frage an, bevor du Regeln definierst.")
    question = pack.questions[0]
    value: Any = question.options[0].value if question.options else (0 if question.field_type in NUMERIC_TYPES else "")
    return RuleDefinition(
        rule_id=f"regel-{number}",
        field_key=question.field_key,
        operator="is_missing" if question.field_type in ("text", "date", "multiselect") else "equals",
        **({} if question.field_type in ("text", "date", "multiselect") else {"value": value}),
        reason_code=f"REGEL_{number}",
        status="draft",
    )


def delete_rule(rules: list[RuleDefinition], index: int) -> list[RuleDefinition]:
    return [rule for position, rule in enumerate(rules) if position != index]


def pack_with_rules(pack: DomainPack, rules: list[RuleDefinition]) -> DomainPack:
    return pack.model_copy(update={"rules": list(rules)})


def pack_with_topics(pack: DomainPack, topics: list[TopicDefinition]) -> DomainPack:
    return pack.model_copy(update={"topics": list(topics) or None})


def topics_from_text(text: str) -> list[TopicDefinition]:
    """One topic per line as "topic_id: Bezeichnung"."""
    topics: list[TopicDefinition] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise EditorError(
                f"Zeile „{line.strip()}“: Themen heißen topic_id: Bezeichnung, zum Beispiel heizung: Heizsystem."
            )
        topic_id, label = (part.strip() for part in line.split(":", 1))
        if not re.fullmatch(r"[a-z0-9_-]+", topic_id):
            raise EditorError(
                f"Die topic_id {topic_id!r} darf nur Kleinbuchstaben, Ziffern, _ und - enthalten."
            )
        if not label:
            raise EditorError(f"Das Thema {topic_id} braucht eine Bezeichnung.")
        if any(item.topic_id == topic_id for item in topics):
            raise EditorError(f"Die topic_id {topic_id} ist doppelt.")
        topics.append(TopicDefinition(topic_id=topic_id, label=label))
    return topics


def topics_to_text(topics: list[TopicDefinition] | None) -> str:
    return "\n".join(f"{topic.topic_id}: {topic.label}" for topic in topics or [])


def unapproved_rules(pack: DomainPack) -> list[str]:
    return [rule.rule_id for rule in pack.rules if rule.status != "approved"]


# --- recommendation catalog ------------------------------------------------------


def recommendation_from_form(
    values: dict[str, Any], pack: DomainPack, index: int | None
) -> RecommendationDefinition:
    """A catalog entry from the form values, validated with the whole pack so
    that renaming an entry a rule references surfaces the loader's message.
    index names the entry being replaced, None appends."""
    recommendation_id = _required_text(
        values.get("recommendation_id"), "Die Empfehlungs-ID darf nicht leer sein."
    )
    others = [item for position, item in enumerate(pack.recommendations) if position != index]
    if any(item.recommendation_id == recommendation_id for item in others):
        raise EditorError(f"Die Empfehlungs-ID {recommendation_id} existiert bereits.")
    priority_text = _required_text(values.get("priority"), "Die Priorität muss eine ganze Zahl ab 1 sein.")
    try:
        priority = int(priority_text)
    except ValueError as exc:
        raise EditorError("Die Priorität muss eine ganze Zahl ab 1 sein.") from exc
    if priority < 1:
        raise EditorError("Die Priorität muss eine ganze Zahl ab 1 sein.")
    figures = figures_from_text(values.get("figures") or "")
    template = _optional_text(values.get("message_with_figures"))
    if (figures is None) != (template is None):
        raise EditorError("Kennzahlen und den Text mit Kennzahlen gibt es nur gemeinsam.")
    data = {
        "recommendation_id": recommendation_id,
        "priority": priority,
        "title": _required_text(values.get("title"), "Der Titel darf nicht leer sein."),
        "message": _required_text(values.get("message"), "Der Text der Empfehlung darf nicht leer sein."),
        "source_reference": _optional_text(values.get("source_reference")),
        "figures": figures,
        "message_with_figures": template,
    }
    try:
        recommendation = RecommendationDefinition.model_validate(data)
    except ValueError as exc:
        raise EditorError(f"Die Empfehlung ist ungültig: {exc}") from exc
    recommendations = list(pack.recommendations)
    if index is None:
        recommendations.append(recommendation)
    else:
        recommendations[index] = recommendation
    error = draft_error(pack_with_recommendations(pack, recommendations))
    if error:
        raise EditorError(error)
    return recommendation


FIGURE_LINE_HINT = (
    "Kennzahlen heißen name: werkzeug /pointer [dezimalstellen] [{\"wert\": \"text\"}], zum Beispiel "
    "months_above: analyze_capacity_tiers /totals/months_above 0."
)


def figures_from_text(text: str) -> dict[str, FigureSource] | None:
    """One figure per line as "name: werkzeug /json/pointer [dezimalstellen]
    [{json-zuordnung}]"; an empty text means no figures."""
    figures: dict[str, FigureSource] = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise EditorError(f"Zeile „{line.strip()}“: {FIGURE_LINE_HINT}")
        name, rest = (part.strip() for part in line.split(":", 1))
        if not FIGURE_NAME.match(name):
            raise EditorError(
                f"Der Kennzahlen-Name {name!r} darf nur Kleinbuchstaben, Ziffern und _ enthalten und muss mit "
                "einem Buchstaben beginnen."
            )
        if name in figures:
            raise EditorError(f"Die Kennzahl {name} ist doppelt.")
        mapping = None
        brace = rest.find("{")
        if brace >= 0:
            try:
                mapping = json.loads(rest[brace:])
            except json.JSONDecodeError as exc:
                raise EditorError(
                    f"Kennzahl {name}: Die Zuordnung muss ein JSON-Objekt sein, zum Beispiel "
                    '{"high": "hoch"}.'
                ) from exc
            if not isinstance(mapping, dict):
                raise EditorError(f"Kennzahl {name}: Die Zuordnung muss ein JSON-Objekt sein.")
            rest = rest[:brace].strip()
        parts = rest.split()
        if len(parts) < 2 or len(parts) > 3:
            raise EditorError(f"Zeile „{line.strip()}“: {FIGURE_LINE_HINT}")
        tool, pointer = parts[0], parts[1]
        if not pointer.startswith("/"):
            raise EditorError(f"Kennzahl {name}: Der JSON-Pointer muss mit / beginnen.")
        decimals = None
        if len(parts) == 3:
            try:
                decimals = int(parts[2])
            except ValueError as exc:
                raise EditorError(f"Kennzahl {name}: Dezimalstellen müssen eine ganze Zahl von 0 bis 6 sein.") from exc
        try:
            figures[name] = FigureSource(tool=tool, pointer=pointer, decimals=decimals, map=mapping)
        except ValueError as exc:
            raise EditorError(f"Kennzahl {name} ist ungültig: {exc}") from exc
    return figures or None


def figures_to_text(figures: dict[str, FigureSource] | None) -> str:
    lines = []
    for name, figure in (figures or {}).items():
        line = f"{name}: {figure.tool} {figure.pointer}"
        if figure.decimals is not None:
            line += f" {figure.decimals}"
        if figure.map is not None:
            line += " " + json.dumps(figure.map, ensure_ascii=False)
        lines.append(line)
    return "\n".join(lines)


def new_recommendation(pack: DomainPack) -> RecommendationDefinition:
    taken = {item.recommendation_id for item in pack.recommendations}
    number = len(pack.recommendations) + 1
    while f"empfehlung-{number}" in taken:
        number += 1
    priority = max((item.priority for item in pack.recommendations), default=0) + 1
    return RecommendationDefinition(
        recommendation_id=f"empfehlung-{number}",
        priority=priority,
        title="Neue Empfehlung",
        message="Text der Empfehlung",
    )


def delete_recommendation(pack: DomainPack, index: int) -> list[RecommendationDefinition]:
    """The catalog without the entry at index; refused while a rule still
    references it, because the loader would reject the draft anyway."""
    recommendation_id = pack.recommendations[index].recommendation_id
    users = [rule.rule_id for rule in pack.rules if recommendation_id in rule.recommendation_ids]
    if users:
        raise EditorError(
            f"Die Empfehlung {recommendation_id} wird von "
            + ("der Regel " if len(users) == 1 else "den Regeln ")
            + ", ".join(users)
            + " verwendet und kann nicht gelöscht werden."
        )
    return [item for position, item in enumerate(pack.recommendations) if position != index]


def pack_with_recommendations(
    pack: DomainPack, recommendations: list[RecommendationDefinition]
) -> DomainPack:
    return pack.model_copy(update={"recommendations": list(recommendations)})


# --- pack-level settings of the result chat -------------------------------------


def result_chat_from_form(
    compact: bool, intro: str | None, updated_intro: str | None, offer: str | None
) -> ResultChatSettings | None:
    """The result_chat block from the Metadaten form: None while nothing is
    configured, so such packs serialize as before the block existed."""
    texts = {
        "intro": _optional_text(intro),
        "updated_intro": _optional_text(updated_intro),
        "offer": _optional_text(offer),
    }
    if not compact and all(value is None for value in texts.values()):
        return None
    try:
        return ResultChatSettings(recommendations="compact" if compact else "detailed", **texts)
    except ValueError as exc:
        raise EditorError(
            f"Die Texte für den Chat „Ergebnis“ dürfen höchstens 500 Zeichen lang sein: {exc}"
        ) from exc


def knowledge_recommendations_from_form(
    enabled: bool, max_count: int
) -> KnowledgeRecommendationSettings | None:
    """The knowledge_recommendations block from the Metadaten form: None for
    the defaults (enabled, at most 5), so packs without the block keep
    serializing, and hashing, as before."""
    if enabled and int(max_count) == 5:
        return None
    return KnowledgeRecommendationSettings(enabled=enabled, max_count=int(max_count))
