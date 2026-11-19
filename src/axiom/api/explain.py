"""The lookup behind the review page's "Explain this": the source text of one citation.

No model is involved. A citation is resolved from the job's input documents (for "line N") or
from the passages the job stored when it retrieved them (for a passage id). Each excerpt
says whether its text still matches the fingerprint (SHA-256) recorded during the job, so
a reader can tell the excerpt is what the platform used.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from axiom.core.types import Document

Fingerprint = Literal["matches", "differs", "not_recorded"]
LINE = re.compile(r"line (\d{1,5})")


class HasDocuments(Protocol):
    """A job's input, whatever the app: it holds the documents a draft cites."""

    @property
    def documents(self) -> Sequence[Document]: ...


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
    package: HasDocuments, result: dict[str, Any], doc_id: str, locator: str
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
