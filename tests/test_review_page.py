"""The review page and the endpoints it relies on: /v1/me, /v1/packages and GET /v1/memos."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom.api.app import REVIEW_PAGE, create_app
from axiom.jobs.store import InMemoryJobStore
from test_api import KEYS, SETTINGS_WITH_KEYS, submit, work


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS_WITH_KEYS, store)) as c:
        yield c


def headers(who: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[who]}


# The page -----------------------------------------------------------------------------------


def test_the_page_and_its_assets_are_served_with_a_strict_policy(client: TestClient) -> None:
    page = client.get("/review")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp and "unsafe" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    for asset, kind in (("review.js", "javascript"), ("review.css", "text/css")):
        r = client.get(f"/review/assets/{asset}")
        assert r.status_code == 200 and kind in r.headers["content-type"]
        assert "content-security-policy" in r.headers


def test_the_root_redirects_to_the_page(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/review"


def test_the_page_needs_no_key_but_the_data_does(client: TestClient) -> None:
    assert client.get("/review").status_code == 200
    assert client.get("/v1/memos").status_code == 401


def test_the_page_loads_nothing_from_elsewhere_and_has_no_inline_script() -> None:
    html = (REVIEW_PAGE / "index.html").read_text()
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/review/assets/review.js" defer>']
    assert not re.search(r"\son[a-z]+\s*=", html)  # no inline event handlers


def test_the_page_renders_api_text_as_text_never_as_html() -> None:
    js = (REVIEW_PAGE / "review.js").read_text()
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert sink not in js, sink
    # The key lives only in this tab's session storage, never in long-lived storage.
    assert "localStorage" not in js and "sessionStorage" in js


def test_page_files_ship_in_the_package() -> None:
    assert {p.name for p in Path(REVIEW_PAGE).iterdir()} >= {
        "index.html",
        "review.js",
        "review.css",
    }


# Endpoints ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "name", "role"),
    [
        ("submitter", "rm-alex", "submitter"),
        ("reviewer", "co-casey", "reviewer"),
        ("auditor", "audit-dana", "auditor"),
    ],
)
def test_me_names_the_key_holder_and_role(
    client: TestClient, who: str, name: str, role: str
) -> None:
    assert client.get("/v1/me", headers=headers(who)).json() == {"name": name, "role": role}


def test_me_rejects_an_unknown_key(client: TestClient) -> None:
    assert client.get("/v1/me", headers={"X-API-Key": "nope"}).status_code == 401


def test_packages_list_the_catalog_without_the_scenario(client: TestClient) -> None:
    r = client.get("/v1/packages", headers=headers("submitter"))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 25 and items[0]["package_id"] == "pkg-01"  # all have test recordings
    # The scenario label (healthy, stressed, ...) would prime the reviewer; it stays out.
    assert all(set(p) == {"package_id", "borrower_name", "industry"} for p in items)


def test_submitters_list_only_their_own_jobs(client: TestClient) -> None:
    mine = submit(client, "pkg-01", key="a").json()["job_id"]
    theirs = submit(client, "pkg-02", key="b", who="other").json()["job_id"]
    own = client.get("/v1/memos", headers=headers("submitter")).json()["items"]
    assert [j["job_id"] for j in own] == [mine]
    for who in ("reviewer", "auditor"):
        every = client.get("/v1/memos", headers=headers(who)).json()["items"]
        assert {j["job_id"] for j in every} == {mine, theirs}


def test_the_list_filters_by_status_and_shows_the_draft_recommendation(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job_id = submit(client, "pkg-01").json()["job_id"]
    queued = client.get("/v1/memos?status=queued", headers=headers("reviewer")).json()["items"]
    assert [j["job_id"] for j in queued] == [job_id] and queued[0]["draft_recommendation"] is None
    work(store)
    r = client.get("/v1/memos?status=awaiting_review&status=approved", headers=headers("reviewer"))
    (row,) = r.json()["items"]
    assert row["status"] == "awaiting_review" and row["draft_recommendation"] == "approve"
    assert "submitter" not in row  # who submitted is in the evidence bundle only
    assert client.get("/v1/memos?status=queued", headers=headers("reviewer")).json()["items"] == []


@pytest.mark.parametrize("query", ["status=bogus", "limit=0", "limit=201"])
def test_bad_list_queries_are_rejected(client: TestClient, query: str) -> None:
    assert client.get(f"/v1/memos?{query}", headers=headers("reviewer")).status_code == 422
