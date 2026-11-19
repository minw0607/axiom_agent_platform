"""Scaffold checks: the package and every planned subpackage import cleanly."""

import importlib

import pytest

import axiom

SUBPACKAGES = [
    "api",
    "apps",
    "apps.credit_memo",
    "apps.credit_memo.agents",
    "apps.credit_memo.tools",
    "core",
    "evidence",
    "gateway",
    "guardrails",
    "jobs",
    "mcp_clients",
    "memory",
    "router",
    "telemetry",
]


def test_version_is_set() -> None:
    assert axiom.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"axiom.{name}")
    assert module.__doc__, f"axiom.{name} needs a docstring saying what it holds"
