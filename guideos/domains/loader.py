import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from guideos.domains.models import (
    RuleDefinition,
    ALLOWED_VALIDATION,
    ALLOWED_WIDGETS,
    RESERVED_UPLOAD_ARGUMENTS,
    DomainPack,
    QuestionDefinition,
    UploadDefinition,
)

NUMERIC_TYPES = {"integer", "number"}
ORDERED_TYPES = {"integer", "number", "date"}
CHOICE_TYPES = {"select", "multiselect"}


DEFAULT_DOMAIN_PACK_PATH = (
    Path(__file__).resolve().parents[2]
    / "domain_packs"
    / "verein-demo"
    / "1.0.0"
    / "pack.json"
)


class DomainPackError(Exception):
    pass


def load_domain_pack(path: Path = DEFAULT_DOMAIN_PACK_PATH) -> DomainPack:
    try:
        raw_content = path.read_bytes()
    except OSError as exc:
        raise DomainPackError("Das Domain Pack konnte nicht gelesen werden.") from exc
    return load_domain_pack_bytes(raw_content)


def load_domain_pack_bytes(raw_content: bytes) -> DomainPack:
    """Validate a serialized pack; the content hash covers exactly these bytes."""
    try:
        data = json.loads(raw_content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DomainPackError("Das Domain Pack enthält kein gültiges JSON.") from exc

    pack = validate_domain_pack_data(data)
    return pack.model_copy(
        update={"content_hash": hashlib.sha256(raw_content).hexdigest()}
    )


def validate_domain_pack_data(data: Any) -> DomainPack:
    try:
        pack = DomainPack.model_validate(data)
    except ValidationError as exc:
        details = str(exc)
        if "version" in details:
            raise DomainPackError("Die Domain-Pack-Version ist ungültig.") from exc
        raise DomainPackError("Das Domain Pack ist ungültig.") from exc

    _validate_question_types(pack)
    _validate_file_questions(pack)
    _validate_references(pack)
    _validate_web_sources(pack)
    _validate_tools(pack)
    return pack


def _validate_tools(pack: DomainPack) -> None:
    """Derivations and metric rules refer to the tools of the first analysis
    (or the dataset tool) and to questions of the pack."""
    tools = pack.tools
    for recommendation in pack.recommendations:
        if (recommendation.figures is None) != (recommendation.message_with_figures is None):
            raise DomainPackError(
                f"Empfehlung {recommendation.recommendation_id}: figures und message_with_figures gibt es "
                "nur gemeinsam."
            )
    if tools is None:
        if any(rule.metric is not None for rule in pack.rules):
            raise DomainPackError("Regeln mit metric benötigen den Pack-Block tools.")
        if any(item.figures is not None for item in pack.recommendations):
            raise DomainPackError("Empfehlungen mit figures benötigen den Pack-Block tools.")
        return
    analysis_tools = [step.tool for step in [*tools.analysis_steps(), *tools.completion_steps()]]
    if len(analysis_tools) != len(set(analysis_tools)):
        raise DomainPackError("tools.after_import und tools.at_completion nennen ein Werkzeug mehrfach.")
    known = {tools.dataset_tool, *analysis_tools}
    questions = {question.field_key: question for question in pack.questions}
    for step in tools.completion_steps():
        for name, spec in step.arguments_from_answers.items():
            question = questions.get(spec.field_key)
            prefix = f"Abschluss-Schritt {step.tool}, Argument {name}"
            if question is None:
                raise DomainPackError(f"{prefix} verweist auf ein unbekanntes Feld.")
            if question.field_type == "file":
                raise DomainPackError(f"{prefix}: Datei-Fragen liefern kein Argument.")
    for derivation in tools.derive:
        prefix = f"Ableitung für {derivation.field_key}"
        question = questions.get(derivation.field_key)
        if question is None:
            raise DomainPackError(f"{prefix} verweist auf ein unbekanntes Feld.")
        if question.field_type == "file":
            raise DomainPackError(f"{prefix}: Datei-Fragen lassen sich nicht ableiten.")
        if derivation.tool not in known:
            raise DomainPackError(
                f"{prefix} nennt das Werkzeug {derivation.tool}, das weder dataset_tool noch Teil "
                "von after_import ist."
            )
        if derivation.when is not None:
            _validate_metric_operator(f"{prefix}: Bedingung", derivation.when.operator,
                                      derivation.when.value, "value" in derivation.when.model_fields_set)
    for rule in pack.rules:
        if rule.metric is None:
            continue
        prefix = f"Regel {rule.rule_id}"
        question = questions.get(rule.field_key)
        if question is None or question.field_type != "file":
            raise DomainPackError(f"{prefix}: metric braucht als field_key eine Datei-Frage.")
        if rule.metric.tool not in analysis_tools:
            raise DomainPackError(
                f"{prefix} nennt in metric das Werkzeug {rule.metric.tool}, das nicht in "
                "tools.after_import steht."
            )
        if rule.divisor_field_key is not None:
            raise DomainPackError(f"{prefix}: metric und divisor_field_key schließen sich aus.")
        _validate_metric_operator(prefix, rule.operator, rule.value, "value" in rule.model_fields_set)
    for recommendation in pack.recommendations:
        if recommendation.figures is None:
            continue
        prefix = f"Empfehlung {recommendation.recommendation_id}"
        for name, figure in recommendation.figures.items():
            if figure.tool not in known:
                raise DomainPackError(
                    f"{prefix} nennt für die Kennzahl {name} das Werkzeug {figure.tool}, das weder "
                    "dataset_tool noch Teil von after_import ist."
                )
        used = recommendation.placeholders()
        unknown = [name for name in used if name not in recommendation.figures]
        if unknown:
            raise DomainPackError(
                f"{prefix}: message_with_figures nennt Platzhalter ohne Kennzahl: {', '.join(unknown)}."
            )
        unused = [name for name in recommendation.figures if name not in used]
        if unused:
            raise DomainPackError(
                f"{prefix}: Kennzahl {', '.join(unused)} wird in message_with_figures nicht verwendet."
            )


def _validate_metric_operator(prefix: str, operator: str, value: Any, has_value: bool) -> None:
    if operator not in ("equals", "in", "lt", "gte"):
        raise DomainPackError(f"{prefix} erlaubt für Kennzahlen nur equals, in, lt oder gte.")
    if operator == "in":
        if not has_value or not isinstance(value, list):
            raise DomainPackError(f"{prefix} erwartet für in eine Liste.")
    elif not has_value or isinstance(value, list) or value is None:
        raise DomainPackError(f"{prefix} erwartet für {operator} einen Einzelwert.")


def is_iso_date(text: Any) -> bool:
    """True for a calendar date written as JJJJ-MM-TT."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _validate_web_sources(pack: DomainPack) -> None:
    if pack.web_sources is None:
        return
    for domain in pack.web_sources.allowed_domains:
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain):
            raise DomainPackError(
                f"web_sources.allowed_domains erwartet Hostnamen wie klimaaktiv.at, nicht {domain!r}."
            )


def _validate_question_types(pack: DomainPack) -> None:
    for question in pack.questions:
        _validate_question_type(question)


def _validate_file_questions(pack: DomainPack) -> None:
    """File questions need the pack's tools block (the import happens on the
    registered server) and at most one of them may feed the advisory, as
    long as a profile carries a single dataset."""
    file_questions = [question for question in pack.questions if question.field_type == "file"]
    if not file_questions:
        return
    if pack.tools is None:
        raise DomainPackError("Fragen vom Typ file benötigen den Pack-Block tools.")
    advisory = [
        question.question_id for question in file_questions
        if question.upload is not None and question.upload.use_in_advisory
    ]
    if len(advisory) > 1:
        raise DomainPackError(
            "Höchstens eine Frage vom Typ file darf use_in_advisory setzen "
            f"(gesetzt bei {', '.join(advisory)})."
        )


def _validate_question_type(question: QuestionDefinition) -> None:
    prefix = f"Frage {question.question_id}"
    field_type = question.field_type
    if field_type in CHOICE_TYPES and not question.options:
        raise DomainPackError(
            f"{prefix} benötigt für field_type {field_type} mindestens eine Option."
        )
    if field_type not in CHOICE_TYPES and question.options:
        raise DomainPackError(
            f"{prefix} erlaubt für field_type {field_type} keine Optionen."
        )
    if question.widget is not None and question.widget not in ALLOWED_WIDGETS.get(
        field_type, set()
    ):
        raise DomainPackError(
            f"{prefix} erlaubt für field_type {field_type} kein Widget {question.widget}."
        )
    if field_type == "file":
        if question.upload is None:
            raise DomainPackError(f"{prefix} benötigt für field_type file den Block upload.")
        _validate_upload(prefix, question.upload)
    elif question.upload is not None:
        raise DomainPackError(f"{prefix} erlaubt für field_type {field_type} keinen Block upload.")

    validation = question.validation
    used = {key for key, value in validation.model_dump().items() if value is not None}
    for key in sorted(used - ALLOWED_VALIDATION[field_type]):
        raise DomainPackError(
            f"{prefix} erlaubt für field_type {field_type} keine Validierung {key}."
        )
    for lower_key, upper_key in (
        ("minimum", "maximum"),
        ("min_length", "max_length"),
        ("min_count", "max_count"),
        ("min_date", "max_date"),
    ):
        lower = getattr(validation, lower_key)
        upper = getattr(validation, upper_key)
        if lower is not None and upper is not None and lower > upper:
            raise DomainPackError(
                f"{prefix} hat eine Untergrenze über der Obergrenze ({lower} > {upper})."
            )
    if validation.pattern is not None:
        try:
            re.compile(validation.pattern)
        except re.error as exc:
            raise DomainPackError(f"{prefix} hat ein ungültiges pattern.") from exc
    for key in ("min_date", "max_date"):
        bound = getattr(validation, key)
        if bound is not None and not is_iso_date(bound):
            raise DomainPackError(
                f"{prefix} erwartet Datumsgrenzen im Format JJJJ-MM-TT."
            )


def _validate_upload(prefix: str, upload: UploadDefinition) -> None:
    reserved = RESERVED_UPLOAD_ARGUMENTS & set(upload.arguments)
    if reserved:
        raise DomainPackError(
            f"{prefix}: upload.arguments dürfen {', '.join(sorted(reserved))} nicht enthalten; "
            "GuideOS setzt diese Argumente selbst."
        )
    for extension in upload.extensions:
        if not re.fullmatch(r"[a-z0-9]+", extension):
            raise DomainPackError(
                f"{prefix} erwartet in upload.extensions Endungen ohne Punkt wie csv, nicht {extension!r}."
            )


def _validate_references(pack: DomainPack) -> None:
    field_keys = [question.field_key for question in pack.questions]
    field_positions = {
        question.field_key: index for index, question in enumerate(pack.questions)
    }
    field_types = {question.field_key: question.field_type for question in pack.questions}
    question_ids = [question.question_id for question in pack.questions]
    recommendation_ids = {
        recommendation.recommendation_id for recommendation in pack.recommendations
    }

    if len(field_keys) != len(set(field_keys)):
        raise DomainPackError("Das Domain Pack enthält doppelte field_key-Werte.")
    if len(question_ids) != len(set(question_ids)):
        raise DomainPackError("Das Domain Pack enthält doppelte question_id-Werte.")

    for index, question in enumerate(pack.questions):
        condition = question.visible_if
        if condition is None:
            continue
        if condition.field_key not in field_positions:
            raise DomainPackError(
                f"Frage {question.question_id} verweist in visible_if auf "
                f"unbekanntes Feld {condition.field_key}."
            )
        if field_positions[condition.field_key] >= index:
            raise DomainPackError(
                f"Frage {question.question_id} muss in visible_if auf eine "
                "frühere Frage verweisen."
            )
        _validate_condition(
            f"Frage {question.question_id} erwartet für visible_if.{condition.operator}",
            f"Frage {question.question_id} erlaubt visible_if.{condition.operator}",
            field_types[condition.field_key],
            condition.operator,
            condition.value,
            "value" in condition.model_fields_set,
        )

    for rule in pack.rules:
        if rule.field_key not in field_keys:
            raise DomainPackError(
                f"Regel {rule.rule_id} verweist auf unbekanntes Feld {rule.field_key}."
            )
        if (
            rule.divisor_field_key is not None
            and rule.divisor_field_key not in field_keys
        ):
            raise DomainPackError(
                f"Regel {rule.rule_id} verweist auf unbekanntes Divisor-Feld "
                f"{rule.divisor_field_key}."
            )
        if rule.divisor_field_key is not None and (
            field_types[rule.field_key] not in NUMERIC_TYPES
            or field_types[rule.divisor_field_key] not in NUMERIC_TYPES
        ):
            raise DomainPackError(
                f"Regel {rule.rule_id} benötigt für divisor_field_key numerische Felder."
            )
        if rule.metric is None:
            _validate_condition(
                f"Regel {rule.rule_id} erwartet für {rule.operator}",
                f"Regel {rule.rule_id} erlaubt {rule.operator}",
                field_types[rule.field_key],
                rule.operator,
                rule.value,
                "value" in rule.model_fields_set,
            )
        unknown = set(rule.recommendation_ids) - recommendation_ids
        if unknown:
            raise DomainPackError(
                f"Regel {rule.rule_id} verweist auf unbekannte Empfehlungen: "
                f"{', '.join(sorted(unknown))}."
            )
        _validate_rule_kind(pack, rule)


def _validate_rule_kind(pack: DomainPack, rule: RuleDefinition) -> None:
    topic_ids = [topic.topic_id for topic in (pack.topics or [])]
    if len(topic_ids) != len(set(topic_ids)):
        raise DomainPackError("Das Domain Pack enthält doppelte topic_id-Werte.")
    if rule.kind == "recommend":
        if rule.topics:
            raise DomainPackError(
                f"Regel {rule.rule_id}: topics gibt es nur für exclude und prioritize."
            )
        if rule.priority is not None:
            raise DomainPackError(
                f"Regel {rule.rule_id}: priority gibt es nur für prioritize."
            )
        return
    if rule.recommendation_ids:
        raise DomainPackError(
            f"Regel {rule.rule_id} ({rule.kind}) darf keine recommendation_ids nennen."
        )
    if not rule.topics:
        raise DomainPackError(
            f"Regel {rule.rule_id} ({rule.kind}) braucht mindestens ein Thema in topics."
        )
    unknown_topics = set(rule.topics) - set(topic_ids)
    if unknown_topics:
        raise DomainPackError(
            f"Regel {rule.rule_id} verweist auf unbekannte Themen: "
            f"{', '.join(sorted(unknown_topics))}."
        )
    if rule.kind == "prioritize" and rule.priority is None:
        raise DomainPackError(f"Regel {rule.rule_id} (prioritize) braucht eine priority.")
    if rule.kind == "exclude" and rule.priority is not None:
        raise DomainPackError(f"Regel {rule.rule_id}: priority gibt es nur für prioritize.")


def _validate_condition(
    expects: str,
    allows: str,
    field_type: str,
    operator: str,
    value: Any,
    has_value: bool,
) -> None:
    """Check that a visible_if or rule condition fits the referenced field type."""
    if operator == "is_missing":
        if has_value and value is not None:
            raise DomainPackError(f"{expects} keinen Vergleichswert.")
        return
    if field_type == "file":
        raise DomainPackError(f"{allows} nicht: für file-Felder ist nur is_missing zulässig.")
    if operator in ("equals", "in") and field_type == "multiselect":
        raise DomainPackError(f"{allows} nur für Einzelwert-Felder.")
    if operator in ("contains", "contains_any") and field_type != "multiselect":
        raise DomainPackError(f"{allows} nur für multiselect-Felder.")
    if operator in ("lt", "gte") and field_type not in ORDERED_TYPES:
        raise DomainPackError(f"{allows} nur für integer, number oder date.")

    if operator in ("in", "contains_any"):
        if not has_value or not isinstance(value, list):
            raise DomainPackError(f"{expects} eine Liste.")
        values = value
    else:
        if not has_value or isinstance(value, list):
            raise DomainPackError(f"{expects} einen Einzelwert.")
        values = [value]

    for item in values:
        if field_type == "boolean" and not isinstance(item, bool):
            raise DomainPackError(f"{expects} bei boolean true oder false.")
        if operator in ("lt", "gte"):
            if field_type == "date" and not is_iso_date(item):
                raise DomainPackError(f"{expects} ein Datum im Format JJJJ-MM-TT.")
            if field_type in NUMERIC_TYPES and (
                isinstance(item, bool) or not isinstance(item, (int, float))
            ):
                raise DomainPackError(f"{expects} eine Zahl.")
