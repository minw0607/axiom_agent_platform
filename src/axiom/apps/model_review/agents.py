"""The two model steps of model documentation review: the assessor and the drafter.

Both go through the core's structured call, so an invalid answer gets one retry with the
errors and every attempt is in the evidence log. What the model returns is checked in code
before it is used: the assessor must assess exactly the present sections and cite only their
lines; the drafter must write the four sections, name every gap and weak section, and cite
only document lines or the checklist's provisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiom.apps.model_review.domain import (
    REPORT_HEADINGS,
    Assessments,
    Checklist,
    Completeness,
    DocumentationPackage,
    DocumentationStatus,
    FindingsReport,
    SectionAssessment,
    SectionCheck,
)
from axiom.core.agents import call_structured
from axiom.core.prompts import Prompt, load_prompt
from axiom.core.types import Citation, ReviewDecision
from axiom.guardrails import GuardrailSuite
from axiom.mcp_clients.governance import GovernanceLookup, RelatedProvision
from axiom.router.router import ModelRouter

PROMPT_VERSIONS = {"assessor": "v1", "drafter": "v1"}


@dataclass
class ReviewContext:
    router: ModelRouter
    checklist: Checklist
    prompts_dir: Path
    guardrails: GuardrailSuite
    governance: GovernanceLookup | None = None

    def prompt(self, prompt_id: str) -> Prompt:
        return load_prompt(self.prompts_dir, prompt_id, PROMPT_VERSIONS[prompt_id])


def _lines(package: DocumentationPackage, check: SectionCheck) -> list[dict[str, Any]]:
    doc = next(d for d in package.documents if d.doc_id == check.doc_id)
    text = doc.text.splitlines()
    assert check.first_line is not None and check.last_line is not None
    return [
        {"doc_id": doc.doc_id, "line": n, "text": text[n - 1]}
        for n in range(check.first_line, check.last_line + 1)
        if text[n - 1].strip()
    ]


def _allowed_lines(
    package: DocumentationPackage, checks: Sequence[SectionCheck]
) -> set[tuple[str, str]]:
    return {
        (line["doc_id"], f"line {line['line']}")
        for c in checks
        if c.outcome != "missing"
        for line in _lines(package, c)
    }


async def run_assessor(
    ctx: ReviewContext, package: DocumentationPackage, checks: Sequence[SectionCheck]
) -> list[SectionAssessment]:
    present = [c for c in checks if c.outcome == "present"]
    if not present:
        return []
    items = {i.item_id: i for i in ctx.checklist.items}
    lines = {c.item_id: _lines(package, c) for c in present}

    def validate(out: Assessments) -> list[str]:
        errors: list[str] = []
        got = [a.item_id for a in out.assessments]
        if sorted(got) != sorted(lines) or len(set(got)) != len(got):
            errors.append(f"assess each of these sections once: {', '.join(lines)}")
        for a in out.assessments:
            allowed = {(x["doc_id"], f"line {x['line']}") for x in lines.get(a.item_id, [])}
            if not a.citations:
                errors.append(f"{a.item_id}: cite the lines the reason relies on")
            bad = [c.locator for c in a.citations if (c.doc_id, c.locator) not in allowed]
            if bad:
                errors.append(f"{a.item_id}: cites lines outside its section: {', '.join(bad)}")
        return errors

    payload = {
        "model_name": package.model_name,
        "sections": [
            {
                "item_id": c.item_id,
                "title": items[c.item_id].title,
                "expects": items[c.item_id].expects,
                "lines": lines[c.item_id],
            }
            for c in present
        ],
    }
    out, _ = await call_structured(
        ctx,
        node="assessor",
        route="compliance",
        prompt=ctx.prompt("assessor"),
        payload=payload,
        schema=Assessments,
        recording_key=f"assessor/{PROMPT_VERSIONS['assessor']}/{package.package_id}",
        validate=validate,
    )
    order = {item_id: n for n, item_id in enumerate(lines)}
    return sorted(out.assessments, key=lambda a: order[a.item_id])


def report_errors(
    report: FindingsReport,
    package: DocumentationPackage,
    checks: Sequence[SectionCheck],
    assessments: Sequence[SectionAssessment],
    checklist: Checklist,
) -> list[str]:
    """What makes a drafted report unusable, in words the drafter can act on."""
    errors: list[str] = []
    headings = tuple(s.heading for s in report.sections)
    if headings != REPORT_HEADINGS:
        errors.append(f"write exactly these sections, in order: {', '.join(REPORT_HEADINGS)}")
    allowed = _allowed_lines(package, checks) | {
        (p.framework, p.provision_id) for i in checklist.items for p in i.provisions
    }
    for section in report.sections:
        for n, claim in enumerate(section.claims, 1):
            where = f"{section.heading}, claim {n}"
            if not claim.citations:
                errors.append(f"{where}: cite a document line or a provision")
            bad = [
                f"{c.doc_id} {c.locator}"
                for c in claim.citations
                if (c.doc_id, c.locator) not in allowed
            ]
            if bad:
                errors.append(f"{where}: unknown citation {', '.join(bad)}")
    by_heading = {s.heading: " ".join(c.text for c in s.claims) for s in report.sections}
    gaps = [c.item_id for c in checks if c.outcome in ("missing", "empty")]
    weak = [a.item_id for a in assessments if a.adequacy == "weak"]
    for item_id in gaps:
        if item_id not in by_heading.get("Checklist gaps", ""):
            errors.append(f"Checklist gaps: name the gap {item_id}")
    for item_id in weak:
        if item_id not in by_heading.get("Section assessments", ""):
            errors.append(f"Section assessments: name the weak section {item_id}")
    return errors


async def run_drafter(
    ctx: ReviewContext,
    package: DocumentationPackage,
    checks: Sequence[SectionCheck],
    completeness: Completeness,
    assessments: Sequence[SectionAssessment],
    status: DocumentationStatus,
    provisions: dict[str, list[RelatedProvision]],
    revision: tuple[FindingsReport, Sequence[ReviewDecision]] | None = None,
    fix: tuple[FindingsReport, list[dict[str, str]]] | None = None,
) -> FindingsReport:
    items = {i.item_id: i for i in ctx.checklist.items}
    payload: dict[str, Any] = {
        "model_name": package.model_name,
        "checklist_version": ctx.checklist.version,
        "checks": [
            {
                "item_id": c.item_id,
                "title": items[c.item_id].title,
                "outcome": c.outcome,
                "critical": c.critical,
                "provisions": [p.model_dump() for p in items[c.item_id].provisions],
            }
            for c in checks
        ],
        "completeness": {
            "present": completeness.present,
            "total": completeness.total,
            "critical_gaps": list(completeness.critical_gaps),
        },
        "assessments": [a.model_dump(mode="json") for a in assessments],
        "documentation_status": status,
        "provisions": {
            item_id: [{"citation": p.citation, "provision_id": p.provision_id} for p in found]
            for item_id, found in provisions.items()
        },
    }
    key = f"drafter/{PROMPT_VERSIONS['drafter']}/{package.package_id}"
    if revision is not None:
        previous, reviews = revision
        payload["revision"] = {
            "comments": [r.comments for r in reviews if r.decision == "revise"],
            "previous_draft": previous.model_dump(mode="json"),
        }
        key = f"{key}.r{len([r for r in reviews if r.decision == 'revise'])}"
    if fix is not None:
        previous, found = fix
        payload["fix"] = {"problems": found, "previous_draft": previous.model_dump(mode="json")}
        key = f"{key}.fix"

    report, _ = await call_structured(
        ctx,
        node="drafter",
        route="narrative",
        prompt=ctx.prompt("drafter"),
        payload=payload,
        schema=FindingsReport,
        recording_key=key,
        validate=lambda r: report_errors(r, package, checks, assessments, ctx.checklist),
    )
    return report


def provision_citations(report: FindingsReport) -> list[Citation]:
    return [
        c
        for s in report.sections
        for claim in s.claims
        for c in claim.citations
        if not c.locator.startswith("line ")
    ]
