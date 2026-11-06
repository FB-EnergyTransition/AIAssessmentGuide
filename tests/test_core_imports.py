"""The core and the test-runner never import the other first-party packages
that live next to them in the repository (product packages, tests, app.py).
The set of first-party packages is read from the repository root, so the
test holds in every checkout regardless of which packages are present."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def first_party_modules() -> set[str]:
    names = set()
    for entry in ROOT.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            names.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
    return names


def imported_top_level_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def offending_imports(package: str, allowed: set[str]) -> list[str]:
    forbidden = first_party_modules() - allowed
    return sorted(
        f"{path.relative_to(ROOT)}: {name}"
        for path in (ROOT / package).rglob("*.py")
        for name in imported_top_level_names(path)
        if name in forbidden
    )


def test_the_repository_root_is_scanned():
    assert {"guideos", "testsuite", "app"} <= first_party_modules()


def test_guideos_imports_no_other_first_party_package():
    assert offending_imports("guideos", {"guideos"}) == []


def test_testsuite_imports_only_the_core():
    assert offending_imports("testsuite", {"guideos", "testsuite"}) == []
