"""Human review: the interrupt, POST /v1/memos/{id}/review, separation of duties, revision
limits, idempotency, and the evidence each decision leaves."""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.domain import ReviewDecision
from axiom.evidence.log import verify_chain
from axiom.graph.workflow import ReviewError, resume_workflow, run_workflow
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, run_once
from axiom.memory.checkpoint import allowed_types
from conftest import SETTINGS, demo_context
from test_api import KEYS, SETTINGS_WITH_KEYS, submit

# A reviewer key that belongs to the same person as the submitter key "rm-alex".
KEYS_SOD = {**KEYS, "self_reviewer": "rev-key-alex"}
SETTINGS_SOD = replace(
    SETTINGS_WITH_KEYS,
    reviewer_keys={KEYS["reviewer"]: "co-casey", KEYS_SOD["self_reviewer"]: "rm-alex"},
)
COMMENTS = (
    "Add a sentence on liquidity that cites the current ratio.",
    "State the main condition you would propose, or say that none is needed.",
)


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_SOD, store)) as c:
        yield c


def work(store: InMemoryJobStore) -> None:
    ctx = build_context(SETTINGS_SOD)
    while asyncio.run(run_once(store, ctx, SETTINGS_SOD)):
        pass


def review(  # type: ignore[no-untyped-def]
    client: TestClient,
    job_id: str,
    decision: str,
    comments: str = "",
    key: str = "r-1",
    who: str = "reviewer",
):
    return client.post(
        f"/v1/memos/{job_id}/review",
        json={"decision": decision, "comments": comments},
        headers={"X-API-Key": KEYS_SOD[who], "Idempotency-Key": key},
    )


def drafted(client: TestClient, store: InMemoryJobStore, package_id: str = "pkg-01") -> str:
    job_id: str = submit(client, package_id).json()["job_id"]
    work(store)
    assert get(client, job_id)["status"] == "awaiting_review"
    return job_id


def get(client: TestClient, job_id: str) -> dict:  # type: ignore[type-arg]
    r = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]})
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def event_types(store: InMemoryJobStore, job_id: str) -> list[str]:
    return [e.type for e in asyncio.run(store.events(job_id))]


# The three decisions ---------------------------------------------------------------------


