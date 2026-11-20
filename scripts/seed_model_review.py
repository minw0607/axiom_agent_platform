"""Seed model documentation review (app 2) with scripted model responses.

Runs the real workflow for every catalog package, with a scripted stand-in for the model that
answers from the package's ground truth: the assessor marks weak exactly the present sections
the ground truth expects a finding on, giving that finding as its reason, and the drafter
writes the four sections from what the workflow passes it. Every response passes the same
validation as a live model's and is saved under tests/fixtures/recorded/model_review/ with
origin "scripted", so an evidence bundle can never mistake it for a model's output.

The evaluation's demo mode (evals/model_review/review_eval.py) replays these, so its results
test the pipeline and the scoring, not a model: they are the ceiling a live run is compared
with. The keyless demo replays live recordings from data/recorded/model_review/ instead.

    uv run python scripts/seed_model_review.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from axiom.apps.model_review.agents import ReviewContext
from axiom.apps.model_review.catalog import list_packages, load_ground_truth, load_package
from axiom.apps.model_review.catalog import packages_dir as model_docs_dir
from axiom.apps.model_review.domain import ReviewDecision
from axiom.apps.model_review.graph import resume_workflow, run_workflow
from axiom.apps.model_review.guardrails import suite
from axiom.apps.model_review.tools.checklist import load_checklist
from axiom.memory.checkpoint import serializer
from axiom.router.adapters import save_recording
from axiom.router.router import AliasTarget, ModelRouter, RoutingPolicy
from axiom.router.types import ModelAdapter, ModelRequest, ModelResponse
from axiom.settings import REPO_ROOT, Settings

SETTINGS = Settings.from_env()
FOLDER = model_docs_dir(SETTINGS.data_dir)
ROOT = REPO_ROOT / "tests" / "fixtures" / "recorded" / "model_review"
# The comments behind the recorded revisions. In demo mode a revision replays the recording
# made for these, whatever the validator typed.
DEMO_REVISION_COMMENTS = (
    "Say which gaps must be closed before validation can start.",
    "Name the provision behind each critical gap.",
)
ADEQUATE = "The section covers what the checklist expects."


def package_of(request: ModelRequest) -> str:
    """The package id in a recording key such as drafter/v1/mdl-04.r1.retry."""
    return request.recording_key.split("/")[2].split(".")[0]


def scripted_assessor(package_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    expected = load_ground_truth(FOLDER, package_id)["expected_findings"]
    return {
        "assessments": [
            {
                "item_id": s["item_id"],
                "adequacy": "weak" if s["item_id"] in expected else "adequate",
                "reason": expected.get(s["item_id"], ADEQUATE),
                "citations": [
                    {"doc_id": line["doc_id"], "locator": f"line {line['line']}"}
                    for line in s["lines"][:2]
                ],
            }
            for s in payload["sections"]
        ]
    }


def _provision(check: dict[str, Any]) -> dict[str, str]:
    p = check["provisions"][0]
    return {"doc_id": p["framework"], "locator": p["provision_id"]}


def scripted_drafter(package_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    expected = load_ground_truth(FOLDER, package_id)["expected_findings"]
    checks = payload["checks"]
    first = _provision(checks[0])
    gaps = [c for c in checks if c["outcome"] != "present"]
    weak = [a for a in payload["assessments"] if a["adequacy"] == "weak"]
    score = payload["completeness"]
    critical = score["critical_gaps"]
    summary = f"{score['present']} of {score['total']} checklist sections are present" + (
        f"; critical gaps: {', '.join(critical)}." if critical else "."
    )
    matters = [
        {
            "text": f"Ask the model owner to close {c['item_id']} ({c['title']}).",
            "citations": [_provision(c)],
        }
        for c in gaps
        if c["critical"]
    ] or [{"text": "Confirm the points above with the model owner.", "citations": [first]}]
    for comment in (payload.get("revision") or {}).get("comments", []):
        matters.append(
            {"text": f"Revised for the validator's comment: {comment}", "citations": [first]}
        )
    return {
        "sections": [
            {"heading": "Summary", "claims": [{"text": summary, "citations": [first]}]},
            {
                "heading": "Checklist gaps",
                "claims": [
                    {
                        "text": f"{c['item_id']} ({c['title']}) is {c['outcome']}. "
                        + expected.get(c["item_id"], ""),
                        "citations": [_provision(c)],
                    }
                    for c in gaps
                ]
                or [{"text": "Every checklist section is present.", "citations": [first]}],
            },
            {
                "heading": "Section assessments",
                "claims": [
                    {"text": f"{a['item_id']} is weak: {a['reason']}", "citations": a["citations"]}
                    for a in weak
                ]
                or [{"text": "Every present section is adequate.", "citations": [first]}],
            },
            {"heading": "Matters for the validator", "claims": matters},
        ]
    }


class ScriptedAdapter:
    provider = "scripted"

    def __init__(self, root: Path) -> None:
        self.root = root

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        payload = json.loads(request.messages[1].content)
        package_id = package_of(request)
        if request.prompt_id == "assessor":
            body = scripted_assessor(package_id, payload)
        else:
            body = scripted_drafter(package_id, payload)
        response = ModelResponse(
            text=json.dumps(body, indent=1),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            origin="scripted",
        )
        save_recording(self.root, request, response, origin="scripted")
        return response


def context(adapter: ModelAdapter, root: Path) -> ReviewContext:
    router = ModelRouter(
        RoutingPolicy(routes={r: {"primary": "scripted"} for r in ("compliance", "narrative")}),
        {"scripted": AliasTarget("scripted", "-")},
        root,
        adapter_factory=lambda provider: adapter,
        local_providers=frozenset({"scripted"}),
    )
    return ReviewContext(
        router=router,
        checklist=load_checklist(SETTINGS.app_config_dir("model_review") / "checklist.yaml"),
        prompts_dir=SETTINGS.app_prompts_dir("model_review"),
        guardrails=suite(SETTINGS.guardrails_file),
    )


async def main(root: Path = ROOT, quiet: bool = False) -> None:
    ctx = context(ScriptedAdapter(root), root)
    saver = InMemorySaver(serde=serializer())
    for entry in list_packages(FOLDER):
        package = load_package(FOLDER, entry["package_id"])
        job_id = f"seed-{package.package_id}"
        state = await run_workflow(ctx, job_id, package, saver)
        for n, comment in enumerate(DEMO_REVISION_COMMENTS, 1):
            if state.get("error"):
                break
            review = ReviewDecision(decision="revise", comments=comment, reviewer="seed", round=n)
            state = await resume_workflow(ctx, job_id, review, saver)
        status = state.get("error") or state["documentation_status"]
        if not quiet:
            print(f"{package.package_id} {entry['scenario']:<28} {status}")


if __name__ == "__main__":
    asyncio.run(main())
