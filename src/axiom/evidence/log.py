"""Append-only, hash-chained evidence events (architecture principle: evidence by default).

Each job has its own chain. An event's hash covers its content and the previous event's
hash, so changing, removing or reordering any past event breaks every later link. Postgres
also refuses UPDATE and DELETE on the table (see jobs/store.py).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

GENESIS = "0" * 64

EventType = Literal[
    "job_submitted",
    "job_started",
    "model_call",
    "node_completed",
    "node_failed",
    "job_awaiting_review",
    "job_failed",
]


class EventDraft(BaseModel):
    """An event before it joins the chain: what happened, who did it, and the details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EventType
    actor: str
    payload: dict[str, Any]


class EvidenceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    seq: int
    type: EventType
    actor: str
    at: datetime
    payload: dict[str, Any]
    prev_hash: str
    hash: str


def event_hash(
    job_id: str,
    seq: int,
    type: str,
    actor: str,
    at: datetime,
    payload: dict[str, Any],
    prev_hash: str,
) -> str:
    body = {
        "job_id": job_id,
        "seq": seq,
        "type": type,
        "actor": actor,
        "at": at.isoformat(),
        "payload": payload,
        "prev_hash": prev_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def chain(
    job_id: str, drafts: list[EventDraft], after: EvidenceEvent | None, at: datetime
) -> list[EvidenceEvent]:
    """Link new events onto the end of a job's chain."""
    seq = after.seq if after else 0
    prev = after.hash if after else GENESIS
    out: list[EvidenceEvent] = []
    for d in drafts:
        seq += 1
        # Round-trip the payload through JSON so the stored and hashed forms are identical.
        payload = json.loads(json.dumps(d.payload, default=str))
        h = event_hash(job_id, seq, d.type, d.actor, at, payload, prev)
        out.append(
            EvidenceEvent(
                job_id=job_id,
                seq=seq,
                type=d.type,
                actor=d.actor,
                at=at,
                payload=payload,
                prev_hash=prev,
                hash=h,
            )
        )
        prev = h
    return out


class ChainCheck(BaseModel):
    verified: bool
    events: int
    first_broken_seq: int | None = None


def verify_chain(events: list[EvidenceEvent]) -> ChainCheck:
    """Recompute every hash and link. Fails at the first event that does not match."""
    prev = GENESIS
    for expected_seq, e in enumerate(sorted(events, key=lambda e: e.seq), 1):
        recomputed = event_hash(e.job_id, e.seq, e.type, e.actor, e.at, e.payload, e.prev_hash)
        if e.seq != expected_seq or e.prev_hash != prev or e.hash != recomputed:
            return ChainCheck(verified=False, events=len(events), first_broken_seq=e.seq)
        prev = e.hash
    return ChainCheck(verified=True, events=len(events))
