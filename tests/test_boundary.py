"""The core never imports an app (ADR 0016). Apps reach the core through config/apps.yaml."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "axiom"

# Core modules that still import the credit memo app. Each moves behind the app registry in
# step 1b of ADR 0016; the list may only shrink.
NOT_YET_SEPARATED = {
    "api/app.py",
    "api/explain.py",
    "api/schemas.py",
    "jobs/worker.py",
}


def app_imports(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        found += [n for n in names if n == "axiom.apps" or n.startswith("axiom.apps.")]
    return found


def core_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "apps" not in p.relative_to(SRC).parts)


def test_core_modules_do_not_import_apps() -> None:
    offenders = {
        str(p.relative_to(SRC)): app_imports(p)
        for p in core_modules()
        if str(p.relative_to(SRC)) not in NOT_YET_SEPARATED and app_imports(p)
    }
    assert offenders == {}


def test_the_not_yet_separated_list_is_current() -> None:
    """A module that no longer imports an app comes off the list."""
    still = {name for name in NOT_YET_SEPARATED if app_imports(SRC / name)}
    assert still == NOT_YET_SEPARATED


def test_the_scan_finds_app_imports() -> None:
    assert app_imports(SRC / "jobs" / "worker.py")
