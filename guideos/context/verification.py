"""Deterministic check of the figures in a model statement against the texts
it may rest on. It proves that a figure occurs there, not that the statement
uses it correctly."""

import json
import re
from collections.abc import Iterable

from guideos.context.models import AdvisoryContext

_FIGURE = r"(?:\d{1,3}(?:[   .']\d{3})+|\d+)(?:[.,]\d+)?"
_NUMBER = re.compile(rf"(?<![\w.,]){_FIGURE}(?!\d)")
_PLAIN_NUMBER = re.compile(r"(?<![\w.,])\d+(?:,\d+)?(?!\d)")
_GROUP_SEPARATOR = re.compile(r"[   .'](?=\d{3}(?!\d))")
# "1." or "2)" in front of a list item is numbering, not a figure.
_LIST_NUMBERING = re.compile(r"(?:^|(?<=\s))\d{1,2}[.)](?=\s+[^\d\s])", re.MULTILINE)
_NUMBER_WORDS = {
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7,
    "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwanzig": 20,
    "dreißig": 30, "vierzig": 40, "fünfzig": 50, "hundert": 100, "tausend": 1000,
}
_WORD = re.compile(r"[a-zäöüß]+")
_CURRENCY = r"(?:€|Euro\b|EUR\b|Cent\b|ct\b)"
_WORD_AMOUNT = re.compile(rf"\b({'|'.join(_NUMBER_WORDS)})\s+(?:euro|cent)\b")
# "€ 49,90", "10 – 30 Euro" / "zwischen 10 und 30 Euro" (both ends of a range) or "150 EUR".
_AMOUNT = re.compile(
    rf"(?<![\w.,])(?:(?:€|EUR)\s*(?P<prefixed>{_FIGURE})(?!\d)"
    rf"|(?P<low>{_FIGURE})(?:\s*{_CURRENCY})?\s*(?:–|-|bis|und|oder)\s*(?P<high>{_FIGURE})\s*{_CURRENCY}"
    rf"|(?P<single>{_FIGURE})\s*{_CURRENCY})"
)


def unverified_numbers(text: str, sources: Iterable[str]) -> list[str]:
    """The figures in text, as written, that occur in none of the sources.
    Formats are compared by value (28.000 = 28 000 = 28000, 21,5 = 21.5);
    number words in the sources count, list numbering in text does not."""
    known = _known_values(sources)
    missing: dict[float, str] = {}
    for match in _NUMBER.finditer(_LIST_NUMBERING.sub(" ", text)):
        value = _value(match.group())
        if value not in known:
            missing.setdefault(value, match.group())
    return list(missing.values())


def unverified_amounts(
    text: str, sources: Iterable[str], facts: Iterable[str] = ()
) -> list[str]:
    """The money amounts in text, as written, that no source states as an
    amount of money. A bare number in a source does not verify a price; every
    figure in facts does (the session's own answers and rule values, stored
    without a unit). Other figures in text (temperatures, shares, years) are
    not reported."""
    known = _known_values(facts)
    for source in sources:
        known.update(_value(token) for token in _amount_tokens(source))
        known.update(float(_NUMBER_WORDS[match.group(1)]) for match in _WORD_AMOUNT.finditer(source.lower()))
    missing: dict[float, str] = {}
    for token in _amount_tokens(text):
        if _value(token) not in known:
            missing.setdefault(_value(token), token)
    return list(missing.values())


def _amount_tokens(text: str) -> list[str]:
    return [
        token
        for match in _AMOUNT.finditer(text)
        for token in match.group("prefixed", "low", "high", "single")
        if token is not None
    ]


def _known_values(sources: Iterable[str]) -> set[float]:
    known: set[float] = set()
    for source in sources:
        # Grouped reading ("2 800" = 2800) and each digit group on its own,
        # because extracted tables separate unrelated figures by spaces.
        for match in (*_NUMBER.finditer(source), *_PLAIN_NUMBER.finditer(source)):
            value = _value(match.group())
            known.update((value, float(round(value))))
        known.update(
            float(_NUMBER_WORDS[word])
            for word in _WORD.findall(source.lower())
            if word in _NUMBER_WORDS
        )
    return known


def context_texts(context: AdvisoryContext) -> list[str]:
    """Everything GuideOS itself knows about the session: stored answers,
    rule results and the guidance of the domain pack."""
    texts = [context.domain_guidance or ""]
    for fact in context.facts:
        texts.extend(
            str(value)
            for value in (
                fact.question_text, fact.normalized_value, fact.display_value, fact.raw_answer
            )
            if value is not None
        )
    for item in context.rule_recommendations:
        texts.extend((item.title, item.message))
    for metric in context.rule_metrics:
        texts.extend((str(metric.value), str(metric.threshold)))
    texts.extend(tool_result_texts(context.tool_results))
    return texts


def tool_result_texts(results) -> list[str]:
    """The summary and the structured output of tool results as text, so that
    figures a tool computed count as verified."""
    texts = []
    for result in results:
        texts.append(result.summary)
        if result.structured_content is not None:
            texts.append(json.dumps(result.structured_content, ensure_ascii=False, default=str))
    return texts


def _value(token: str) -> float:
    digits = _GROUP_SEPARATOR.sub("", token)
    return float(digits.replace(",", "."))


def strip_internal_references(
    text: str, field_keys: Iterable[str], passage_ids: Iterable[str]
) -> str:
    """Remove bracketed references to internal identifiers from a model
    statement meant for the user: field keys with their raw value such as
    "(heating_type = gas)", bare field keys such as "[construction_year]" and
    passage ids such as "(vgl. D3)" or "[D1, W2]". Provenance is shown by
    GuideOS from the cited ids, never inline. Other brackets stay."""
    keys = [re.escape(key) for key in field_keys if key]
    # Passage ids follow the D1/W1 scheme, tool results T1…; a model may also echo one it was not given.
    ids = [re.escape(item) for item in passage_ids if item] + [r"[DWT]\d{1,3}"]
    markers = []
    if keys:
        markers.append(rf"\b(?:{'|'.join(keys)})\b(?:\s*[=:]\s*[^()\[\]]*)?")
    markers.append(rf"\b(?:{'|'.join(ids)})\b")
    marker = "|".join(markers)
    cleaned = re.sub(rf"\s*[(\[][^()\[\]]*?(?:{marker})[^()\[\]]*[)\]]", "", text)
    # Ids woven into prose ("Quelle D2 empfiehlt", "laut D3 und W1") lose the id itself.
    cleaned = re.sub(rf"(?:\s*(?:,|und|sowie)\s*)?\s*\b(?:{'|'.join(ids)})\b", "", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()
