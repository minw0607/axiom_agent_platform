"""The credit memo's agent context. Structured model calls are the core's (core/agents.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from axiom.apps.credit_memo.guardrails import suite
from axiom.apps.credit_memo.tools.policy import PolicyThresholds
from axiom.core.agents import AgentOutputError, call_structured, collect_calls, sensitivity
from axiom.core.prompts import Prompt, load_prompt
from axiom.guardrails import GuardrailSuite
from axiom.mcp_clients.evidence import EvidenceService
from axiom.mcp_clients.governance import GovernanceLookup
from axiom.router.router import ModelRouter
from axiom.settings import Settings

PROMPT_VERSIONS = {
    "intake": "v2",
    "analyst": "v1",
    "drafter": "v3",
    "drafter_revision": "v1",
    "drafter_fix": "v1",
    "compliance": "v2",
}


@dataclass
class AgentContext:
    router: ModelRouter
    evidence: EvidenceService
    policy: PolicyThresholds
    prompts_dir: Path
    governance: GovernanceLookup | None = None
    guardrails: GuardrailSuite = field(
        default_factory=lambda: suite(Settings.from_env().guardrails_file)
    )

    def prompt(self, prompt_id: str) -> Prompt:
        return load_prompt(self.prompts_dir, prompt_id, PROMPT_VERSIONS[prompt_id])


__all__ = [
    "PROMPT_VERSIONS",
    "AgentContext",
    "AgentOutputError",
    "call_structured",
    "collect_calls",
    "sensitivity",
]
