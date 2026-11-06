import re
from datetime import date, datetime
from typing import Any

from guideos.domains.models import QuestionDefinition, format_date_de


class AnswerValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_answer(question: QuestionDefinition, value: Any) -> Any:
    if _is_missing(value):
        if not question.required or question.allow_unknown:
            return None
        raise AnswerValidationError("required", "Diese Angabe ist erforderlich.")

    field_type = question.field_type
    if field_type == "select":
        return _validate_select(question, value)
    if field_type == "multiselect":
        return _validate_multiselect(question, value)
    if field_type == "text":
        return _validate_text(question, value)
    if field_type == "boolean":
        return _validate_boolean(value)
    if field_type == "date":
        return _validate_date(question, value)
    if field_type == "file":
        return _validate_file(question, value)
    if field_type == "integer":
        normalized = _validate_integer(value)
    else:
        normalized = _validate_number(value)

    minimum = question.validation.minimum
    maximum = question.validation.maximum
    if minimum is not None and normalized < minimum:
        raise AnswerValidationError(
            "below_minimum",
            f"Der Wert muss mindestens {minimum:g} betragen.",
        )
    if maximum is not None and normalized > maximum:
        raise AnswerValidationError(
            "above_maximum",
            f"Der Wert darf höchstens {maximum:g} betragen.",
        )
    return normalized


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return isinstance(value, list) and len(value) == 0


def _validate_select(question: QuestionDefinition, value: Any) -> str:
    if not isinstance(value, str):
        raise AnswerValidationError(
            "invalid_type",
            "Bitte wähle eine der angebotenen Optionen.",
        )
    allowed = {option.value for option in question.options}
    if value not in allowed:
        raise AnswerValidationError(
            "invalid_option",
            "Die ausgewählte Option ist nicht gültig.",
        )
    return value


def _validate_multiselect(question: QuestionDefinition, value: Any) -> list[str]:
    if isinstance(value, str):
        chosen = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        chosen = value
    else:
        raise AnswerValidationError(
            "invalid_type",
            "Bitte wähle eine oder mehrere der angebotenen Optionen.",
        )
    allowed = {option.value for option in question.options}
    if any(item not in allowed for item in chosen):
        raise AnswerValidationError(
            "invalid_option",
            "Mindestens eine ausgewählte Option ist nicht gültig.",
        )
    normalized = [option.value for option in question.options if option.value in chosen]

    min_count = question.validation.min_count
    max_count = question.validation.max_count
    if min_count is not None and len(normalized) < min_count:
        raise AnswerValidationError(
            "too_few_options",
            f"Bitte wähle mindestens {min_count} Optionen aus.",
        )
    if max_count is not None and len(normalized) > max_count:
        raise AnswerValidationError(
            "too_many_options",
            f"Bitte wähle höchstens {max_count} Optionen aus.",
        )
    return normalized


def _validate_text(question: QuestionDefinition, value: Any) -> str:
    if not isinstance(value, str):
        raise AnswerValidationError("invalid_type", "Bitte gib einen Text ein.")
    normalized = value.strip()
    min_length = question.validation.min_length
    max_length = question.validation.max_length
    pattern = question.validation.pattern
    if min_length is not None and len(normalized) < min_length:
        raise AnswerValidationError(
            "too_short",
            f"Der Text muss mindestens {min_length} Zeichen lang sein.",
        )
    if max_length is not None and len(normalized) > max_length:
        raise AnswerValidationError(
            "too_long",
            f"Der Text darf höchstens {max_length} Zeichen lang sein.",
        )
    if pattern is not None and re.fullmatch(pattern, normalized) is None:
        raise AnswerValidationError(
            "pattern_mismatch",
            "Die Eingabe hat nicht das erwartete Format.",
        )
    return normalized


_TRUE_WORDS = {"true", "ja", "yes"}
_FALSE_WORDS = {"false", "nein", "no"}


def _validate_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise AnswerValidationError("invalid_type", "Bitte antworte mit Ja oder Nein.")


def _validate_date(question: QuestionDefinition, value: Any) -> str:
    normalized = _parse_date(value)
    if normalized is None:
        raise AnswerValidationError(
            "invalid_type",
            "Bitte gib ein Datum im Format TT.MM.JJJJ ein.",
        )
    min_date = question.validation.min_date
    max_date = question.validation.max_date
    if min_date is not None and normalized < min_date:
        raise AnswerValidationError(
            "before_minimum",
            f"Das Datum darf nicht vor dem {format_date_de(min_date)} liegen.",
        )
    if max_date is not None and normalized > max_date:
        raise AnswerValidationError(
            "after_maximum",
            f"Das Datum darf nicht nach dem {format_date_de(max_date)} liegen.",
        )
    return normalized


def _validate_file(question: QuestionDefinition, value: Any) -> dict[str, Any]:
    """The upload input of a file question: file name, decoded text content
    and format hint, checked for shape, extension and size. The import on
    the tool server happens afterwards in the orchestrator; a value that
    already carries a dataset_id is such an import result and passes."""
    if not isinstance(value, dict):
        raise AnswerValidationError("invalid_type", "Bitte lade eine Datei hoch.")
    if isinstance(value.get("dataset_id"), str) and value["dataset_id"]:
        return value
    filename = value.get("filename")
    content = value.get("content")
    format_hint = value.get("format_hint", "auto")
    if (
        not isinstance(filename, str) or not filename.strip()
        or not isinstance(content, str)
        or not isinstance(format_hint, str) or not format_hint.strip()
    ):
        raise AnswerValidationError("invalid_type", "Bitte lade eine Datei hoch.")
    if not content.strip():
        raise AnswerValidationError("file_empty", "Die Datei ist leer.")
    upload = question.upload
    extensions = upload.extensions if upload is not None else ["csv", "txt"]
    name = filename.strip()
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in extensions:
        allowed = ", ".join(f".{item}" for item in extensions)
        raise AnswerValidationError(
            "file_extension", f"Bitte lade eine Datei mit der Endung {allowed} hoch."
        )
    max_bytes = upload.max_bytes if upload is not None else None
    if max_bytes is not None and len(content.encode("utf-8")) > max_bytes:
        raise AnswerValidationError(
            "file_too_large", f"Die Datei ist größer als {_format_bytes(max_bytes)}."
        )
    return {"filename": name, "content": content, "format_hint": format_hint.strip()}


def _format_bytes(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count // (1024 * 1024)} MiB"
    if count >= 1024:
        return f"{count // 1024} KiB"
    return f"{count} Bytes"


def _parse_date(value: Any) -> str | None:
    """Return the ISO form of a date given as date, ISO text or TT.MM.JJJJ text."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    german = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if german:
        text = f"{german.group(3)}-{int(german.group(2)):02d}-{int(german.group(1)):02d}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _validate_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise AnswerValidationError("invalid_type", "Bitte gib eine ganze Zahl ein.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise AnswerValidationError("invalid_type", "Bitte gib eine ganze Zahl ein.")


def _validate_number(value: Any) -> float | int:
    if isinstance(value, bool):
        raise AnswerValidationError("invalid_type", "Bitte gib eine Zahl ein.")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            pass
    raise AnswerValidationError("invalid_type", "Bitte gib eine Zahl ein.")
