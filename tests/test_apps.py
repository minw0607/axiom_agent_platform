"""The app registry and per-app routes (ADR 0016, step 1b)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.apps.credit_memo import APP
from axiom.core.app import App
from axiom.core.registry import AppConfigError, enabled_apps, load_apps
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, error_codes, run_once
from test_api import KEYS, SETTINGS_WITH_KEYS

ROUTE = "/v1/apps/credit_memo/jobs"


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as c:
        yield c


def h(who: str, key: str | None = None) -> dict[str, str]:
    return {"X-API-Key": KEYS[who]} | ({"Idempotency-Key": key} if key else {})


def work(store: InMemoryJobStore) -> None:
    ctx = {"credit_memo": build_context(SETTINGS_WITH_KEYS)}
    while asyncio.run(run_once(store, ctx, SETTINGS_WITH_KEYS)):
        pass


def test_the_credit_memo_meets_the_app_contract() -> None:
    assert isinstance(APP, App)
    assert enabled_apps() == {"credit_memo": APP}
    assert error_codes(APP)["IntakeError"] == "invalid_package"
    assert error_codes(APP)["GuardrailFailed"] == "guardrail_failed"


def test_an_app_package_without_a_contract_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "apps.yaml"
    config.write_text("version: 1\napps:\n  - credit_memo: axiom.apps.credit_memo.domain\n")
    with pytest.raises(AppConfigError, match="App protocol"):
        load_apps(config)


def test_apps_are_listed_with_their_role_names(client: TestClient) -> None:
    r = client.get("/v1/apps", headers=h("auditor"))
    assert r.status_code == 200
    assert r.json() == [
        {
            "app_id": "credit_memo",
            "title": "Credit memo",
            "roles": {
                "submitter": "Relationship manager",
                "reviewer": "Credit officer",
                "auditor": "Auditor",
            },
        }
    ]
    assert client.get("/v1/apps").status_code == 401


def test_a_job_submitted_on_the_app_route_runs_and_is_the_same_job_on_v1_memos(
    client: TestClient, store: InMemoryJobStore
) -> None:
    r = client.post(ROUTE, json={"package_id": "pkg-01"}, headers=h("submitter", "k-app"))
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert r.headers["Location"] == f"{ROUTE}/{job_id}"
    assert store.jobs[job_id].app_id == "credit_memo"
    work(store)
    on_app = client.get(f"{ROUTE}/{job_id}", headers=h("reviewer")).json()
    on_memos = client.get(f"/v1/memos/{job_id}", headers=h("reviewer")).json()
    assert on_app == on_memos
    assert on_app["status"] == "awaiting_review" and on_app["memo"]
    listed = client.get(ROUTE, headers=h("reviewer")).json()["items"]
    assert [j["job_id"] for j in listed] == [job_id]
    events = client.get(f"{ROUTE}/{job_id}/evidence", headers=h("auditor")).json()["events"]
    assert events[0]["type"] == "job_submitted"
    assert events[0]["payload"]["app_id"] == "credit_memo"


def test_another_apps_job_is_not_found_on_the_credit_memo_routes(
    client: TestClient, store: InMemoryJobStore
) -> None:
    other = asyncio.run(store.submit("rm-alex", "k-x", "h", "pkg-01", None, "other_app"))
    assert other.job is not None
    job_id = other.job.job_id
    for path in (f"{ROUTE}/{job_id}", f"/v1/memos/{job_id}"):
        assert client.get(path, headers=h("reviewer")).status_code == 404
    assert client.get(f"/v1/memos/{job_id}/evidence", headers=h("auditor")).status_code == 404
    review = client.post(
        f"/v1/memos/{job_id}/review",
        json={"decision": "approve"},
        headers=h("reviewer", "k-r"),
    )
    assert review.status_code == 404
    assert client.get("/v1/memos", headers=h("reviewer")).json()["items"] == []


def test_each_app_route_has_the_apps_own_schemas(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path in (ROUTE, "/v1/memos"):
        body = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]
        assert body["schema"]["$ref"].endswith("/MemoRequest")
        job = spec["paths"][f"{path}/{{job_id}}"]["get"]["responses"]["200"]["content"]
        assert job["application/json"]["schema"]["$ref"].endswith("/MemoJob")
    assert (
        "pkg-01" in spec["components"]["schemas"]["MemoRequest"]["properties"]["package_id"]["enum"]
    )
