"""PostgresJobStore against a real Postgres, in a throwaway schema.

Skipped when no database is reachable. CI runs it with a Postgres service container.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest

from axiom.evidence.log import EventDraft, verify_chain
from axiom.jobs.store import PostgresJobStore

BASE_URL = os.environ.get(
    "AXIOM_TEST_DATABASE_URL", "postgresql://axiom:axiom@127.0.0.1:5432/axiom"
)


def _reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(BASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="Postgres not reachable")


@pytest.fixture
def store() -> Iterator[PostgresJobStore]:
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in BASE_URL else "?"
    s = PostgresJobStore(f"{BASE_URL}{sep}options=-csearch_path%3D{schema}")
    asyncio.run(s.init())
    try:
        yield s
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


def test_submit_replay_and_conflict(store: PostgresJobStore) -> None:
    first = asyncio.run(store.submit("rm-a", "k1", "hash-1", "pkg-01"))
    again = asyncio.run(store.submit("rm-a", "k1", "hash-1", "pkg-01"))
    clash = asyncio.run(store.submit("rm-a", "k1", "hash-2", "pkg-02"))
    other = asyncio.run(store.submit("rm-b", "k1", "hash-2", "pkg-02"))
    assert first.job is not None and again.job is not None and other.job is not None
    assert again.replayed and again.job.job_id == first.job.job_id
    assert clash.key_conflict and clash.job is None
    assert other.job.job_id != first.job.job_id


def test_concurrent_same_key_creates_one_job(store: PostgresJobStore) -> None:
    async def race() -> set[str]:
        results = await asyncio.gather(
            *(store.submit("rm-a", "race", "h", "pkg-01") for _ in range(8))
        )
        return {r.job.job_id for r in results if r.job}

    assert len(asyncio.run(race())) == 1


def test_claim_is_exclusive_and_finish_stores_result(store: PostgresJobStore) -> None:
    for i in range(3):
        asyncio.run(store.submit("rm-a", f"k{i}", f"h{i}", "pkg-01"))

    async def claim_all() -> list[str]:
        claimed = await asyncio.gather(*(store.claim_next() for _ in range(5)))
        return [j.job_id for j in claimed if j is not None]

    ids = asyncio.run(claim_all())
    assert len(ids) == len(set(ids)) == 3
    asyncio.run(store.finish(ids[0], "awaiting_review", {"draft": {"x": 1}}, None))
    job = asyncio.run(store.get(ids[0]))
    assert job is not None and job.status == "awaiting_review" and job.result == {"draft": {"x": 1}}
    assert asyncio.run(store.ping())


def test_evidence_chain_survives_the_database_round_trip(store: PostgresJobStore) -> None:
    job = asyncio.run(store.submit("rm-a", "ev", "h", "pkg-01")).job
    assert job is not None
    drafts = [
        EventDraft(type="model_call", actor="worker", payload={"tokens": 12, "valid": True}),
        EventDraft(type="node_failed", actor="worker", payload={"node": "intake", "error": "x"}),
    ]
    asyncio.run(store.append_events(job.job_id, drafts))
    events = asyncio.run(store.events(job.job_id))
    assert [e.type for e in events] == ["job_submitted", "model_call", "node_failed"]
    assert verify_chain(events).verified


def test_concurrent_appends_keep_one_unbroken_chain(store: PostgresJobStore) -> None:
    job = asyncio.run(store.submit("rm-a", "cc", "h", "pkg-01")).job
    assert job is not None

    async def race() -> None:
        await asyncio.gather(
            *(
                store.append_events(
                    job.job_id, [EventDraft(type="model_call", actor="w", payload={"i": i})]
                )
                for i in range(8)
            )
        )

    asyncio.run(race())
    events = asyncio.run(store.events(job.job_id))
    assert len(events) == 9 and verify_chain(events).verified


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE evidence_events SET actor = 'someone'",
        "DELETE FROM evidence_events",
        "TRUNCATE evidence_events CASCADE",
    ],
)
def test_the_database_refuses_to_change_evidence(store: PostgresJobStore, statement: str) -> None:
    import psycopg

    asyncio.run(store.submit("rm-a", "ro", "h", "pkg-01"))

    async def attempt() -> None:
        async with await store._connect() as conn:
            await conn.execute(statement)

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        asyncio.run(attempt())
