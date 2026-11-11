"""API contract: 202 + Location, idempotency, roles, problem+json, and no model names."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, run_once
from conftest import SETTINGS

KEYS = {"submitter": "sub-key-a", "other": "sub-key-b", "reviewer": "rev-key", "auditor": "aud-key"}
SETTINGS_WITH_KEYS = replace(
    SETTINGS,
    mode="demo",
    gkn_mcp_url=None,
    submitter_keys={KEYS["submitter"]: "rm-alex", KEYS["other"]: "rm-blair"},
    reviewer_keys={KEYS["reviewer"]: "co-casey"},
    auditor_keys={KEYS["auditor"]: "audit-dana"},
)
PROBLEM = "application/problem+json"


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as c:
        yield c


def submit(
    client: TestClient, package_id: str = "pkg-01", key: str = "k-1", who: str = "submitter"
):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/memos",
        json={"package_id": package_id},
        headers={"X-API-Key": KEYS[who], "Idempotency-Key": key},
    )


def work(store: InMemoryJobStore) -> None:
    ctx = build_context(SETTINGS_WITH_KEYS)
    while asyncio.run(run_once(store, ctx, SETTINGS_WITH_KEYS)):
        pass


def test_submit_returns_202_with_location(client: TestClient) -> None:
    r = submit(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert r.headers["Location"] == f"/v1/memos/{body['job_id']}" == body["links"]["self"]


def test_polling_returns_a_draft_with_sourced_figures(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client).json()["job_id"]
    queued = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]})
    assert queued.json()["status"] == "queued" and queued.json()["memo"] is None
    work(store)
    r = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_review"
    memo = body["memo"]
    assert memo["draft_recommendation"] == "approve"
    assert memo["disclaimer"].startswith("Draft for credit officer review")
    revenue = memo["figures"]["2025"]["revenue"]
    assert revenue == {
        "value": "13801200",
        "source": {"doc_id": "pkg-01-income_statement", "line": 8},
    }
    assert next(s["heading"] for s in memo["sections"]) == "Borrower and request"
    assert all(c["citations"] for c in memo["sections"][1]["claims"])


def test_responses_never_name_models_agents_or_prompts(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client).json()["job_id"]
    work(store)
    text = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]}).text
    for word in ("model_calls", "alias", "prompt", "intake", "drafter", "analyst", "scripted"):
        assert word not in text


def test_same_key_same_body_replays(client: TestClient, store: InMemoryJobStore) -> None:
    first = submit(client, key="same")
    second = submit(client, key="same")
    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len(store.jobs) == 1


def test_same_key_different_body_is_rejected(client: TestClient) -> None:
    submit(client, key="same")
    r = submit(client, package_id="pkg-02", key="same")
    assert r.status_code == 422
    assert r.headers["content-type"] == PROBLEM
    assert r.json()["type"].endswith("#idempotency-key-reused")


def test_keys_are_scoped_per_submitter(client: TestClient) -> None:
    a = submit(client, key="shared", who="submitter")
    b = submit(client, key="shared", who="other", package_id="pkg-02")
    assert a.json()["job_id"] != b.json()["job_id"]


def test_missing_idempotency_key_is_a_problem(client: TestClient) -> None:
    r = client.post(
        "/v1/memos", json={"package_id": "pkg-01"}, headers={"X-API-Key": KEYS["submitter"]}
    )
    assert r.status_code == 400
    assert r.headers["content-type"] == PROBLEM
    assert r.json()["type"].endswith("#idempotency-key-required")


@pytest.mark.parametrize(
    ("headers", "status", "slug"),
    [
        ({}, 401, "unauthorized"),
        ({"X-API-Key": "wrong"}, 401, "unauthorized"),
        ({"X-API-Key": KEYS["reviewer"]}, 403, "forbidden"),
        ({"X-API-Key": KEYS["auditor"]}, 403, "forbidden"),
    ],
)
def test_only_submitters_can_submit(
    client: TestClient, headers: dict[str, str], status: int, slug: str
) -> None:
    r = client.post(
        "/v1/memos", json={"package_id": "pkg-01"}, headers={**headers, "Idempotency-Key": "x"}
    )
    assert r.status_code == status
    assert r.json()["type"].endswith(f"#{slug}")


def test_unknown_and_malformed_packages(client: TestClient) -> None:
    assert submit(client, package_id="pkg-99").json()["type"].endswith("#unknown-package")
    r = submit(client, package_id="../../etc/passwd", key="k-2")
    assert r.status_code == 422
    assert r.json()["type"].endswith("#invalid-request")


def test_extra_fields_are_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/memos",
        json={"package_id": "pkg-01", "model": "something"},
        headers={"X-API-Key": KEYS["submitter"], "Idempotency-Key": "k"},
    )
    assert r.status_code == 422


def test_submitters_see_only_their_own_jobs(client: TestClient) -> None:
    job_id = submit(client).json()["job_id"]
    assert (
        client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["other"]}).status_code == 404
    )
    assert (
        client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["reviewer"]}).status_code
        == 200
    )


def test_unknown_job_is_a_problem(client: TestClient) -> None:
    r = client.get("/v1/memos/job_nope", headers={"X-API-Key": KEYS["auditor"]})
    assert r.status_code == 404
    assert r.headers["content-type"] == PROBLEM


def test_oversized_body_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/memos",
        content=b"x" * (70 * 1024),
        headers={
            "X-API-Key": KEYS["submitter"],
            "Idempotency-Key": "k",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 413
    assert r.json()["type"].endswith("#payload-too-large")


def test_failed_job_reports_only_a_public_reason(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client).json()["job_id"]
    asyncio.run(
        store.finish(
            job_id, "failed", {"error": "AgentOutputError: drafter ..."}, "output_validation_failed"
        )
    )
    body = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]}).json()
    assert body["status"] == "failed"
    assert body["error"] == {"code": "output_validation_failed"}
    assert "drafter" not in str(body)


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/v1/ready").json() == {"status": "ready"}


def test_openapi_documents_the_contract(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/v1/memos" in spec["paths"]
    assert "202" in spec["paths"]["/v1/memos"]["post"]["responses"]
