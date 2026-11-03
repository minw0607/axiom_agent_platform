"""Scaffold checks: the package and every planned subpackage import cleanly."""

import importlib

import pytest

import axiom

SUBPACKAGES = [
    "agents",
    "api",
    "evidence",
    "graph",
    "guardrails",
    "jobs",
    "mcp_clients",
    "memory",
    "router",
    "telemetry",
    "tools",
]


def test_version_is_set() -> None:
    assert axiom.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"axiom.{name}")
    assert module.__doc__, f"axiom.{name} needs a docstring saying what it holds"
