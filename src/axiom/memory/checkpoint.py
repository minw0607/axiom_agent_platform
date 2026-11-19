"""The LangGraph checkpointer that lets a job pause for review and resume later.

Checkpoints are keyed by job_id (the LangGraph thread). Deserialization is limited to an
explicit allowlist of the platform's own state types and those of the enabled apps, so a
tampered checkpoint cannot make the worker build an arbitrary object.
"""

from __future__ import annotations

import enum
import inspect
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from axiom.core import types as core_types
from axiom.core.registry import enabled_apps
from axiom.guardrails import GuardrailResult
from axiom.mcp_clients.client import ToolCall
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.mcp_clients.governance import RelatedGuidance, RelatedProvision

# Core types that lived elsewhere before ADR 0016: {old module name: current module}.
LEGACY_MODULES: dict[str, ModuleType] = {"axiom.agents.base": core_types}


def _state_types(module: ModuleType) -> list[type[Any]]:
    """The Pydantic models and enums a module defines or re-exports."""
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel | enum.Enum)
        and obj.__module__.startswith("axiom.")
        and (obj.__module__ == module.__name__ or obj.__name__ in getattr(module, "__all__", ()))
    ]


def allowed_types() -> list[tuple[str, str]]:
    """Every Pydantic model and enum in the core types and in each enabled app's state
    modules, plus the call, evidence, guidance and guardrail records. A type is allowed
    under its current module and under any module it was reachable from before ADR 0016,
    so a job paused before the restructure still resumes."""
    pairs: set[tuple[str, str]] = {
        (t.__module__, t.__name__)
        for t in (ToolCall, EvidenceHit, RelatedGuidance, RelatedProvision, GuardrailResult)
    }
    modules: list[ModuleType] = [core_types]
    legacy = dict(LEGACY_MODULES)
    for app in enabled_apps().values():
        modules += app.state_modules
        legacy.update(app.legacy_modules)
    for module in modules:
        pairs |= {(t.__module__, t.__name__) for t in _state_types(module)}
    for old_name, module in legacy.items():
        # The serializer imports a type's module by name; the old name resolves to the new.
        sys.modules.setdefault(old_name, module)
        pairs |= {(old_name, t.__name__) for t in _state_types(module)}
    return sorted(pairs)


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=allowed_types())


@asynccontextmanager
async def postgres_checkpointer(url: str) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url, serde=serializer()) as saver:
        await saver.setup()
        yield saver
