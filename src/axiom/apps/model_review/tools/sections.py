"""Split a document into its `## ` sections, with line numbers a citation can use."""

from __future__ import annotations

import re

from axiom.apps.model_review.domain import Section
from axiom.core.types import Document

HEADING = re.compile(r"^##\s+(.+?)\s*$")
TOP = re.compile(r"^#\s")
# Leading numbering such as "3.", "3.1" or "3)" is not part of a heading's name.
NUMBERING = re.compile(r"^\d+(\.\d+)*[.)]?\s+")


def normalize(heading: str) -> str:
    return " ".join(NUMBERING.sub("", heading.strip()).lower().split()).rstrip(":")


def sections(document: Document) -> list[Section]:
    lines = document.text.splitlines()
    found: list[Section] = []
    i = 0
    while i < len(lines):
        m = HEADING.match(lines[i])
        if not m:
            i += 1
            continue
        heading_line = i + 1
        j = i + 1
        while j < len(lines) and not HEADING.match(lines[j]) and not TOP.match(lines[j]):
            j += 1
        body = range(i + 1, j)  # 0-based indexes of the lines under the heading
        content = [k for k in body if lines[k].strip()]
        found.append(
            Section(
                doc_id=document.doc_id,
                heading=m.group(1),
                heading_line=heading_line,
                first_line=content[0] + 1 if content else heading_line,
                last_line=content[-1] + 1 if content else heading_line,
                content_lines=len(content),
            )
        )
        i = j
    return found
