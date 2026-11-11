"""Hash-chained evidence events: verification, tamper detection, and what a job records."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from itertools import pairwise

import pytest

from axiom.evidence.log import GENESIS, EventDraft, EvidenceEvent, chain, verify_chain
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import process_job
from conftest import SETTINGS, fake_context

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _events(n: int = 4) -> list[EvidenceEvent]:
    drafts = [EventDraft(type="model_call", actor="worker", payload={"i": i}) for i in range(n)]
    return chain("job_x", drafts, None, AT)


def test_a_fresh_chain_verifies() -> None:
    events = _events()
    assert events[0].prev_hash == GENESIS and [e.seq for e in events] == [1, 2, 3, 4]
    assert all(b.prev_hash == a.hash for a, b in pairwise(events))
    assert verify_chain(events).verified


def test_appending_continues_the_chain() -> None:
    first = _events(2)
    more = chain(
        "job_x", [EventDraft(type="job_failed", actor="worker", payload={})], first[-1], AT
    )
    assert more[0].seq == 3 and more[0].prev_hash == first[-1].hash
    assert verify_chain(first + more).verified


@pytest.mark.parametrize(
    ("tamper", "broken_at"),
    [
        (lambda ev: ev.__setitem__(1, ev[1].model_copy(update={"payload": {"i": 99}})), 2),
        (lambda ev: ev.__setitem__(2, ev[2].model_copy(update={"actor": "someone"})), 3),
        (lambda ev: ev.pop(1), 3),  # a removed event leaves a gap at the next one
        (lambda ev: ev.pop(), None),  # removing the tail is caught by count, see below
    ],
)
def test_tampering_breaks_the_chain(tamper, broken_at) -> None:  # type: ignore[no-untyped-def]
    events = _events()
    tamper(events)
    check = verify_chain(events)
    if broken_at is None:
        # A truncated chain still links; the job's terminal event is what goes missing.
        assert check.verified and check.events == 3
    else:
        assert not check.verified and check.first_broken_seq == broken_at


def test_rehashing_an_edited_event_still_breaks_the_next_link() -> None:
    events = _events()
    edited = chain(
        "job_x", [EventDraft(type="model_call", actor="worker", payload={"i": 99})], events[0], AT
    )[0]
    events[1] = edited
    assert verify_chain(events).first_broken_seq == 3


# What a job records ------------------------------------------------------------


def _run(package_id: str, responder):  # type: ignore[no-untyped-def]
    store = InMemoryJobStore()
    sub = asyncio.run(store.submit("rm-a", "k", "h", package_id))
    assert sub.job is not None
    ctx, _ = fake_context(responder)
    job = asyncio.run(store.claim_next())
    assert job is not None
    asyncio.run(process_job(store, ctx, SETTINGS, job))
    return store, job.job_id


def test_successful_job_records_every_step_in_order() -> None:
    store, job_id = _run("pkg-01", lambda r: "")
    events = asyncio.run(store.events(job_id))
    types = [e.type for e in events]
    assert types[:2] == ["job_submitted", "job_started"]
    assert types[-1] == "job_awaiting_review"
    assert types.count("model_call") == 7  # five documents, analyst, drafter
    assert [e.payload["node"] for e in events if e.type == "node_completed"] == [
        "intake",
        "analyst",
        "drafter",
        "compliance",
    ]
    assert events[0].actor == "rm-a" and events[0].payload["package_id"] == "pkg-01"
    assert verify_chain(events).verified


def test_failed_job_records_its_rejected_attempts() -> None:
    def bad_income_statement(request) -> str:  # type: ignore[no-untyped-def]
        key = request.recording_key.removesuffix(".retry")
        if request.prompt_id == "intake" and key.endswith("income_statement"):
            return '{"figures": []}'
        return ""

    store, job_id = _run("pkg-01", bad_income_statement)
    events = asyncio.run(store.events(job_id))
    calls = [e.payload for e in events if e.type == "model_call"]
    rejected = [c for c in calls if not c["valid"]]
    assert [c["attempt"] for c in rejected] == [1, 2]
    assert [e.type for e in events][-2:] == ["node_failed", "job_failed"]
    assert events[-1].payload == {"error_code": "output_validation_failed"}
    job = asyncio.run(store.get(job_id))
    assert job is not None and len(job.result["model_calls"]) == len(calls)  # type: ignore[index]
    assert verify_chain(events).verified
