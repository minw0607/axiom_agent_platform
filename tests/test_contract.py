"""Contract tests: Schemathesis generates requests from the published OpenAPI document and
checks every response against it (dev plan 6, 9.5): status codes the spec declares, bodies
that match their schemas, the declared content types, and never a server error.

Each operation is called with a key whose role may use it; Schemathesis also drops the key
to check the 401. Generated requests include invalid ones (wrong types, missing fields,
unknown query parameters), which must be refused with a documented 4xx. Generated ids mostly
miss, so a second test walks one real memo through draft, review and audit.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings

from axiom.api.app import MAX_IDEMPOTENCY_KEY, create_app
from axiom.jobs.store import InMemoryJobStore, PostgresJobStore
from test_api import KEYS, SETTINGS_WITH_KEYS, work
from test_store_postgres import BASE_URL, _reachable

STORE = InMemoryJobStore()
APP = create_app(SETTINGS_WITH_KEYS, STORE)
schema = schemathesis.openapi.from_asgi("/openapi.json", APP)

# The role each operation is meant for; everything else is open to all three roles.
ROLE = {
    ("POST", "/v1/memos"): "submitter",
    ("POST", "/v1/memos/{job_id}/review"): "reviewer",
    ("GET", "/v1/memos/{job_id}/evidence"): "auditor",
}


@pytest.fixture(scope="module", autouse=True)
def started() -> Iterator[None]:
    with TestClient(APP):  # runs the app's startup, which initializes the store
        yield


def check(case: schemathesis.Case) -> None:
    role = ROLE.get((case.method.upper(), case.path), "auditor")
    generated = case.headers or {}
    # A generated key is replaced with a real one for the operation's role. A case with no
    # key is left without one: it checks a missing key is refused.
    headers = {"X-API-Key": KEYS[role]} if "X-API-Key" in generated else {}
    key = generated.get("Idempotency-Key")
    if key and len(key) <= MAX_IDEMPOTENCY_KEY:
        # Valid generated keys repeat across examples with different bodies, which the API
        # rightly refuses (idempotency-key-reused); a fresh one keeps each example independent.
        # Invalid keys are left alone: those cases check the API refuses them.
        headers["Idempotency-Key"] = uuid.uuid4().hex
    case.call_and_validate(headers=headers)


EXAMPLES = settings(max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))


@schema.parametrize()
@EXAMPLES
def test_the_api_keeps_its_contract(case: schemathesis.Case) -> None:
    check(case)


# The same requests against Postgres, as the stack runs: generated strings (a NUL byte in an
# id, say) must not turn into database errors. A throwaway schema, dropped afterwards.
PG_SCHEMA = f"test_contract_{uuid.uuid4().hex[:10]}"
PG_URL = f"{BASE_URL}{'&' if '?' in BASE_URL else '?'}options=-csearch_path%3D{PG_SCHEMA}"
PG_APP = create_app(SETTINGS_WITH_KEYS, PostgresJobStore(PG_URL))
# Loaded from the document directly: from_asgi would start the app, and so connect, before
# the throwaway schema exists.
pg_schema = schemathesis.openapi.from_dict(PG_APP.openapi())
pg_schema.app = PG_APP


@pytest.fixture(scope="module")
def postgres() -> Iterator[None]:
    if not _reachable():
        pytest.skip("Postgres not reachable")
    import psycopg

    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {PG_SCHEMA}")
    try:
        with TestClient(PG_APP):
            yield
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {PG_SCHEMA} CASCADE")


@pg_schema.parametrize()
@EXAMPLES
def test_the_api_keeps_its_contract_on_postgres(case: schemathesis.Case, postgres: None) -> None:
    check(case)


def find_citation(value: Any) -> dict[str, str] | None:
    """The first {doc_id, locator} pair anywhere in a memo."""
    if isinstance(value, dict):
        if isinstance(value.get("doc_id"), str) and isinstance(value.get("locator"), str):
            return {"doc_id": value["doc_id"], "locator": value["locator"]}
        values: Any = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for item in values:
        found = find_citation(item)
        if found:
            return found
    return None


def test_a_real_memo_matches_the_contract_through_its_life() -> None:
    """Random ids mostly miss, so walk one job through draft, review and audit and check
    every response for it against the published schemas."""

    def call(path: str, method: str, role: str, **kwargs: Any) -> Any:
        headers = {"X-API-Key": KEYS[role]}
        if method == "POST":
            headers["Idempotency-Key"] = uuid.uuid4().hex
        case = schema[path][method].Case(headers=headers, **kwargs)
        return case.call_and_validate()

    with TestClient(APP):
        accepted = call(
            "/v1/memos",
            "POST",
            "submitter",
            body={"package_id": "pkg-01"},
        ).json()
        job = {"job_id": accepted["job_id"]}
        work(STORE)

        memo = call("/v1/memos/{job_id}", "GET", "reviewer", path_parameters=job).json()
        assert memo["status"] == "awaiting_review"
        citation = find_citation(memo["memo"])
        assert citation is not None
        call("/v1/memos/{job_id}/source", "GET", "reviewer", path_parameters=job, query=citation)
        call("/v1/policy/{ref}", "GET", "reviewer", path_parameters={"ref": "CP-4.1"})
        for role in ("submitter", "reviewer", "auditor"):
            call("/v1/dashboard", "GET", role)
            call("/v1/memos", "GET", role)
        call(
            "/v1/memos/{job_id}/review",
            "POST",
            "reviewer",
            path_parameters=job,
            body={"decision": "approve"},
        )
        work(STORE)
        approved = call("/v1/memos/{job_id}", "GET", "submitter", path_parameters=job).json()
        assert approved["status"] == "approved"
        call("/v1/memos/{job_id}/evidence", "GET", "auditor", path_parameters=job)
