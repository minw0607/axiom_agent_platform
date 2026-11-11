"""Evidence retrieval: GKN over MCP, or an in-memory stand-in for tests and offline runs.

The platform sets job_id and collection from workflow state; a model never chooses them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from axiom.domain import Document
from axiom.mcp_clients.client import MCPToolClient

Collection = Literal["job", "credit_policy", "lending_refs"]


class EvidenceHit(BaseModel):
    collection: str
    doc_id: str
    chunk_id: str
    title: str
    text: str
    text_hash: str
    score: float


class EvidenceService(Protocol):
    async def ingest(self, job_id: str, documents: Sequence[Document]) -> dict[str, str]: ...

    async def retrieve(
        self, job_id: str, query: str, k: int = 5, collection: Collection = "job"
    ) -> list[EvidenceHit]: ...

    async def delete(self, job_id: str) -> None: ...


class GKNEvidence:
    """geometric_knowledge_network's MCP tools."""

    def __init__(self, client: MCPToolClient) -> None:
        self.client = client

    async def ingest(self, job_id: str, documents: Sequence[Document]) -> dict[str, str]:
        result = await self.client.call(
            "ingest_documents",
            {
                "job_id": job_id,
                "documents": [
                    {"doc_id": d.doc_id, "text": d.text, "title": d.title, "source": d.kind}
                    for d in documents
                ],
            },
        )
        return {r["doc_id"]: r["text_hash"] for r in result["ingested"]}

    async def retrieve(
        self, job_id: str, query: str, k: int = 5, collection: Collection = "job"
    ) -> list[EvidenceHit]:
        result = await self.client.call(
            "retrieve_evidence",
            {"query": query, "job_id": job_id, "k": k, "collection": collection},
        )
        return [EvidenceHit(**hit) for hit in result["hits"]]

    async def delete(self, job_id: str) -> None:
        await self.client.call("delete_job", {"job_id": job_id})


_WORD = re.compile(r"[a-z0-9]+")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _chunks(collection: str, doc_id: str, title: str, text: str) -> list[EvidenceHit]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [
        EvidenceHit(
            collection=collection,
            doc_id=doc_id,
            chunk_id=f"{doc_id}#{i}",
            title=title,
            text=para,
            text_hash=_sha(para),
            score=0.0,
        )
        for i, para in enumerate(paras)
    ]


class InMemoryEvidence:
    """Paragraph chunks scored by word overlap. Job-scoped, like GKN.

    Reference collections are read-only and seeded from folders the way GKN seeds them:
    every .md and .txt file except a README, with the file stem as the doc_id.
    """

    def __init__(self, references: Mapping[str, Path] | None = None) -> None:
        self._jobs: dict[str, list[EvidenceHit]] = {}
        self._refs: dict[str, list[EvidenceHit]] = {}
        for collection, folder in (references or {}).items():
            files = sorted([*folder.glob("*.md"), *folder.glob("*.txt")])
            self._refs[collection] = [
                chunk
                for path in files
                if path.stem.lower() != "readme"
                for chunk in _chunks(
                    collection,
                    path.stem,
                    path.stem.replace("_", " ").title(),
                    path.read_text(encoding="utf-8"),
                )
            ]

    async def ingest(self, job_id: str, documents: Sequence[Document]) -> dict[str, str]:
        chunks = self._jobs.setdefault(job_id, [])
        for doc in documents:
            chunks[:] = [c for c in chunks if c.doc_id != doc.doc_id]
            chunks.extend(_chunks("job", doc.doc_id, doc.title, doc.text))
        return {d.doc_id: _sha(d.text) for d in documents}

    async def retrieve(
        self, job_id: str, query: str, k: int = 5, collection: Collection = "job"
    ) -> list[EvidenceHit]:
        pool = self._jobs.get(job_id, []) if collection == "job" else self._refs.get(collection, [])
        words = set(_WORD.findall(query.lower()))
        scored = []
        for chunk in pool:
            overlap = len(words & set(_WORD.findall(chunk.text.lower())))
            if overlap:
                scored.append(chunk.model_copy(update={"score": overlap / max(len(words), 1)}))
        scored.sort(key=lambda c: (-c.score, c.chunk_id))
        return scored[:k]

    async def delete(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
