"""The enabled apps, read from config/apps.yaml (ADR 0016).

The core finds apps through this file, never through an import, so no core module depends
on any app. Each entry names an app's package; the package exposes an `APP` object.
"""

from __future__ import annotations

import importlib
from functools import cache
from pathlib import Path

import yaml

from axiom.core.app import App


class AppConfigError(ValueError):
    """config/apps.yaml is malformed or names an app that cannot be loaded."""


def read_app_packages(path: Path) -> dict[str, str]:
    raw = yaml.safe_load(path.read_text()) or {}
    entries = raw.get("apps")
    if not isinstance(entries, list) or not entries:
        raise AppConfigError(f"{path.name} needs a non-empty 'apps' list")
    packages: dict[str, str] = {}
    for entry in entries:
        if not (isinstance(entry, dict) and len(entry) == 1):
            raise AppConfigError(f"{path.name}: each app is one 'app_id: package' entry")
        ((app_id, package),) = entry.items()
        if not (isinstance(package, str) and package.startswith("axiom.apps.")):
            raise AppConfigError(f"{app_id}: the package must be under axiom.apps")
        if app_id in packages:
            raise AppConfigError(f"{app_id} is listed twice")
        packages[str(app_id)] = package
    return packages


def load_apps(path: Path) -> dict[str, App]:
    apps: dict[str, App] = {}
    for app_id, package in read_app_packages(path).items():
        spec = getattr(importlib.import_module(package), "APP", None)
        if not isinstance(spec, App):
            raise AppConfigError(f"{package} has no APP meeting the App protocol")
        if spec.app_id != app_id:
            raise AppConfigError(f"{package} is app {spec.app_id!r}, listed as {app_id!r}")
        apps[app_id] = spec
    return apps


def get_app(app_id: str) -> App:
    """An enabled app by ID. Raises KeyError."""
    return enabled_apps()[app_id]


@cache
def enabled_apps(path: Path | None = None) -> dict[str, App]:
    from axiom.settings import Settings

    return load_apps(path or Settings.from_env().config_dir / "apps.yaml")
