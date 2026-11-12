"""Related AI governance guidance from Regulus, attached at finalize for model risk review.

Regulus covers AI governance, not lending regulation (ADR 0012), so its provisions are
labeled related guidance. They never review memo content and never block finalization.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from axiom.mcp_clients.client import MCPToolClient


class RelatedProvision(BaseModel):
    citation: str
    framework: str
    provision_id: str
    source_url: str
    text_hash: str
    relevance: float | None = None


class RelatedGuidance(BaseModel):
    """What finalize attaches. not_evaluated when Regulus is not configured or fails."""

    label: str = "Related AI governance guidance for model risk review; not a compliance finding"
    status: Literal["attached", "not_evaluated"]
    reason: str = ""
    query: str = ""
    provisions: list[RelatedProvision] = []


class GovernanceLookup(Protocol):
    async def lookup(self, description: str, k: int = 5) -> list[RelatedProvision]: ...


class RegulusGovernance:
    def __init__(self, client: MCPToolClient) -> None:
        self.client = client

    async def lookup(self, description: str, k: int = 5) -> list[RelatedProvision]:
        result = await self.client.call("lookup_provisions", {"description": description, "k": k})
        return [RelatedProvision.model_validate(p) for p in result["provisions"]]
