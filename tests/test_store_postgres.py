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


# Human review against Postgres ------------------------------------------------------------


def test_review_round_trip_and_rules(store: PostgresJobStore) -> None:
    job = asyncio.run(store.submit("rm-a", "k1", "h", "pkg-01")).job
    assert job is not None
    early = asyncio.run(store.submit_review(job.job_id, "co-b", "r1", "h1", "approve", ""))
    assert early.conflict == "not-awaiting-review"
    asyncio.run(store.finish(job.job_id, "awaiting_review", {"draft": {}}, None))
    own = asyncio.run(store.submit_review(job.job_id, "rm-a", "r1", "h1", "approve", ""))
    assert own.conflict == "separation-of-duties"
    ok = asyncio.run(store.submit_review(job.job_id, "co-b", "r1", "h1", "revise", "more"))
    assert ok.conflict is None and ok.review is not None and ok.job is not None
    assert ok.job.status == "revising" and ok.job.human_revisions == 1
    assert ok.job.pending_review == ok.review
    again = asyncio.run(store.submit_review(job.job_id, "co-b", "r1", "h1", "revise", "more"))
    assert again.replayed and again.review == ok.review
    clash = asyncio.run(store.submit_review(job.job_id, "co-b", "r1", "h2", "reject", ""))
    assert clash.conflict == "key-reused"
    claimed = asyncio.run(store.claim_next())
    assert claimed is not None and claimed.pending_review == ok.review
    events = asyncio.run(store.events(job.job_id))
    assert [e.type for e in events] == ["job_submitted", "review_submitted"]
    assert events[-1].actor == "co-b" and verify_chain(events).verified


def test_a_paused_job_resumes_through_a_new_store(store: PostgresJobStore) -> None:
    """The worker that drafted the memo and the one that finalizes it share only Postgres."""
    from dataclasses import replace

    from axiom.jobs.worker import build_context, run_once
    from conftest import SETTINGS

    settings = replace(SETTINGS, mode="demo", gkn_mcp_url=None)
    job = asyncio.run(store.submit("rm-a", "k1", "h", "pkg-01")).job
    assert job is not None
    assert asyncio.run(run_once(store, build_context(settings), settings))
    paused = asyncio.run(store.get(job.job_id))
    assert paused is not None and paused.status == "awaiting_review"

    restarted = PostgresJobStore(store.url)  # nothing carried over but the database
    review = asyncio.run(restarted.submit_review(job.job_id, "co-b", "r1", "h1", "approve", "ok"))
    assert review.conflict is None
    assert asyncio.run(run_once(restarted, build_context(settings), settings))
    done = asyncio.run(restarted.get(job.job_id))
    assert done is not None and done.status == "approved", done.error_code
    events = asyncio.run(restarted.events(job.job_id))
    assert events[-1].type == "job_approved" and verify_chain(events).verified


def test_list_jobs_filters_by_submitter_and_status(store: PostgresJobStore) -> None:
    a = asyncio.run(store.submit("rm-a", "k1", "h1", "pkg-01")).job
    b = asyncio.run(store.submit("rm-b", "k1", "h2", "pkg-02")).job
    assert a is not None and b is not None
    result = {"draft": {"draft_recommendation": "approve", "sections": []}}
    asyncio.run(store.finish(a.job_id, "awaiting_review", result, None))
    everyone = asyncio.run(store.list_jobs(None, None, 10))
    assert [j.job_id for j in everyone] == [a.job_id, b.job_id]  # most recently updated first
    assert (
        everyone[0].draft_recommendation == "approve" and everyone[1].draft_recommendation is None
    )
    mine = asyncio.run(store.list_jobs("rm-b", None, 10))
    assert [j.job_id for j in mine] == [b.job_id]
    waiting = asyncio.run(store.list_jobs(None, ("awaiting_review",), 10))
    assert [j.job_id for j in waiting] == [a.job_id]
    assert len(asyncio.run(store.list_jobs(None, None, 1))) == 1
