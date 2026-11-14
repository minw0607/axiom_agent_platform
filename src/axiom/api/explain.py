"""Lookups behind the review page's "Explain this": a cited source excerpt and a policy clause.

No model is involved. A citation is resolved from the borrower package (for "line N") or
from the passages the job stored when it retrieved them (for a passage id). Each excerpt
says whether its text still matches the fingerprint (SHA-256) recorded during the job, so
a reader can tell the excerpt is what the platform used.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from axiom.domain import BorrowerPackage

Fingerprint = Literal["matches", "differs", "not_recorded"]
LINE = re.compile(r"line (\d{1,5})")
CLAUSE = re.compile(r"^(CP-\d+\.\d+) (.+)$")
SECTION = re.compile(r"^## (CP-\d+) (.+)$")
VERSION = re.compile(r"Policy version (CP-\d+(?:\.\d+)*)")
POLICY_REF = re.compile(r"CP-\d+(\.\d+)?")


class SourceExcerpt(BaseModel):
    doc_id: str
    title: str
    locator: str
    kind: Literal["line", "passage"]
    line: int | None = None
    text: str
    header: str | None = Field(
        default=None, description="For a table row, the table's header row (which years)"
    )
    fingerprint: Fingerprint
    note: str = ""


class PolicyClause(BaseModel):
    ref: str
    policy_version: str
    section: str
    text: str
    clauses: list[str] = Field(
        default_factory=list, description="For a section reference, the clauses it holds"
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _table_header(lines: list[str], index: int) -> str | None:
    """The header row of the table that line `index` (0-based) sits in, if any."""
    if not lines[index].lstrip().startswith("|"):
        return None
    i = index
    while i > 0 and lines[i - 1].lstrip().startswith("|"):
        i -= 1
    header = lines[i]
    is_rule = i + 1 < len(lines) and set(lines[i + 1].replace("|", "").strip()) <= {"-", ":", " "}
    return header if is_rule and i != index else None


def source_excerpt(
    package: BorrowerPackage, result: dict[str, Any], doc_id: str, locator: str
) -> SourceExcerpt | None:
    """The cited text, or None when the citation does not resolve in this job."""
    redacted = bool(result.get("pii_redacted"))
    m = LINE.fullmatch(locator)
    if m:
        doc = next((d for d in package.documents if d.doc_id == doc_id), None)
        if doc is None:
            return None
        lines = doc.text.splitlines()
        n = int(m.group(1))
        if not 1 <= n <= len(lines):
            return None
        recorded = (result.get("source_hashes") or {}).get(doc_id)
        fingerprint: Fingerprint
        if recorded is None:
            fingerprint, note = (
                "not_recorded",
                "The job did not record this document's fingerprint.",
            )
        elif recorded == _sha(doc.text):
            fingerprint, note = "matches", ""
        elif redacted:
            fingerprint = "differs"
            note = (
                "Personal identifiers were masked before any AI read the package, so the "
                "fingerprint recorded is of the masked document. This is the document as submitted."
            )
        else:
            fingerprint = "differs"
            note = "The document has changed since the job read it."
        return SourceExcerpt(
            doc_id=doc_id,
            title=doc.title,
            locator=locator,
            kind="line",
            line=n,
            text=lines[n - 1],
            header=_table_header(lines, n - 1),
            fingerprint=fingerprint,
            note=note,
        )
    for hit in [*result.get("evidence", []), *result.get("reference_evidence", [])]:
        if hit.get("chunk_id") == locator and hit.get("doc_id") == doc_id:
            text = hit.get("text", "")
            return SourceExcerpt(
                doc_id=doc_id,
                title=hit.get("title") or doc_id,
                locator=locator,
                kind="passage",
                text=text,
                fingerprint="matches" if _sha(text) == hit.get("text_hash") else "differs",
                note="A passage the platform retrieved while drafting.",
            )
    return None


@lru_cache(maxsize=4)
def _policy(path: Path, mtime: float) -> tuple[str, dict[str, PolicyClause]]:
    text = path.read_text()
    version_match = VERSION.search(text)
    version = version_match.group(1) if version_match else "unknown"
    clauses: dict[str, PolicyClause] = {}
    section_ref, section_title = "", ""
    for line in text.splitlines():
        if s := SECTION.match(line):
            section_ref, section_title = s.group(1), s.group(2)
            clauses[section_ref] = PolicyClause(
                ref=section_ref, policy_version=version, section=section_title, text=section_title
            )
        elif c := CLAUSE.match(line):
            body = c.group(2).replace("**", "")
            clauses[c.group(1)] = PolicyClause(
                ref=c.group(1), policy_version=version, section=section_title, text=body
            )
            if section_ref:
                clauses[section_ref].clauses.append(c.group(1))
    return version, clauses


def policy_clause(policy_dir: Path, ref: str) -> PolicyClause | None:
    path = policy_dir / "credit_policy.md"
    if not POLICY_REF.fullmatch(ref) or not path.is_file():
        return None
    return _policy(path, path.stat().st_mtime)[1].get(ref)
