"""Credit policy clauses for the review page's "Explain this". No model is involved."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

CLAUSE = re.compile(r"^(CP-\d+\.\d+) (.+)$")
SECTION = re.compile(r"^## (CP-\d+) (.+)$")
VERSION = re.compile(r"Policy version (CP-\d+(?:\.\d+)*)")
POLICY_REF = re.compile(r"CP-\d+(\.\d+)?")


class PolicyClause(BaseModel):
    ref: str
    policy_version: str
    section: str
    text: str
    clauses: list[str] = Field(
        default_factory=list, description="For a section reference, the clauses it holds"
    )


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
