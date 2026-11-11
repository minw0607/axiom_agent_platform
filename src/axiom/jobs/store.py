"""Job records and idempotency keys: Postgres in the stack, in memory for tests.

Submitting stores the job, its idempotency key and its first evidence event in one
transaction, so a retried request can never create a second job and no job exists without
a submission record. A review is stored the same way: the review, the job's new status and
the review_submitted event commit together. Evidence events are append-only: Postgres
rejects UPDATE, DELETE and TRUNCATE on that table.

The store also owns the job's graph checkpointer, so a paused job and its record live in
the same database.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from axiom.domain import MAX_HUMAN_REVISIONS, ReviewDecision
from axiom.evidence.log import EventDraft, EvidenceEvent, chain
from axiom.memory.checkpoint import postgres_checkpointer, serializer

JobStatus = Literal[
    "queued",
    "running",
    "awaiting_review",
    "revising",
    "finalizing",
    "approved",
    "rejected",
    "failed",
]
# A reviewed job waits in one of these until the worker resumes it.
RESUMABLE: tuple[JobStatus, ...] = ("revising", "finalizing")
IDEMPOTENCY_TTL = timedelta(hours=24)
ReviewConflict = Literal[
    "not-found", "not-awaiting-review", "separation-of-duties", "revision-limit", "key-reused"
]


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    package_id: str
    submitter: str
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error_code: str | None = None
    human_revisions: int = 0
    pending_review: ReviewDecision | None = None


class SubmitResult(BaseModel):
    job: JobRecord | None
    replayed: bool = False
    key_conflict: bool = False


class ReviewResult(BaseModel):
    job: JobRecord | None
    review: ReviewDecision | None = None
    replayed: bool = False
    conflict: ReviewConflict | None = None


def review_event(review: ReviewDecision) -> EventDraft:
    return EventDraft(
        type="review_submitted",
        actor=review.reviewer,
        payload={"round": review.round, "decision": review.decision, "comments": review.comments},
    )


def check_review(job: JobRecord, reviewer: str, decision: str) -> ReviewConflict | None:
    """The rules a review must pass, in the order they are reported."""
    if job.status != "awaiting_review":
        return "not-awaiting-review"
    if reviewer == job.submitter:
        return "separation-of-duties"
    if decision == "revise" and job.human_revisions >= MAX_HUMAN_REVISIONS:
        return "revision-limit"
    return None


class JobStore(Protocol):
    async def init(self) -> None: ...
    async def ping(self) -> bool: ...
    async def submit(
        self, submitter: str, idem_key: str, request_hash: str, package_id: str
    ) -> SubmitResult: ...
    async def get(self, job_id: str) -> JobRecord | None: ...
    async def claim_next(self) -> JobRecord | None: ...
    async def finish(
        self, job_id: str, status: JobStatus, result: dict[str, Any] | None, error_code: str | None
    ) -> None: ...
    async def append_events(self, job_id: str, drafts: list[EventDraft]) -> list[EvidenceEvent]: ...
    async def events(self, job_id: str) -> list[EvidenceEvent]: ...
    async def submit_review(
        self,
        job_id: str,
        reviewer: str,
        idem_key: str,
        request_hash: str,
        decision: str,
        comments: str,
    ) -> ReviewResult: ...
    def checkpointer(self) -> AbstractAsyncContextManager[BaseCheckpointSaver[Any]]: ...


def submitted_event(submitter: str, package_id: str, request_hash: str) -> EventDraft:
    return EventDraft(
        type="job_submitted",
        actor=submitter,
        payload={"package_id": package_id, "request_sha256": request_hash},
    )


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.keys: dict[tuple[str, str], tuple[str, str, datetime]] = {}
        self.evidence: dict[str, list[EvidenceEvent]] = {}
        self.reviews: dict[str, list[tuple[str, str, ReviewDecision]]] = {}
        self.saver = InMemorySaver(serde=serializer())
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def submit(
        self, submitter: str, idem_key: str, request_hash: str, package_id: str
    ) -> SubmitResult:
        async with self._lock:
            existing = self.keys.get((submitter, idem_key))
            if existing and _now() - existing[2] < IDEMPOTENCY_TTL:
                stored_hash, job_id, _ = existing
                if stored_hash != request_hash:
                    return SubmitResult(job=None, key_conflict=True)
                return SubmitResult(job=self.jobs[job_id], replayed=True)
            now = _now()
            job = JobRecord(
                job_id=new_job_id(),
                status="queued",
                package_id=package_id,
                submitter=submitter,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job.job_id] = job
            self.keys[(submitter, idem_key)] = (request_hash, job.job_id, now)
            self.evidence[job.job_id] = chain(
                job.job_id, [submitted_event(submitter, package_id, request_hash)], None, now
            )
            return SubmitResult(job=job)

    async def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    async def claim_next(self) -> JobRecord | None:
        async with self._lock:
            ready = sorted(
                (j for j in self.jobs.values() if j.status in ("queued", *RESUMABLE)),
                key=lambda j: j.updated_at,
            )
            if not ready:
                return None
            job = ready[0].model_copy(update={"status": "running", "updated_at": _now()})
            self.jobs[job.job_id] = job
            return job

    async def finish(
        self, job_id: str, status: JobStatus, result: dict[str, Any] | None, error_code: str | None
    ) -> None:
        async with self._lock:
            self.jobs[job_id] = self.jobs[job_id].model_copy(
                update={
                    "status": status,
                    "result": result,
                    "error_code": error_code,
                    "pending_review": None,
                    "updated_at": _now(),
                }
            )

    async def append_events(self, job_id: str, drafts: list[EventDraft]) -> list[EvidenceEvent]:
        async with self._lock:
            existing = self.evidence.setdefault(job_id, [])
            new = chain(job_id, drafts, existing[-1] if existing else None, _now())
            existing.extend(new)
            return new

    async def events(self, job_id: str) -> list[EvidenceEvent]:
        return list(self.evidence.get(job_id, []))

    async def submit_review(
        self,
        job_id: str,
        reviewer: str,
        idem_key: str,
        request_hash: str,
        decision: str,
        comments: str,
    ) -> ReviewResult:
        async with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return ReviewResult(job=None, conflict="not-found")
            for key, stored_hash, review in self.reviews.get(job_id, []):
                if key == idem_key:
                    if stored_hash != request_hash:
                        return ReviewResult(job=job, conflict="key-reused")
                    return ReviewResult(job=job, review=review, replayed=True)
            conflict = check_review(job, reviewer, decision)
            if conflict:
                return ReviewResult(job=job, conflict=conflict)
            review = ReviewDecision(
                decision=decision,  # type: ignore[arg-type]
                comments=comments,
                reviewer=reviewer,
                round=len(self.reviews.get(job_id, [])) + 1,
            )
            self.reviews.setdefault(job_id, []).append((idem_key, request_hash, review))
            job = job.model_copy(
                update={
                    "status": "revising" if decision == "revise" else "finalizing",
                    "human_revisions": job.human_revisions + (decision == "revise"),
                    "pending_review": review,
                    "updated_at": _now(),
                }
            )
            self.jobs[job_id] = job
            existing = self.evidence.setdefault(job_id, [])
            existing.extend(chain(job_id, [review_event(review)], existing[-1], _now()))
            return ReviewResult(job=job, review=review)

    @asynccontextmanager
    async def checkpointer(self) -> AsyncIterator[BaseCheckpointSaver[Any]]:
        yield self.saver


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    package_id  TEXT NOT NULL,
    submitter   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    result      JSONB,
    error_code  TEXT
);
CREATE INDEX IF NOT EXISTS jobs_queued ON jobs (created_at) WHERE status = 'queued';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS human_revisions INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pending_review JSONB;
CREATE TABLE IF NOT EXISTS reviews (
    job_id       TEXT NOT NULL REFERENCES jobs (job_id),
    round        INTEGER NOT NULL,
    reviewer     TEXT NOT NULL,
    decision     TEXT NOT NULL,
    comments     TEXT NOT NULL,
    idem_key     TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, round),
    UNIQUE (job_id, idem_key)
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    submitter    TEXT NOT NULL,
    key          TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    job_id       TEXT NOT NULL REFERENCES jobs (job_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (submitter, key)
);
CREATE TABLE IF NOT EXISTS evidence_events (
    job_id     TEXT NOT NULL REFERENCES jobs (job_id),
    seq        INTEGER NOT NULL,
    type       TEXT NOT NULL,
    actor      TEXT NOT NULL,
    at         TIMESTAMPTZ NOT NULL,
    payload    JSONB NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
CREATE OR REPLACE FUNCTION evidence_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'evidence_events is append-only';
END
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS evidence_events_no_change ON evidence_events;
CREATE TRIGGER evidence_events_no_change BEFORE UPDATE OR DELETE ON evidence_events
    FOR EACH ROW EXECUTE FUNCTION evidence_events_append_only();
DROP TRIGGER IF EXISTS evidence_events_no_truncate ON evidence_events;
CREATE TRIGGER evidence_events_no_truncate BEFORE TRUNCATE ON evidence_events
    FOR EACH STATEMENT EXECUTE FUNCTION evidence_events_append_only();
"""

