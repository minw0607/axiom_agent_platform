"""'Explain this': ratio inputs, cited source excerpts and policy clauses, with no model."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import create_app
from axiom.api.explain import policy_clause, source_excerpt
from axiom.catalog import load_package
from axiom.jobs.store import InMemoryJobStore
from conftest import SETTINGS
from test_api import KEYS, SETTINGS_WITH_KEYS, submit, work


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as c:
        yield c


def drafted(client: TestClient, store: InMemoryJobStore, package_id: str = "pkg-04") -> str:
    job_id: str = submit(client, package_id).json()["job_id"]
    work(store)
    return job_id


def source(client: TestClient, job_id: str, doc_id: str, locator: str, who: str = "reviewer"):  # type: ignore[no-untyped-def]
    return client.get(
        f"/v1/memos/{job_id}/source",
        params={"doc_id": doc_id, "locator": locator},
        headers={"X-API-Key": KEYS[who]},
    )


def memo(client: TestClient, job_id: str) -> dict:  # type: ignore[type-arg]
    return client.get(f"/v1/memos/{job_id}", headers={"X-API-Key": KEYS["reviewer"]}).json()["memo"]  # type: ignore[no-any-return]


def test_a_ratio_carries_the_inputs_that_recompute_it(
    client: TestClient, store: InMemoryJobStore
) -> None:
    ratios = memo(client, drafted(client, store))["ratios"]
    dscr = next(r for r in ratios if r["name"] == "pro_forma_dscr" and r["year"] == 2025)
    assert dscr["value"] == "0.93"
    inputs = {k: Decimal(v) for k, v in dscr["inputs"].items()}
    assert inputs["ebitda"] == Decimal("1025256")
    # Every input is a number the reader can trace; recomputing gives the stated value.
    denominator = sum(v for k, v in inputs.items() if k != "ebitda")
    assert round(inputs["ebitda"] / denominator, 2) == Decimal("0.93")


def test_a_cited_line_resolves_to_the_document_text(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    r = source(client, job_id, "pkg-04-income_statement", "line 11")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "| EBITDA | 1,046,500 | 1,056,965 | 1,025,256 |"
    assert body["header"] == "| Line item | FY2023 | FY2024 | FY2025 |"
    assert body["kind"] == "line" and body["line"] == 11
    assert body["fingerprint"] == "matches"


def test_a_line_outside_a_table_has_no_header(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = drafted(client, store)
    body = source(client, job_id, "pkg-04-loan_application", "line 5").json()
    assert body["text"] == "Requested amount: $1,400,000" and body["header"] is None


def test_a_retrieved_passage_resolves_from_the_job(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    hit = store.jobs[job_id].result["evidence"][0]  # type: ignore[index]
    body = source(client, job_id, hit["doc_id"], hit["chunk_id"]).json()
    assert body["kind"] == "passage" and body["text"] == hit["text"]
    assert body["fingerprint"] == "matches"


@pytest.mark.parametrize(
    ("doc_id", "locator"),
    [
        ("pkg-04-income_statement", "line 99"),
        ("pkg-04-income_statement", "line 0"),
        ("pkg-01-income_statement", "line 8"),  # another package's document
        ("pkg-04-income_statement", "../../etc/passwd"),
        ("pkg-04-income_statement", "pkg-01-business_description_chunk_0"),
    ],
)
def test_a_citation_that_does_not_resolve_is_not_found(
    client: TestClient, store: InMemoryJobStore, doc_id: str, locator: str
) -> None:
    job_id = drafted(client, store)
    r = source(client, job_id, doc_id, locator)
    assert r.status_code == 404 and r.json()["type"].endswith("source-not-found")


def test_a_submitter_cannot_read_another_submitters_sources(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = drafted(client, store)
    assert (
        source(client, job_id, "pkg-04-income_statement", "line 11", "submitter").status_code == 200
    )
    assert source(client, job_id, "pkg-04-income_statement", "line 11", "other").status_code == 404
    assert (
        source(client, job_id, "pkg-04-income_statement", "line 11", "auditor").status_code == 200
    )


def test_a_changed_document_no_longer_matches_its_fingerprint() -> None:
    package = load_package(SETTINGS.packages_dir, "pkg-04")
    result = {"source_hashes": {"pkg-04-income_statement": "0" * 64}}
    excerpt = source_excerpt(package, result, "pkg-04-income_statement", "line 11")
    assert excerpt is not None and excerpt.fingerprint == "differs"
    assert "changed" in excerpt.note
    masked = source_excerpt(
        package, result | {"pii_redacted": True}, "pkg-04-income_statement", "line 11"
    )
    assert masked is not None and "masked" in masked.note


def test_a_policy_clause_is_quoted_with_its_version(client: TestClient) -> None:
    r = client.get("/v1/policy/CP-6.3", headers={"X-API-Key": KEYS["reviewer"]})
    assert r.status_code == 200
    body = r.json()
    assert body["policy_version"] == "CP-2026.2" and body["section"] == "Exceptions"
    assert body["text"].startswith("A request whose pro forma DSCR is below 1.00x")
    section = policy_clause(SETTINGS.reference_dirs["credit_policy"], "CP-3")
    assert section is not None and section.clauses == ["CP-3.1", "CP-3.2", "CP-3.3", "CP-3.4"]
    bold = policy_clause(SETTINGS.reference_dirs["credit_policy"], "CP-4.1")
    assert bold is not None and "**" not in bold.text


@pytest.mark.parametrize("ref", ["CP-9.9", "12 CFR 1002.4(a)", "cp-4.1", "CP-4.1x"])
def test_an_unknown_policy_reference_is_not_found(client: TestClient, ref: str) -> None:
    r = client.get(f"/v1/policy/{ref}", headers={"X-API-Key": KEYS["auditor"]})
    assert r.status_code == 404


def test_the_lookups_need_a_key(client: TestClient, store: InMemoryJobStore) -> None:
    job_id = drafted(client, store)
    assert client.get("/v1/policy/CP-4.1").status_code == 401
    assert (
        client.get(
            f"/v1/memos/{job_id}/source", params={"doc_id": "x", "locator": "line 1"}
        ).status_code
        == 401
    )
