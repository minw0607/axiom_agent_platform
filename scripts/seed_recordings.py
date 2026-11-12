"""Seed the keyless demo with scripted model responses.

Runs the real workflow for every catalog package, with a scripted stand-in for the model:
intake answers come from the ground truth, the analyst and drafter text is filled in from a
template using only the inputs the workflow passes, and the compliance review applies simple
keyword tests to the draft's claims. Every response passes through
the same validation as a live model's and is saved under tests/fixtures/recorded/ with
origin "scripted", so the evidence bundle can never mistake it for a model's output.

The tests replay these, so their results never depend on a model's judgment. The demo
replays live recordings from data/recorded/ instead (see data/recorded/README.md).

    uv run python scripts/seed_recordings.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from axiom.agents.base import AgentContext
from axiom.catalog import list_packages, load_ground_truth, load_package
from axiom.domain import ReviewDecision
from axiom.graph.workflow import resume_workflow, run_workflow
from axiom.mcp_clients.evidence import InMemoryEvidence
from axiom.memory.checkpoint import serializer
from axiom.router.adapters import save_recording
from axiom.router.router import AliasTarget, ModelRouter, RoutingPolicy
from axiom.router.types import ModelAdapter, ModelRequest, ModelResponse
from axiom.settings import Settings
from axiom.tools.policy import load_policy
from axiom.tools.policy_rules import PROTECTED

SETTINGS = Settings.from_env()
# The comments behind the recorded revisions. In demo mode a revision replays the recording
# made for these, whatever the reviewer typed; data/recorded/README.md says so.
DEMO_REVISION_COMMENTS = (
    "Add a sentence on liquidity that cites the current ratio.",
    "State the main condition you would propose, or say that none is needed.",
)


def _ratio(payload: dict[str, Any], name: str) -> str:
    years = [r["year"] for r in payload["ratios"] if r["name"] == name]
    if not years:
        return "not computable"
    latest = max(years)
    value = next(r["value"] for r in payload["ratios"] if r["name"] == name and r["year"] == latest)
    return "not computable" if value is None else str(value)


def scripted_intake(package_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    truth = load_ground_truth(SETTINGS.packages_dir, package_id)
    doc_id = payload["document"]["doc_id"]
    figures = [
        {"field": field, "year": int(year), "value": raw["value"], "line": raw["line"]}
        for year, fields in truth["years"].items()
        for field, raw in fields.items()
        if raw["doc_id"] == doc_id
    ]
    figures += [
        {"field": field, "year": None, "value": raw["value"], "line": raw["line"]}
        for field, raw in truth["loan"].items()
        if raw["doc_id"] == doc_id
    ]
    return {"figures": figures}


def scripted_analyst(payload: dict[str, Any]) -> dict[str, Any]:
    years = payload["fiscal_years"]
    paragraphs = [
        f"Across FY{years[0]} to FY{years[-1]}, the EBITDA margin reached "
        f"{_ratio(payload, 'ebitda_margin')} in the latest year, with latest revenue growth "
        f"of {_ratio(payload, 'revenue_growth')}.",
        f"Including the requested loan, pro forma DSCR is {_ratio(payload, 'pro_forma_dscr')} "
        f"and pro forma leverage is {_ratio(payload, 'pro_forma_leverage')}x. Interest "
        f"coverage is {_ratio(payload, 'interest_coverage')}x and the current ratio is "
        f"{_ratio(payload, 'current_ratio')}.",
    ]
    flags = [f["message"] for f in payload["risk_flags"]]
    issues = [c["detail"] for c in payload["reconciliation_issues"]]
    paragraphs.append(
        "Risk flags: " + " ".join(flags) if flags else "No policy threshold is breached."
    )
    if issues:
        paragraphs.append("Reconciliation issues: " + "; ".join(issues) + ".")
    return {"paragraphs": paragraphs}


FLAG_FIELDS = {
    "DSCR_BELOW_MIN": ("ebitda", "requested_amount"),
    "LEVERAGE_ABOVE_MAX": ("total_debt", "requested_amount"),
    "LIQUIDITY_BELOW_MIN": ("total_current_assets", "total_current_liabilities"),
    "COVERAGE_BELOW_MIN": ("ebitda", "interest_expense"),
    "REVENUE_DECLINE": ("revenue",),
    "INSUFFICIENT_HISTORY": ("revenue",),
}


def scripted_drafter(package_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    package = load_package(SETTINGS.packages_dir, package_id)
    src = {f["field"]: f for f in payload["figure_sources"]}

    def cite(*fields: str) -> list[dict[str, str]]:
        return [
            {"doc_id": src[f]["doc_id"], "locator": f"line {src[f]['line']}"}
            for f in fields
            if f in src
        ]

    description = package.document("business_description")
    assert description is not None
    lines = description.text.splitlines()

    def cite_text(fragment: str) -> list[dict[str, str]]:
        n = next(i for i, line in enumerate(lines, 1) if fragment in line)
        return [{"doc_id": description.doc_id, "locator": f"line {n}"}]

    year = src["revenue"]["year"]
    v = {k: f["value"] for k, f in src.items()}
    borrower = [
        {
            "text": f"{payload['borrower']['name']} is a fictional "
            f"{payload['borrower']['industry']} business.",
            "citations": cite_text("is a fictional"),
        },
        {
            "text": f"The request is a commercial term loan of {v['requested_amount']} at "
            f"{v['interest_rate_pct']}% for {v['term_years']} years, amortizing over "
            f"{v['amortization_years']} years.",
            "citations": cite(
                "requested_amount", "interest_rate_pct", "term_years", "amortization_years"
            ),
        },
    ]
    analysis = [
        {
            "text": f"FY{year} revenue was {v['revenue']} and EBITDA was {v['ebitda']}.",
            "citations": cite("revenue", "ebitda"),
        },
        {
            "text": f"Total debt was {v['total_debt']} at the end of FY{year}.",
            "citations": cite("total_debt"),
        },
        {
            "text": f"Pro forma DSCR is {_ratio(payload, 'pro_forma_dscr')} and pro forma leverage "
            f"is {_ratio(payload, 'pro_forma_leverage')}x, as computed by the platform's "
            "ratio tools.",
            "citations": cite("ebitda", "total_debt", "requested_amount"),
        },
        {
            "text": f"The current ratio is {_ratio(payload, 'current_ratio')}, as computed by the "
            "platform's ratio tools.",
            "citations": cite("total_current_assets", "total_current_liabilities"),
        },
    ]

    def flag_citations(code: str) -> list[dict[str, str]]:
        if code.startswith("CONCENTRATION"):  # the share comes from the business description
            return cite_text("largest customer")
        return cite(*FLAG_FIELDS.get(code, ("ebitda",)))

    risks = [
        {
            "text": f"{f['code']} ({f['policy_ref']}): {f['message']}",
            "citations": flag_citations(f["code"]),
        }
        for f in payload["risk_flags"]
    ]
    risks += [
        {
            "text": f"Reconciliation check failed: {c['detail']}.",
            "citations": cite("total_assets", "total_liabilities", "total_equity"),
        }
        for c in payload["reconciliation_issues"]
        if c["outcome"] == "triggered"
    ]
    concentrated = any("largest customer" in line for line in lines)
    if concentrated:
        line = next(t for t in lines if "largest customer" in t)
        sentence = line[line.index("Its largest customer") :]
        risks.append(
            {
                "text": f"The business description states: {sentence} Policy CP-5 requires the "
                "memo to address it.",
                "citations": cite_text("largest customer"),
            }
        )
    if not risks:
        risks.append(
            {
                "text": "No credit policy threshold is breached by the computed ratios.",
                "citations": cite("ebitda", "total_debt"),
            }
        )
    codes = {f["code"] for f in payload["risk_flags"] if f["severity"] == "high"}
    if any(c["outcome"] == "triggered" for c in payload["reconciliation_issues"]):
        rec = "refer"
    elif "DSCR_BELOW_DECLINE_FLOOR" in codes:
        rec = "decline"
    elif codes or payload["risk_flags"] or concentrated:
        rec = "approve_with_conditions"
    else:
        rec = "approve"
    rec_claims: list[dict[str, Any]] = [
        {"text": f"Draft recommendation for the credit officer: {rec}.", "citations": []}
    ]
    if rec == "approve_with_conditions":
        rec_claims.append(
            {
                "text": "Proposed condition: each risk flag above is addressed to the credit "
                "officer's satisfaction before closing.",
                "citations": [],
            }
        )
    return {
        "sections": [
            {"heading": "Borrower and request", "claims": borrower},
            {"heading": "Financial analysis", "claims": analysis},
            {"heading": "Risk factors", "claims": risks},
            {
                "heading": "Draft recommendation",
                "claims": rec_claims,
            },
        ],
        "draft_recommendation": rec,
    }


# The reference passage each scripted assessment cites, by the locator that starts it.
REVIEW_PASSAGES = {
    "REF_PROHIBITED_BASIS": "[12 CFR 1002.2(z)]",
    "REF_REPAYMENT_CAPACITY": "[OCC RCR, Financial Statement Analysis]",
    "REF_ADVERSE_ACTION_REASONS": "[12 CFR 1002.9(b)(2)]",
}


def scripted_compliance(payload: dict[str, Any]) -> dict[str, Any]:
    claims = {c["claim_id"]: c["text"] for c in payload["claims"]}
    refs = payload["references"]
    assessments = []
    for topic in payload["topics"]:
        rule_id = topic["rule_id"]
        cited = [r["chunk_id"] for r in refs if r["text"].startswith(REVIEW_PASSAGES[rule_id])]
        chunk_ids = cited[:1] or [refs[0]["chunk_id"]]
        if rule_id == "REF_PROHIBITED_BASIS":
            hits = [k for k, v in claims.items() if PROTECTED.search(v)]
            outcome = "triggered" if hits else "passed"
            message = (
                "A claim refers to a prohibited-basis category."
                if hits
                else "No claim refers to a prohibited-basis characteristic."
            )
            about = hits
        elif rule_id == "REF_REPAYMENT_CAPACITY":
            text = " ".join(claims.values()).lower()
            needs = {
                "debt service coverage": "dscr",
                "leverage": "leverage",
                "liquidity": "current",
            }
            missing = [n for n, word in needs.items() if word not in text]
            outcome = "triggered" if missing else "passed"
            message = (
                f"The draft does not address {', '.join(missing)}."
                if missing
                else "The draft covers debt service coverage, leverage and liquidity."
            )
            about = [k for k, v in claims.items() if "dscr" in v.lower()][:1]
        else:
            reasons = [k for k, v in claims.items() if "(" in v and "CP-" in v]
            outcome = "passed" if reasons else "triggered"
            message = (
                "The draft decline names the specific policy findings behind it."
                if reasons
                else "The draft decline gives no specific reasons."
            )
            about = reasons
        assessments.append(
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "message": message,
                "claim_ids": about,
                "reference_chunk_ids": chunk_ids,
            }
        )
    return {"assessments": assessments}


class ScriptedAdapter:
    provider = "scripted"

    def __init__(self, root: Path) -> None:
        self.root = root

    async def complete(self, request: ModelRequest, deployment: str) -> ModelResponse:
        payload = json.loads(request.messages[1].content)
        package_id = request.recording_key.split("/")[2].split(".")[0]
        if request.prompt_id == "intake":
            body = scripted_intake(package_id, payload)
        elif request.prompt_id == "analyst":
            body = scripted_analyst(payload)
        elif request.prompt_id == "drafter_revision":
            body = scripted_drafter(package_id, payload)
            for comment in payload["reviewer_comments"]:
                body["sections"][-1]["claims"].append(
                    {
                        "text": f"Revised for the credit officer's comment: {comment}",
                        "citations": [],
                    }
                )
        elif request.prompt_id == "compliance":
            body = scripted_compliance(payload)
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


async def main() -> None:
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "recorded"
    adapter = ScriptedAdapter(root)

    def factory(provider: str) -> ModelAdapter:
        return adapter

    policy = RoutingPolicy(
        routes={r: {"primary": "scripted"} for r in ("extraction", "narrative", "compliance")}
    )
    router = ModelRouter(
        policy, {"scripted": AliasTarget("scripted", "-")}, root, adapter_factory=factory
    )
    evidence = InMemoryEvidence(SETTINGS.reference_dirs)
    ctx = AgentContext(router, evidence, load_policy(), SETTINGS.prompts_dir)
    saver = InMemorySaver(serde=serializer())
    for entry in list_packages(SETTINGS.packages_dir):
        package = load_package(SETTINGS.packages_dir, entry["package_id"])
        job_id = f"seed-{package.package_id}"
        state = await run_workflow(ctx, job_id, package, saver)
        # Two revision rounds with the demo comments, so revisions replay too.
        for n, comment in enumerate(DEMO_REVISION_COMMENTS, 1):
            if state.get("error"):
                break
            review = ReviewDecision(decision="revise", comments=comment, reviewer="seed", round=n)
            state = await resume_workflow(ctx, job_id, review, saver)
        status = state.get("error") or state["draft"].draft_recommendation
        review = [
            f"{f.rule_id}={f.outcome}"
            for f in state.get("findings", [])
            if f.source == "compliance_reviewer"
        ]
        print(f"{package.package_id} {entry['scenario']:<22} {status:<24} {' '.join(review)}")


if __name__ == "__main__":
    asyncio.run(main())
