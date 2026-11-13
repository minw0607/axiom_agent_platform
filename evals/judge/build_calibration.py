"""Build the judge calibration set v1 from the live recorded drafts.

    uv run python evals/judge/build_calibration.py

30 items: 20 citation-support items (a claim and the source lines it cites) and 10
key-finding items (a memo and a finding). Seven citation items are altered on purpose (a
changed number, a citation moved to the wrong line, an unsupported clause added) and four
finding items pair a memo with another package's finding, so the set holds both answers.
How each item was built is kept in `built_as`; the labelling page never shows it, so the
labels are blind. Output: evals/judge/v1/calibration.json. Re-running gives the same file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from platform_outputs import platform_outputs

from axiom.catalog import ground_truth_statements, load_ground_truth, load_package
from axiom.settings import REPO_ROOT, Settings
from axiom.tools.policy import load_policy, risk_flags
from axiom.tools.ratios import compute_ratios
from axiom.tools.reconcile import reconcile

OUT = Path(__file__).parent / "v1" / "calibration.json"
LIVE = REPO_ROOT / "data" / "recorded" / "drafter" / "v3"
SETTINGS = Settings.from_env()
PACKAGES = [f"pkg-{i:02d}" for i in range(1, 11)]  # the packages with live drafts
BIG_NUMBER = re.compile(r"\b\d{5,}\b")


def live_draft(package_id: str) -> dict[str, Any]:
    raw = json.loads((LIVE / f"{package_id}.json").read_text())
    draft: dict[str, Any] = json.loads(raw["text"])
    return draft


def line_text(package_id: str, doc_id: str, locator: str) -> str:
    pkg = load_package(SETTINGS.packages_dir, package_id)
    doc = next(d for d in pkg.documents if d.doc_id == doc_id)
    return doc.text.split("\n")[int(locator.removeprefix("line ")) - 1]


def line_cited_claims(package_id: str) -> list[dict[str, Any]]:
    """Two claims per package: a judgement claim (a risk factor, or a ratio statement) and a
    figures claim, so the set holds hard cases as well as easy ones."""
    by_section: dict[str, list[dict[str, Any]]] = {}
    for section in live_draft(package_id)["sections"]:
        for claim in section["claims"]:
            cites = claim["citations"]
            if cites and all(c["locator"].startswith("line ") for c in cites):
                by_section.setdefault(section["heading"], []).append(claim)
    analysis = by_section.get("Financial analysis", [])
    ratio = [c for c in analysis if "ratio" in c["text"].lower() or "dscr" in c["text"].lower()]
    judgement = (
        by_section.get("Risk factors") or ratio or by_section.get("Borrower and request", [])
    )
    figures = [c for c in analysis if c not in judgement[:1]] or by_section.get(
        "Borrower and request", []
    )
    return judgement[:1] + figures[:1]


def platform(package_id: str) -> list[str]:
    """The tool outputs the drafter was given, computed from the package's true figures."""
    fs = ground_truth_statements(load_ground_truth(SETTINGS.packages_dir, package_id))
    ratios, policy = compute_ratios(fs), load_policy(SETTINGS.config_dir / "policy_thresholds.yaml")
    return list(platform_outputs(ratios, risk_flags(fs, ratios, policy), reconcile(fs), policy))


def sources(package_id: str, cites: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "doc_id": c["doc_id"],
            "locator": c["locator"],
            "text": line_text(package_id, c["doc_id"], c["locator"]),
        }
        for c in cites
    ]


def perturb(kind: str, package_id: str, claim: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    text, cites = claim["text"], claim["citations"]
    if kind == "changed_number":
        m = BIG_NUMBER.search(text)
        if m:
            wrong = str(int(m.group()) + int(m.group()) // 9)
            return text[: m.start()] + wrong + text[m.end() :], cites
        kind = "unsupported_clause"
    if kind == "wrong_line":
        doc = cites[0]["doc_id"]
        n = int(cites[0]["locator"].removeprefix("line "))
        lines = load_package(SETTINGS.packages_dir, package_id).documents
        total = len(next(d for d in lines if d.doc_id == doc).text.split("\n"))
        # The table's first data row when the claim cites a later one, else the last row.
        other = 8 if n != 8 else max(9, total - 1)
        return text, [{"doc_id": doc, "locator": f"line {other}"}]
    return text.rstrip(
        "."
    ) + ", and revenue has grown in every year since the business was founded.", cites


def citation_items() -> list[dict[str, Any]]:
    kinds = ["changed_number", "wrong_line", "unsupported_clause"]
    items: list[dict[str, Any]] = []
    altered = 0
    for pid in PACKAGES:
        for claim in line_cited_claims(pid)[:2]:
            n = len(items)
            if n % 3 == 1 and altered < 7:  # items 2, 5, 8, ... are altered
                kind = kinds[altered % 3]
                text, cites = perturb(kind, pid, claim)
                altered += 1
            else:
                kind, text, cites = "as_drafted", claim["text"], claim["citations"]
            items.append(
                {
                    "item_id": f"cit-{n + 1:02d}",
                    "type": "citation",
                    "package_id": pid,
                    "claim": text,
                    "sources": sources(pid, cites),
                    "platform": platform(pid),
                    "built_as": kind,
                }
            )
    return items[:20]


def finding_items() -> list[dict[str, Any]]:
    items = []
    for n, pid in enumerate(PACKAGES):
        memo = [c["text"] for s in live_draft(pid)["sections"] for c in s["claims"]]
        own = n % 5 not in (1, 3)  # 6 of 10 use the package's own finding
        source = pid if own else PACKAGES[(n + 3) % len(PACKAGES)]
        finding = load_ground_truth(SETTINGS.packages_dir, source)["golden"]["key_findings"][0]
        items.append(
            {
                "item_id": f"kf-{n + 1:02d}",
                "type": "finding",
                "package_id": pid,
                "finding": finding["text"],
                "memo": memo,
                "built_as": "own_finding" if own else f"finding_from_{source}",
            }
        )
    return items


def main() -> None:
    items = citation_items() + finding_items()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"version": 1, "items": items}, indent=2) + "\n")
    built = [i["built_as"] for i in items]
    counts = {
        "citations as drafted": sum(b == "as_drafted" for b in built),
        "altered": sum(b in ("changed_number", "wrong_line", "unsupported_clause") for b in built),
        "own findings": sum(b == "own_finding" for b in built),
        "mismatched": sum(b.startswith("finding_from") for b in built),
    }
    print(f"{len(items)} items -> {OUT}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
