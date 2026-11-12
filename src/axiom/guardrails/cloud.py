"""Controls that need Azure AI Content Safety, a cloud service.

Demo and local modes make no cloud calls, so these report `not_evaluated` there, never
`passed`. The service client arrives with the input and output pieces of week 5; until a
client is connected, the controls say so in their reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiom.guardrails.base import Stage, Subject, Verdict

NOT_CONNECTED = (
    "needs Azure AI Content Safety, which is not connected; demo and local modes make no "
    "cloud calls"
)


@dataclass(frozen=True)
class CloudOnly:
    """A control whose check runs only in the cloud service."""

    name: str
    stage: Stage
    description: str

    async def check(self, subject: Subject) -> Verdict:
        return Verdict("not_evaluated", NOT_CONNECTED)


INJECTION_SHIELD = CloudOnly(
    "injection_shield",
    "input",
    "Screens borrower documents for hidden instructions aimed at the AI (Prompt Shields)",
)
CONTENT_SAFETY = CloudOnly(
    "content_safety",
    "output",
    "Screens the draft for harmful content: hate, violence, sexual content and self-harm",
)
