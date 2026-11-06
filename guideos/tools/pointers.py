"""JSON pointers into structured tool outputs (RFC 6901 subset: object keys
and list indexes, ~1 and ~0 unescaped) and the numeric coercion of the
values pydantic serialises as strings (Decimal)."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def resolve_pointer(data: Any, pointer: str) -> Any:
    """The value under the pointer, None when any step is missing."""
    if not pointer.startswith("/"):
        return None
    current = data
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def numeric(value: Any) -> float | int | None:
    """A number from a tool output value: ints and floats as they are,
    numeric strings (Decimal output) as float; anything else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    return None


def compare(value: Any, operator: str, expected: Any) -> bool:
    """equals / in / lt / gte between a tool output value and a pack value;
    numbers are compared numerically whatever their serialised form."""
    number = numeric(value)
    if operator == "equals":
        return value == expected or (number is not None and numeric(expected) is not None
                                     and number == numeric(expected))
    if operator == "in":
        return isinstance(expected, list) and any(compare(value, "equals", item) for item in expected)
    if number is None or numeric(expected) is None:
        return False
    if operator == "lt":
        return number < numeric(expected)
    if operator == "gte":
        return number >= numeric(expected)
    return False


def format_figure(value: Any, decimals: int | None = None, mapping: dict[str, Any] | None = None) -> str | None:
    """A tool output value as the text a recommendation quotes: mapped values
    through mapping (an unmapped value gives None), booleans as Ja/Nein,
    numbers rounded to decimals (or shown as written, integers without
    decimals) with a German decimal comma and no grouping (a thousands dot
    would end the first sentence of a card teaser early), anything else as
    its string. None for a missing value."""
    if value is None:
        return None
    if mapping is not None:
        key = str(value).lower() if isinstance(value, bool) else str(value)
        if key not in mapping:
            return None
        value = mapping[key]
        if value is None:
            return None
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    number = numeric(value)
    if number is None:
        return str(value)
    decimal = Decimal(str(value)) if not isinstance(value, float) else Decimal(repr(value))
    if decimals is not None:
        decimal = decimal.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    elif decimal == decimal.to_integral_value():
        decimal = decimal.quantize(Decimal(1))
    return f"{decimal:f}".replace(".", ",")