_EVENT_COLUMNS = "job_id, seq, type, actor, at, payload, prev_hash, hash"


def _event(row: tuple[Any, ...]) -> EvidenceEvent:
    return EvidenceEvent(**dict(zip(_EVENT_COLUMNS.split(", "), row, strict=True)))


async def _append(conn: Any, job_id: str, drafts: list[EventDraft]) -> list[EvidenceEvent]:
    """Append within the caller's transaction, serialized per job by an advisory lock."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"evidence:{job_id}",))
    cur = await conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM evidence_events WHERE job_id = %s ORDER BY seq DESC LIMIT 1",
        (job_id,),
    )
    row = await cur.fetchone()
    new = chain(job_id, drafts, None if row is None else _event(row), _now())
    for e in new:
        await conn.execute(
            f"INSERT INTO evidence_events ({_EVENT_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (e.job_id, e.seq, e.type, e.actor, e.at, json.dumps(e.payload), e.prev_hash, e.hash),
        )
    return new


_COLUMNS = (
    "job_id, status, package_id, submitter, created_at, updated_at, result, error_code, "
    "human_revisions, pending_review"
)


def _record(row: tuple[Any, ...]) -> JobRecord:
    return JobRecord(**dict(zip(_COLUMNS.split(", "), row, strict=True)))


def _review_row(row: tuple[Any, ...]) -> ReviewDecision:
    return ReviewDecision(round=row[0], reviewer=row[1], decision=row[2], comments=row[3])


class PostgresJobStore:
    def __init__(self, url: str) -> None:
        self.url = url

    async def _connect(self) -> Any:
        import psycopg

        return await psycopg.AsyncConnection.connect(self.url)

    async def init(self) -> None:
        async with await self._connect() as conn:
            await conn.execute(SCHEMA)
            await conn.commit()

    async def ping(self) -> bool:
        try:
            async with await self._connect() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def submit(
        self, submitter: str, idem_key: str, request_hash: str, package_id: str
    ) -> SubmitResult:
        async with await self._connect() as conn, conn.transaction():
            # Serialize concurrent submissions with the same key.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{submitter}:{idem_key}",)
            )
            cur = await conn.execute(
                "SELECT request_hash, job_id, created_at FROM idempotency_keys "
                "WHERE submitter = %s AND key = %s",
                (submitter, idem_key),
            )
            row = await cur.fetchone()
            if row is not None and _now() - row[2] < IDEMPOTENCY_TTL:
                if row[0] != request_hash:
                    return SubmitResult(job=None, key_conflict=True)
                cur = await conn.execute(
                    f"SELECT {_COLUMNS} FROM jobs WHERE job_id = %s", (row[1],)
                )
                job_row = await cur.fetchone()
                return SubmitResult(job=_record(job_row), replayed=True)
            job_id = new_job_id()
            cur = await conn.execute(
                f"INSERT INTO jobs (job_id, status, package_id, submitter) "
                f"VALUES (%s, 'queued', %s, %s) RETURNING {_COLUMNS}",
                (job_id, package_id, submitter),
            )
            job_row = await cur.fetchone()
            await conn.execute(
                "INSERT INTO idempotency_keys (submitter, key, request_hash, job_id) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (submitter, key) DO UPDATE SET "
                "request_hash = EXCLUDED.request_hash, job_id = EXCLUDED.job_id, "
                "created_at = now()",
                (submitter, idem_key, request_hash, job_id),
            )
            await _append(conn, job_id, [submitted_event(submitter, package_id, request_hash)])
            return SubmitResult(job=_record(job_row))

    async def get(self, job_id: str) -> JobRecord | None:
        async with await self._connect() as conn:
            cur = await conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE job_id = %s", (job_id,))
            row = await cur.fetchone()
        return None if row is None else _record(row)

    async def claim_next(self) -> JobRecord | None:
        async with await self._connect() as conn, conn.transaction():
            cur = await conn.execute(
                f"UPDATE jobs SET status = 'running', updated_at = now() WHERE job_id = ("
                "SELECT job_id FROM jobs WHERE status IN ('queued', 'revising', 'finalizing') "
                f"ORDER BY updated_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING {_COLUMNS}"
            )
            row = await cur.fetchone()
        return None if row is None else _record(row)

    async def finish(
        self, job_id: str, status: JobStatus, result: dict[str, Any] | None, error_code: str | None
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE jobs SET status = %s, result = %s, error_code = %s, "
                "pending_review = NULL, updated_at = now() WHERE job_id = %s",
                (status, None if result is None else json.dumps(result), error_code, job_id),
            )
            await conn.commit()

    async def append_events(self, job_id: str, drafts: list[EventDraft]) -> list[EvidenceEvent]:
        async with await self._connect() as conn, conn.transaction():
            return await _append(conn, job_id, drafts)

    async def events(self, job_id: str) -> list[EvidenceEvent]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM evidence_events WHERE job_id = %s ORDER BY seq",
                (job_id,),
            )
            rows = await cur.fetchall()
        return [_event(r) for r in rows]

    async def submit_review(
        self,
        job_id: str,
        reviewer: str,
        idem_key: str,
        request_hash: str,
        decision: str,
        comments: str,
    ) -> ReviewResult:
        async with await self._connect() as conn, conn.transaction():
            # Lock the job row: concurrent reviews of one job are serialized here.
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,)
            )
            row = await cur.fetchone()
            if row is None:
                return ReviewResult(job=None, conflict="not-found")
            job = _record(row)
            cur = await conn.execute(
                "SELECT round, reviewer, decision, comments, request_hash FROM reviews "
                "WHERE job_id = %s AND idem_key = %s",
                (job_id, idem_key),
            )
            prior = await cur.fetchone()
            if prior is not None:
                if prior[4] != request_hash:
                    return ReviewResult(job=job, conflict="key-reused")
                return ReviewResult(job=job, review=_review_row(prior), replayed=True)
            conflict = check_review(job, reviewer, decision)
            if conflict:
                return ReviewResult(job=job, conflict=conflict)
            cur = await conn.execute("SELECT count(*) FROM reviews WHERE job_id = %s", (job_id,))
            count = await cur.fetchone()
            review = ReviewDecision(
                decision=decision,  # type: ignore[arg-type]
                comments=comments,
                reviewer=reviewer,
                round=int(count[0]) + 1 if count else 1,
            )
            await conn.execute(
                "INSERT INTO reviews (job_id, round, reviewer, decision, comments, idem_key, "
                "request_hash) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (job_id, review.round, reviewer, decision, comments, idem_key, request_hash),
            )
            cur = await conn.execute(
                "UPDATE jobs SET status = %s, human_revisions = human_revisions + %s, "
                f"pending_review = %s, updated_at = now() WHERE job_id = %s RETURNING {_COLUMNS}",
                (
                    "revising" if decision == "revise" else "finalizing",
                    int(decision == "revise"),
                    review.model_dump_json(),
                    job_id,
                ),
            )
            updated = await cur.fetchone()
            await _append(conn, job_id, [review_event(review)])
            return ReviewResult(job=_record(updated) if updated else job, review=review)

    def checkpointer(self) -> AbstractAsyncContextManager[BaseCheckpointSaver[Any]]:
        return postgres_checkpointer(self.url)
