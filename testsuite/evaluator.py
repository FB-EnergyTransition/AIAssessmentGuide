import re
from typing import Any

from testsuite.models import Assertion


def resolve_pointer(data: Any, pointer: str) -> Any:
    if not pointer:
        return data
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise LookupError(pointer)
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(data):
            data = data[int(key)]
        else:
            raise LookupError(pointer)
    return data


def equal(actual, expected) -> bool:
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], v) for k, v in expected.items())
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def matches(actual, expected) -> bool:
    if isinstance(actual, dict) and isinstance(expected, dict):
        return all(k in actual and matches(actual[k], v) for k, v in expected.items())
    return equal(actual, expected)


def evaluate(assertion: Assertion, data: Any) -> dict:
    try:
        actual = resolve_pointer(data, assertion.path)
        found = True
    except LookupError:
        actual, found = None, False
    expected = assertion.expected
    if assertion.op == "exists":
        expected = True if "expected" not in assertion.model_fields_set else expected
        passed = found == expected
    elif not found:
        passed = False
    elif assertion.op == "eq":
        passed = equal(actual, expected)
    elif assertion.op in ("contains", "not_contains"):
        if isinstance(actual, list):
            contained = any(matches(item, expected) for item in actual)
        elif isinstance(actual, str) and isinstance(expected, str):
            contained = expected in actual
        elif isinstance(actual, dict) and isinstance(expected, dict):
            contained = matches(actual, expected)
        else:
            contained = None
        passed = contained is not None and contained is (assertion.op == "contains")
    elif assertion.op == "min_length":
        passed = isinstance(actual, (str, list)) and len(actual) >= expected
    else:
        passed = type(actual) in (int, float) and expected[0] <= actual <= expected[1]
    return {"path": assertion.path, "op": assertion.op, "expected": expected,
            "actual": actual, "found": found, "passed": passed,
            "message": "Erfüllt." if passed else "Erwartung nicht erfüllt."}
