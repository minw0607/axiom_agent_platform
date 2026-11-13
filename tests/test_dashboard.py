"""GET /v1/dashboard: each role's summary, scoped like the memo list, with no names."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.jobs.store import InMemoryJobStore
from test_api import KEYS, submit
from test_review import SETTINGS_SOD, review, work


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_SOD, store)) as c:
        yield c


def dashboard(client: TestClient, who: str) -> dict:  # type: ignore[type-arg]
    r = client.get("/v1/dashboard", headers={"X-API-Key": KEYS[who]})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def tiles(board: dict) -> dict[str, int | None]:  # type: ignore[type-arg]
    return {t["key"]: t["value"] for t in board["tiles"]}


def section(board: dict, title: str) -> list[dict]:  # type: ignore[type-arg]
    return next(s["items"] for s in board["sections"] if s["title"] == title)


def test_a_key_is_required(client: TestClient) -> None:
    assert client.get("/v1/dashboard").status_code == 401


def test_the_relationship_manager_sees_only_their_own_requests(
    client: TestClient, store: InMemoryJobStore
) -> None:
    submit(client, "pkg-01", "k-1")
    submit(client, "pkg-04", "k-2")
    submit(client, "pkg-02", "k-3", who="other")
    board = dashboard(client, "submitter")
    assert board["role"] == "submitter" and board["jobs_considered"] == 2
    assert tiles(board)["in_progress"] == 2
    work(store)
    after = tiles(dashboard(client, "submitter"))
    assert after["in_progress"] == 0 and after["awaiting_review"] == 2


def test_a_failed_request_shows_its_reason(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    record = store.jobs[job_id]
    store.jobs[job_id] = record.model_copy(
        update={"status": "failed", "error_code": "guardrail_failed"}
    )
    board = dashboard(client, "submitter")
    assert tiles(board)["failed"] == 1
    [item] = section(board, "Failed requests")
    assert item["job_id"] == job_id and item["code"] == "guardrail_failed"


def test_the_credit_officer_queue_leads_with_risk(
    client: TestClient, store: InMemoryJobStore
) -> None:
    healthy = submit(client, "pkg-01", "k-1").json()["job_id"]
    work(store)
    weak = submit(client, "pkg-04", "k-2").json()["job_id"]  # weak DSCR: two high flags
    work(store)
    board = dashboard(client, "reviewer")
    t = tiles(board)
    assert t["awaiting_review"] == 2 and t["high_risk"] == 1
    assert t["oldest_wait"] is not None and t["oldest_wait"] >= 0
    queue = section(board, "Review queue, riskiest first")
    # The weak-DSCR memo is newer but comes first, because it carries high-severity risk.
    assert [i["job_id"] for i in queue] == [weak, healthy]
    assert "DSCR_BELOW_DECLINE_FLOOR" in queue[0]["reason"] and queue[0]["tone"] == "bad"


def test_a_revised_draft_comes_back_to_the_officer_who_asked(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    work(store)
    assert review(client, job_id, "revise", "Add a liquidity sentence.").status_code == 202
    assert tiles(dashboard(client, "reviewer"))["revising"] == 1
    work(store)
    board = dashboard(client, "reviewer")
    assert tiles(board)["revised_back"] == 1 and tiles(board)["revising"] == 0
    assert (
        "revised after your comments" in section(board, "Review queue, riskiest first")[0]["reason"]
    )


def test_the_auditor_sees_decisions_and_a_broken_chain(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    work(store)
    review(client, job_id, "approve", "Agreed.")
    work(store)
    board = dashboard(client, "auditor")
    assert tiles(board)["decided"] == 1 and tiles(board)["chain_broken"] == 0
    assert section(board, "Recent decisions")[0]["reason"].startswith("Memo approved")
    events = store.evidence[job_id]
    events[1] = events[1].model_copy(update={"actor": "someone"})
    board = dashboard(client, "auditor")
    assert tiles(board)["chain_broken"] == 1
    assert (
        section(board, "Integrity check failures")[0]["reason"]
        == "Evidence chain breaks at event 2"
    )


def test_the_dashboard_names_no_people(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    work(store)
    review(client, job_id, "revise", "More on liquidity.")
    work(store)
    for who in ("submitter", "reviewer", "auditor"):
        text = client.get("/v1/dashboard", headers={"X-API-Key": KEYS[who]}).text
        assert "rm-alex" not in text and "co-casey" not in text


def test_the_auditor_dashboard_reports_triggered_safety_checks(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    work(store)
    record = store.jobs[job_id]
    result = dict(record.result or {})
    result["guardrails"] = [
        *result.get("guardrails", []),
        {"control": "on_task", "stage": "draft", "outcome": "triggered", "reason": "x"},
    ]
    store.jobs[job_id] = record.model_copy(update={"result": result})
    [item] = section(dashboard(client, "auditor"), "Safety checks that found a problem")
    assert item["code"] == "on_task" and item["tone"] == "warn"
    asyncio.run(asyncio.sleep(0))
