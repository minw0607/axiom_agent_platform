"""The core never imports an app (ADR 0016). Apps reach the core through config/apps.yaml."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "axiom"


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
    offenders = {str(p.relative_to(SRC)): app_imports(p) for p in core_modules() if app_imports(p)}
    assert offenders == {}


def test_the_scan_finds_app_imports(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text(
        "import axiom.apps\nfrom axiom.apps.credit_memo import APP\nimport axiom.core\n"
    )
    assert app_imports(module) == ["axiom.apps", "axiom.apps.credit_memo"]


def test_the_api_and_worker_are_core_modules() -> None:
    scanned = {str(p.relative_to(SRC)) for p in core_modules()}
    assert {"api/app.py", "api/jobs.py", "jobs/worker.py", "core/registry.py"} <= scanned


def test_an_app_never_imports_another_app() -> None:
    """Shared code belongs in the core, so apps stay independent of one another."""
    apps = SRC / "apps"
    offenders = {}
    for package in (p for p in apps.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        own = f"axiom.apps.{package.name}"
        for module in package.rglob("*.py"):
            others = [n for n in app_imports(module) if n != "axiom.apps" and not n.startswith(own)]
            if others:
                offenders[str(module.relative_to(SRC))] = others
    assert offenders == {}
