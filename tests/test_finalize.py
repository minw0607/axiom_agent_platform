"""Finalize: the approved draft's hash, and related AI governance guidance from Regulus,
which is for auditors only and can never block an approval."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from axiom.agents.base import AgentContext
from axiom.api.app import create_app
from axiom.jobs.store import InMemoryJobStore
from axiom.jobs.worker import build_context, run_once
from axiom.mcp_clients.governance import RelatedProvision
from test_api import KEYS
from test_review import SETTINGS_SOD, drafted, review

PROVISION = RelatedProvision(
    citation="Framework X, Section 1 — Human oversight",
    framework="Framework X",
    provision_id="Section 1",
    source_url="https://example.org/framework-x#1",
    text_hash="a" * 64,
    relevance=0.4,
)


class FakeRegulus:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.queries: list[str] = []

    async def lookup(self, description: str, k: int = 5) -> list[RelatedProvision]:
        self.queries.append(description)
        if self.fail:
            raise ConnectionError("regulus down")
        return [PROVISION]


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_SOD, store)) as c:
        yield c


def work_with(store: InMemoryJobStore, ctx: AgentContext) -> None:
    while asyncio.run(run_once(store, ctx, SETTINGS_SOD)):
        pass


def approve(client: TestClient, store: InMemoryJobStore, governance: object) -> tuple[str, dict]:  # type: ignore[type-arg]
    ctx = replace(build_context(SETTINGS_SOD), governance=governance)
    job_id = drafted(client, store)
    assert review(client, job_id, "approve", "Agreed.").status_code == 202
    work_with(store, ctx)
    r = client.get(f"/v1/memos/{job_id}/evidence", headers={"X-API-Key": KEYS["auditor"]})
    assert r.status_code == 200
    return job_id, r.json()


def test_related_guidance_is_attached_for_auditors(
    client: TestClient, store: InMemoryJobStore
) -> None:
    regulus = FakeRegulus()
    job_id, bundle = approve(client, store, regulus)
    assert bundle["status"] == "approved"
    guidance = bundle["related_guidance"]
    assert guidance["status"] == "attached"
    assert guidance["label"].startswith("Related AI governance guidance")
    assert [p["citation"] for p in guidance["provisions"]] == [PROVISION.citation]
    assert "Draft recommendation: approve" in regulus.queries[0]
    final = next(
        e
        for e in bundle["events"]
        if e["type"] == "node_completed" and e["payload"]["node"] == "finalize"
    )
    assert final["payload"]["related_guidance"] == {
        "status": "attached",
        "provisions": [PROVISION.citation],
    }
    # The public memo carries the decision, not the governance lookup.
    public = client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["submitter"]}).json()
    assert "related_guidance" not in str(public) and "Framework X" not in str(public)


def test_a_failed_lookup_never_blocks_approval(client: TestClient, store: InMemoryJobStore) -> None:
    _, bundle = approve(client, store, FakeRegulus(fail=True))
    assert bundle["status"] == "approved"
    assert bundle["related_guidance"]["status"] == "not_evaluated"
    assert "ConnectionError" in bundle["related_guidance"]["reason"]


def test_without_regulus_guidance_is_not_evaluated(
    client: TestClient, store: InMemoryJobStore
) -> None:
    _, bundle = approve(client, store, None)
    assert bundle["related_guidance"]["status"] == "not_evaluated"
    assert bundle["related_guidance"]["reason"] == "Regulus is not configured"


def test_the_approved_draft_is_hashed(client: TestClient, store: InMemoryJobStore) -> None:
    job_id, _ = approve(client, store, None)
    job = asyncio.run(store.get(job_id))
    assert job is not None and job.result is not None
    from axiom.domain import MemoDraft

    draft = MemoDraft.model_validate(job.result["draft"])
    digest = hashlib.sha256(draft.model_dump_json().encode()).hexdigest()
    assert job.result["approved_draft_sha256"] == digest


def test_a_rejected_memo_gets_no_guidance(client: TestClient, store: InMemoryJobStore) -> None:
    regulus = FakeRegulus()
    ctx = replace(build_context(SETTINGS_SOD), governance=regulus)
    job_id = drafted(client, store)
    assert review(client, job_id, "reject", "No.").status_code == 202
    work_with(store, ctx)
    assert regulus.queries == []
