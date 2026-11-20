"""App 2, step 2c: model documentation review switched on, through the core's routes.

The worker runs the real workflow with the stub model from the workflow tests, so no tokens
are spent. The routes, roles, evidence chain and review loop are the core's; this checks app
2 meets them and stays apart from the credit memo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.apps.model_review import APP
from axiom.core.app import App
from axiom.core.registry import enabled_apps
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, error_codes, run_once
from test_api import KEYS, SETTINGS_WITH_KEYS
from test_model_review_workflow import FakeLibrary, careful, context

ROUTE = "/v1/apps/model_review/jobs"
# Local mode, so the stub model in `work` drafts every package.
LOCAL = replace(SETTINGS_WITH_KEYS, mode="local")


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(LOCAL, store)) as c:
        yield c


def h(who: str, key: str | None = None) -> dict[str, str]:
    return {"X-API-Key": KEYS[who]} | ({"Idempotency-Key": key} if key else {})


def work(store: InMemoryJobStore) -> None:
    ctx = {"model_review": context(careful(), FakeLibrary())}
    while asyncio.run(run_once(store, ctx, LOCAL)):
        pass


def submit(client: TestClient, package_id: str = "mdl-03", key: str = "k-1") -> str:
    r = client.post(ROUTE, json={"package_id": package_id}, headers=h("submitter", key))
    assert r.status_code == 202, r.text
    assert r.headers["Location"] == f"{ROUTE}/{r.json()['job_id']}"
    return str(r.json()["job_id"])


def test_model_review_meets_the_app_contract_and_is_enabled() -> None:
    assert isinstance(APP, App)
    assert enabled_apps()["model_review"] is APP
    assert error_codes(APP)["GuardrailFailed"] == "guardrail_failed"
    ctx = build_context(SETTINGS_WITH_KEYS, "model_review")
    assert ctx.checklist.version == "MR-2026.1"
    assert APP.started(ctx) == {
        "checklist_version": "MR-2026.1",
        "prompt_versions": {"assessor": "v1", "drafter": "v1"},
    }


def test_the_catalog_and_checklist_routes(client: TestClient) -> None:
    packages = client.get("/v1/apps/model_review/packages", headers=h("submitter")).json()
    assert [p["package_id"] for p in packages] == [f"mdl-0{i}" for i in range(1, 7)]
    # The catalog's scenario is the answer key; it is not served.
    assert set(packages[0]) == {"package_id", "model_name", "model_type"}
    checklist = client.get("/v1/apps/model_review/checklist", headers=h("reviewer")).json()
    assert checklist["version"] == "MR-2026.1" and len(checklist["items"]) == 12
    assert all(i["provisions"] and i["expects"] for i in checklist["items"])
    assert client.get("/v1/apps/model_review/checklist").status_code == 401


def test_demo_mode_offers_only_recorded_packages(store: InMemoryJobStore, tmp_path: Path) -> None:
    # The tests' scripted recordings cover all six packages (scripts/seed_model_review.py).
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as demo:
        offered = demo.get("/v1/apps/model_review/packages", headers=h("submitter")).json()
        assert [p["package_id"] for p in offered] == [f"mdl-0{i}" for i in range(1, 7)]
    # With no recordings, none is offered and a request is refused.
    empty = replace(SETTINGS_WITH_KEYS, recordings_override=tmp_path)
    with TestClient(create_app(empty, store)) as demo:
        assert demo.get("/v1/apps/model_review/packages", headers=h("submitter")).json() == []
        r = demo.post(ROUTE, json={"package_id": "mdl-01"}, headers=h("submitter", "k-d"))
        assert r.status_code == 422 and r.json()["type"].endswith("package-not-recorded")


def test_a_credit_memo_package_id_is_refused(client: TestClient) -> None:
    r = client.post(ROUTE, json={"package_id": "pkg-01"}, headers=h("submitter", "k-p"))
    assert r.status_code == 422


def test_a_report_is_drafted_reviewed_and_approved(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client)
    assert store.jobs[job_id].app_id == "model_review"
    work(store)
    job = client.get(f"{ROUTE}/{job_id}", headers=h("reviewer")).json()
    assert job["status"] == "awaiting_review", job.get("error")
    report = job["report"]
    assert report["documentation_status"] == "remediation_needed"
    assert report["checklist_version"] == "MR-2026.1"
    assert [s["heading"] for s in report["sections"]] == [
        "Summary",
        "Checklist gaps",
        "Section assessments",
        "Matters for the validator",
    ]
    assert "not a validation decision" in report["disclaimer"]
    assert report["completeness"]["total"] == 12

    revise = client.post(
        f"{ROUTE}/{job_id}/review",
        json={"decision": "revise", "comments": "Name the owner of each fix."},
        headers=h("reviewer", "k-r1"),
    )
    assert revise.status_code == 202, revise.text
    work(store)
    approve = client.post(
        f"{ROUTE}/{job_id}/review", json={"decision": "approve"}, headers=h("reviewer", "k-r2")
    )
    assert approve.status_code == 202, approve.text
    work(store)
    job = client.get(f"{ROUTE}/{job_id}", headers=h("submitter")).json()
    assert job["status"] == "approved" and job["revisions"]["human"] == 1
    assert job["report"]["disclaimer"].startswith("Approved by a model validator")

    bundle = client.get(f"{ROUTE}/{job_id}/evidence", headers=h("auditor")).json()
    assert bundle["chain"]["verified"]
    assert bundle["checklist_version"] == "MR-2026.1" and bundle["approved_report_sha256"]
    assert bundle["provisions_status"] == "attached" and bundle["provisions"]
    assert all(c["doc_id"] and c["locator"] for c in bundle["citations"])
    events: list[dict[str, Any]] = bundle["events"]
    assert events[0]["payload"]["app_id"] == "model_review"
    completed = [e["payload"]["node"] for e in events if e["type"] == "node_completed"]
    assert completed[:4] == ["intake", "assessor", "provisions", "drafter"]
    assert completed[-1] == "finalize"


def test_a_citation_resolves_to_the_submitted_document(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client, "mdl-01", "k-s")
    work(store)
    report = client.get(f"{ROUTE}/{job_id}", headers=h("reviewer")).json()["report"]
    # Every assessment cites lines of the section it judged.
    cited = report["assessments"][0]["citations"][0]
    r = client.get(
        f"{ROUTE}/{job_id}/source",
        params={"doc_id": cited["doc_id"], "locator": cited["locator"]},
        headers=h("reviewer"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "line" and r.json()["fingerprint"] == "matches"


def test_the_apps_jobs_stay_apart(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = submit(client, key="k-a")
    for path in (f"/v1/apps/credit_memo/jobs/{job_id}", f"/v1/memos/{job_id}"):
        assert client.get(path, headers=h("reviewer")).status_code == 404
    assert client.get("/v1/memos", headers=h("reviewer")).json()["items"] == []
    assert [j["job_id"] for j in client.get(ROUTE, headers=h("reviewer")).json()["items"]] == [
        job_id
    ]
    # Another model owner cannot see it.
    assert client.get(f"{ROUTE}/{job_id}", headers=h("other")).status_code == 404


def test_the_openapi_names_app_2s_own_schemas(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    body = spec["paths"][ROUTE]["post"]["requestBody"]["content"]["application/json"]
    assert body["schema"]["$ref"].endswith("/ModelReviewRequest")
    request = spec["components"]["schemas"]["ModelReviewRequest"]
    assert request["properties"]["package_id"]["enum"] == [f"mdl-0{i}" for i in range(1, 7)]
    job = spec["paths"][f"{ROUTE}/{{job_id}}"]["get"]["responses"]["200"]["content"]
    assert job["application/json"]["schema"]["$ref"].endswith("/ReviewJob")
