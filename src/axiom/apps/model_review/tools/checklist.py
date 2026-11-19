"""The required-section checklist and the completeness score. Code, not a model, decides
whether a section is present; the score is the share of items present (ADR 0016, rule 6)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from axiom.apps.model_review.domain import (
    Checklist,
    ChecklistItem,
    Completeness,
    DocumentationPackage,
    DocumentationStatus,
    ProvisionRef,
    SectionAssessment,
    SectionCheck,
)
from axiom.apps.model_review.tools.sections import normalize, sections


def load_checklist(path: Path) -> Checklist:
    raw = yaml.safe_load(path.read_text())
    items = tuple(
        ChecklistItem(
            item_id=str(i["id"]),
            title=str(i["title"]),
            expects=str(i["expects"]),
            document=i["document"],
            headings=tuple(normalize(h) for h in i["headings"]),
            critical=bool(i["critical"]),
            provisions=tuple(ProvisionRef(**p) for p in i["provisions"]),
        )
        for i in raw["items"]
    )
    ids = [i.item_id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("checklist item IDs must be unique")
    return Checklist(version=str(raw["version"]), min_lines=int(raw["min_lines"]), items=items)


def check_package(package: DocumentationPackage, checklist: Checklist) -> list[SectionCheck]:
    """One result per checklist item, in checklist order. A section is looked for only in
    the document the item names; the first matching heading counts."""
    by_kind = {d.kind: sections(d) for d in package.documents}
    results: list[SectionCheck] = []
    for item in checklist.items:
        match = next(
            (s for s in by_kind.get(item.document, []) if normalize(s.heading) in item.headings),
            None,
        )
        if match is None:
            results.append(
                SectionCheck(item_id=item.item_id, outcome="missing", critical=item.critical)
            )
            continue
        results.append(
            SectionCheck(
                item_id=item.item_id,
                outcome="present" if match.content_lines >= checklist.min_lines else "empty",
                critical=item.critical,
                doc_id=match.doc_id,
                heading=match.heading,
                first_line=match.first_line,
                last_line=match.last_line,
            )
        )
    return results


def completeness(checks: list[SectionCheck], checklist: Checklist) -> Completeness:
    present = sum(c.outcome == "present" for c in checks)
    total = len(checklist.items)
    return Completeness(
        checklist_version=checklist.version,
        present=present,
        total=total,
        score=(Decimal(present) / Decimal(total)).quantize(Decimal("0.01"), ROUND_HALF_UP),
        empty=tuple(c.item_id for c in checks if c.outcome == "empty"),
        missing=tuple(c.item_id for c in checks if c.outcome == "missing"),
        critical_gaps=tuple(c.item_id for c in checks if c.critical and c.outcome != "present"),
    )


def documentation_status(
    completeness: Completeness, assessments: Sequence[SectionAssessment]
) -> DocumentationStatus:
    """A critical gap is a major gap; any other gap or a weak section needs remediation."""
    if completeness.critical_gaps:
        return "major_gaps"
    if completeness.empty or completeness.missing or any(a.adequacy == "weak" for a in assessments):
        return "remediation_needed"
    return "no_material_gaps"
