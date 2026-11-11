"""Job records and idempotency keys: Postgres in the stack, in memory for tests.

Submitting stores the job and its idempotency key in one transaction, so a retried
request can never create a second job.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel

JobStatus = Literal["queued", "running", "awaiting_review", "failed"]
IDEMPOTENCY_TTL = timedelta(hours=24)


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    package_id: str
    submitter: str
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error_code: str | None = None


class SubmitResult(BaseModel):
    job: JobRecord | None
    replayed: bool = False
    key_conflict: bool = False


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


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.keys: dict[tuple[str, str], tuple[str, str, datetime]] = {}
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
            return SubmitResult(job=job)

    async def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    async def claim_next(self) -> JobRecord | None:
        async with self._lock:
            queued = sorted(
                (j for j in self.jobs.values() if j.status == "queued"), key=lambda j: j.created_at
            )
            if not queued:
                return None
            job = queued[0].model_copy(update={"status": "running", "updated_at": _now()})
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
                    "updated_at": _now(),
                }
            )


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
CREATE TABLE IF NOT EXISTS idempotency_keys (
    submitter    TEXT NOT NULL,
    key          TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    job_id       TEXT NOT NULL REFERENCES jobs (job_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (submitter, key)
);
"""

_COLUMNS = "job_id, status, package_id, submitter, created_at, updated_at, result, error_code"


def _record(row: tuple[Any, ...]) -> JobRecord:
    return JobRecord(**dict(zip(_COLUMNS.split(", "), row, strict=True)))


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
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at "
                f"FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING {_COLUMNS}"
            )
            row = await cur.fetchone()
        return None if row is None else _record(row)

    async def finish(
        self, job_id: str, status: JobStatus, result: dict[str, Any] | None, error_code: str | None
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE jobs SET status = %s, result = %s, error_code = %s, updated_at = now() "
                "WHERE job_id = %s",
                (status, None if result is None else json.dumps(result), error_code, job_id),
            )
            await conn.commit()
