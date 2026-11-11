"""Evidence retrieval: GKN over MCP, or an in-memory stand-in for tests and offline runs.

The platform sets job_id and collection from workflow state; a model never chooses them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
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


class InMemoryEvidence:
    """Paragraph chunks scored by word overlap. Job-scoped, like GKN."""

    def __init__(self) -> None:
        self._jobs: dict[str, list[EvidenceHit]] = {}

    async def ingest(self, job_id: str, documents: Sequence[Document]) -> dict[str, str]:
        chunks = self._jobs.setdefault(job_id, [])
        for doc in documents:
            chunks[:] = [c for c in chunks if c.doc_id != doc.doc_id]
            for i, para in enumerate(p for p in doc.text.split("\n\n") if p.strip()):
                chunks.append(
                    EvidenceHit(
                        collection="job",
                        doc_id=doc.doc_id,
                        chunk_id=f"{doc.doc_id}#{i}",
                        title=doc.title,
                        text=para.strip(),
                        text_hash=_sha(para.strip()),
                        score=0.0,
                    )
                )
        return {d.doc_id: _sha(d.text) for d in documents}

    async def retrieve(
        self, job_id: str, query: str, k: int = 5, collection: Collection = "job"
    ) -> list[EvidenceHit]:
        if collection != "job":
            return []
        words = set(_WORD.findall(query.lower()))
        scored = []
        for chunk in self._jobs.get(job_id, []):
            overlap = len(words & set(_WORD.findall(chunk.text.lower())))
            if overlap:
                scored.append(chunk.model_copy(update={"score": overlap / max(len(words), 1)}))
        scored.sort(key=lambda c: (-c.score, c.chunk_id))
        return scored[:k]

    async def delete(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