def test_approval_finalizes_the_memo(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = drafted(client, store)
    r = review(client, job_id, "approve", "Agreed.")
    assert r.status_code == 202
    assert r.json()["status"] == "finalizing" and r.json()["round"] == 1
    assert r.headers["Location"] == f"/v1/memos/{job_id}"
    work(store)
    body = get(client, job_id)
    assert body["status"] == "approved"
    assert body["memo"]["disclaimer"].startswith("Approved by a credit officer")
    assert body["reviews"] == [{"round": 1, "decision": "approve", "comments": "Agreed."}]
    assert body["revisions"] == {"human": 0, "automated": 0}
    events = asyncio.run(store.events(job_id))
    types = [e.type for e in events]
    assert types[-4:] == ["review_submitted", "job_resumed", "node_completed", "job_approved"]
    submitted = next(e for e in events if e.type == "review_submitted")
    assert submitted.actor == "co-casey"
    assert submitted.payload == {"round": 1, "decision": "approve", "comments": "Agreed."}
    assert events[-1].payload["reviewer"] == "co-casey"
    assert events[-2].payload["node"] == "finalize"
    assert verify_chain(events).verified


def test_rejection_closes_the_job_without_finalizing(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    assert review(client, job_id, "reject", "Outside appetite.").status_code == 202
    work(store)
    body = get(client, job_id)
    assert body["status"] == "rejected"
    assert body["memo"]["disclaimer"].startswith("Rejected by a credit officer")
    job = asyncio.run(store.get(job_id))
    assert job is not None and job.result is not None
    assert "finalize" not in job.result["completed"]
    assert event_types(store, job_id)[-1] == "job_rejected"


def test_revision_redrafts_then_pauses_again(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = drafted(client, store)
    r = review(client, job_id, "revise", COMMENTS[0])
    assert r.status_code == 202 and r.json()["status"] == "revising"
    work(store)
    body = get(client, job_id)
    assert body["status"] == "awaiting_review"
    assert body["revisions"] == {"human": 1, "automated": 0}
    claims = [c["text"] for c in body["memo"]["sections"][-1]["claims"]]
    assert any(COMMENTS[0] in c for c in claims)
    job = asyncio.run(store.get(job_id))
    assert job is not None and job.result is not None
    nodes = [c["node"] for c in job.result["model_calls"]]
    assert nodes.count("drafter") == 2 and nodes.count("compliance") == 2
    # The redraft and its compliance review are logged in the new segment.
    types = event_types(store, job_id)
    tail = types[types.index("job_resumed") :]
    assert tail.count("model_call") == 2 and tail[-1] == "job_awaiting_review"


def test_two_revisions_then_only_approve_or_reject(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    for n, comment in enumerate(COMMENTS, 1):
        assert review(client, job_id, "revise", comment, key=f"r-{n}").status_code == 202
        work(store)
    assert get(client, job_id)["revisions"]["human"] == 2
    third = review(client, job_id, "revise", "One more change.", key="r-3")
    assert third.status_code == 409 and third.json()["type"].endswith("revision-limit")
    assert get(client, job_id)["status"] == "awaiting_review"
    assert review(client, job_id, "approve", key="r-4").status_code == 202
    work(store)
    body = get(client, job_id)
    assert body["status"] == "approved"
    assert [v["decision"] for v in body["reviews"]] == ["revise", "revise", "approve"]


# Who may decide ---------------------------------------------------------------------------


def test_the_submitter_cannot_review_their_own_job(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    before = event_types(store, job_id)
    r = review(client, job_id, "approve", who="self_reviewer")
    assert r.status_code == 403
    assert r.json()["type"].endswith("separation-of-duties")
    assert get(client, job_id)["status"] == "awaiting_review"
    assert event_types(store, job_id) == before


@pytest.mark.parametrize("who", ["submitter", "auditor"])
def test_only_the_reviewer_role_can_review(
    client: TestClient, store: InMemoryJobStore, who: str
) -> None:
    job_id = drafted(client, store)
    assert review(client, job_id, "approve", who=who).status_code == 403


# Request rules -----------------------------------------------------------------------------


def test_a_job_not_awaiting_review_is_refused(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = submit(client).json()["job_id"]  # still queued
    r = review(client, job_id, "approve")
    assert r.status_code == 409 and r.json()["type"].endswith("not-awaiting-review")


def test_a_repeated_review_is_replayed_not_applied_twice(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    first = review(client, job_id, "approve", key="same")
    again = review(client, job_id, "approve", key="same")
    assert again.status_code == 202 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["round"] == first.json()["round"]
    assert event_types(store, job_id).count("review_submitted") == 1
    clash = review(client, job_id, "reject", key="same")
    assert clash.status_code == 422
    late = review(client, job_id, "reject", key="other")
    assert late.status_code == 409


@pytest.mark.parametrize(
    ("decision", "comments", "headers", "status"),
    [
        ("revise", "  ", {}, 422),
        ("approve", "", {"Idempotency-Key": ""}, 400),
        ("maybe", "", {}, 422),
    ],
)
def test_malformed_reviews_are_rejected(
    client: TestClient,
    store: InMemoryJobStore,
    decision: str,
    comments: str,
    headers: dict[str, str],
    status: int,
) -> None:
    job_id = drafted(client, store)
    r = client.post(
        f"/v1/memos/{job_id}/review",
        json={"decision": decision, "comments": comments},
        headers={"X-API-Key": KEYS["reviewer"], "Idempotency-Key": "k", **headers},
    )
    assert r.status_code == status
    assert get(client, job_id)["status"] == "awaiting_review"


def test_an_unknown_job_is_not_found(client: TestClient) -> None:
    assert review(client, "job_missing", "approve").status_code == 404


# The graph itself --------------------------------------------------------------------------


def _paused(job_id: str):  # type: ignore[no-untyped-def]
    from langgraph.checkpoint.memory import InMemorySaver

    from axiom.catalog import load_package
    from axiom.memory.checkpoint import serializer

    ctx = demo_context()
    saver = InMemorySaver(serde=serializer())
    package = load_package(SETTINGS.packages_dir, "pkg-01")
    state = asyncio.run(run_workflow(ctx, job_id, package, saver))
    return ctx, saver, state


def test_the_graph_pauses_before_any_decision() -> None:
    _, _, state = _paused("g-1")
    assert "__interrupt__" in state
    assert "outcome" not in state and "finalize" not in state["completed"]


def test_a_third_revision_is_refused_by_the_graph_too() -> None:
    ctx, saver, _ = _paused("g-2")
    for n in (1, 2):
        decision = ReviewDecision(
            decision="revise", comments=COMMENTS[n - 1], reviewer="x", round=n
        )
        asyncio.run(resume_workflow(ctx, "g-2", decision, saver))
    third = ReviewDecision(decision="revise", comments="more", reviewer="x", round=3)
    with pytest.raises(ReviewError, match="at most 2"):
        asyncio.run(resume_workflow(ctx, "g-2", third, saver))


def test_resuming_a_job_that_is_not_paused_is_refused() -> None:
    ctx, saver, _ = _paused("g-3")
    approve = ReviewDecision(decision="approve", reviewer="x", round=1)
    asyncio.run(resume_workflow(ctx, "g-3", approve, saver))
    with pytest.raises(ReviewError):
        asyncio.run(resume_workflow(ctx, "g-3", approve, saver))


def test_checkpoints_load_only_allowlisted_types() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an unregistered type would warn
        ctx, saver, _ = _paused("g-4")
        approve = ReviewDecision(decision="approve", reviewer="x", round=1)
        state = asyncio.run(resume_workflow(ctx, "g-4", approve, saver))
    assert state["outcome"] == "approved"
    assert ("axiom.domain", "ReviewDecision") in allowed_types()
