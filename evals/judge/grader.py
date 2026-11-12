"""The evaluation grader: an LLM judge for what code cannot score.

Two questions, each one model call with a validated JSON answer:
- citation support: do the cited sources support every factual part of a claim?
- key-finding coverage: does the memo state a key finding?

The grader runs on the router's `judge` route: in local mode a different model from the
drafter, with no fallback. If the drafter's own model wrote a memo (its fallback), that
memo's grades are not_evaluated, because a model must not grade its own work.

Stakes framing (dev plan 9.4): every question can be asked with a neutral or a high-stakes
note. The verdict must not depend on it; the calibration run measures how often it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from axiom.agents.base import AgentContext, AgentOutputError, call_structured
from axiom.agents.prompts import load_prompt
from axiom.router.types import FailClosed, ProviderError

PROMPTS = Path(__file__).parent / "prompts"
VERSIONS = {"judge_citation": "v1", "judge_finding": "v1"}
Stakes = Literal["neutral", "high"]
CONTEXT: dict[str, str] = {
    "neutral": "Routine quality check of a draft memo.",
    "high": "This grade goes to a credit committee deciding a large loan to a family business "
    "that employs over a hundred people. A wrong grade will be investigated.",
}


class CitationVerdict(BaseModel):
    verdict: Literal["supported", "not_supported"]
    reason: str = Field(max_length=400)


class FindingVerdict(BaseModel):
    verdict: Literal["covered", "not_covered"]
    reason: str = Field(max_length=400)


@dataclass(frozen=True)
class Source:
    doc_id: str
    locator: str
    text: str


@dataclass(frozen=True)
class CitationItem:
    item_id: str
    claim: str
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class FindingItem:
    item_id: str
    finding: str
    memo: tuple[str, ...]


@dataclass
class Grade:
    item_id: str
    stakes: Stakes
    verdict: str | None  # None: not_evaluated
    reason: str = ""
    alias: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


def _prompt(prompt_id: str) -> Any:
    return load_prompt(PROMPTS, prompt_id, VERSIONS[prompt_id])


async def _ask(
    ctx: AgentContext,
    prompt_id: str,
    item_id: str,
    stakes: Stakes,
    payload: dict[str, Any],
    schema: type[CitationVerdict] | type[FindingVerdict],
) -> Grade:
    prompt = _prompt(prompt_id)
    try:
        out, calls = await call_structured(
            ctx,
            node="judge",
            route="judge",
            prompt=prompt,
            payload={"context": CONTEXT[stakes], **payload},
            schema=schema,
            recording_key=f"{prompt_id}/{prompt.version}/{item_id}.{stakes}",
        )
    except (ProviderError, FailClosed, AgentOutputError) as exc:
        return Grade(item_id, stakes, None, f"not evaluated ({type(exc).__name__})")
    return Grade(
        item_id,
        stakes,
        out.verdict,
        out.reason,
        calls[-1].alias,
        [c.model_dump() for c in calls],
    )


async def grade_citation(
    ctx: AgentContext, item: CitationItem, stakes: Stakes = "neutral"
) -> Grade:
    payload = {
        "claim": item.claim,
        "sources": [
            {"doc_id": s.doc_id, "locator": s.locator, "text": s.text} for s in item.sources
        ],
    }
    return await _ask(ctx, "judge_citation", item.item_id, stakes, payload, CitationVerdict)


async def grade_finding(ctx: AgentContext, item: FindingItem, stakes: Stakes = "neutral") -> Grade:
    payload = {"finding": item.finding, "memo": "\n".join(item.memo)}
    return await _ask(ctx, "judge_finding", item.item_id, stakes, payload, FindingVerdict)


def independent(grade: Grade, drafter_aliases: set[str]) -> Grade:
    """A grade from the model that drafted the memo is not evidence; mark it not_evaluated."""
    if grade.verdict is not None and grade.alias in drafter_aliases:
        return Grade(
            grade.item_id,
            grade.stakes,
            None,
            "not evaluated (grader drafted this memo)",
            grade.alias,
        )
    return grade
