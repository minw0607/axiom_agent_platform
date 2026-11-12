"""The LangGraph checkpointer that lets a job pause for review and resume later.

Checkpoints are keyed by job_id (the LangGraph thread). Deserialization is limited to an
explicit allowlist of the platform's own state types, so a tampered checkpoint cannot make
the worker build an arbitrary object.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from axiom import domain
from axiom.agents.base import ModelCall
from axiom.mcp_clients.evidence import EvidenceHit
from axiom.mcp_clients.governance import RelatedGuidance, RelatedProvision


def allowed_types() -> list[tuple[str, str]]:
    """Every Pydantic model and enum in the domain, plus the call, evidence and guidance
    records."""
    types: list[type[Any]] = [ModelCall, EvidenceHit, RelatedGuidance, RelatedProvision]
    for _, obj in inspect.getmembers(domain, inspect.isclass):
        if obj.__module__ == domain.__name__ and issubclass(obj, BaseModel | enum.Enum):
            types.append(obj)
    return sorted((t.__module__, t.__name__) for t in types)


def serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=allowed_types())


@asynccontextmanager
async def postgres_checkpointer(url: str) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url, serde=serializer()) as saver:
        await saver.setup()
        yield saver
