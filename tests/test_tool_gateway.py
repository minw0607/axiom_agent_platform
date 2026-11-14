"""The tool gateway (ADR 0014): per-caller tokens, its rules, its log, and a memo run through it.

The gateway runs over real HTTP in front of two stand-in tool services, each with its own
secret, so these tests exercise the same path as the Docker stack.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from mcp.server.mcpserver import MCPServer
from starlette.types import ASGIApp, Receive, Scope, Send
from tests.conftest import SETTINGS, demo_context

from axiom.catalog import load_package
from axiom.demo_keys import GATEWAY_STEPS
from axiom.domain import ReviewDecision
from axiom.gateway.policy import GatewayPolicy, load_policy
from axiom.gateway.service import CallLog, Gateway, build_app, verify_log
from axiom.graph.workflow import resume_workflow, run_workflow
from axiom.jobs.worker import run_events
from axiom.mcp_clients.client import (
    GatewayClient,
    MCPToolClient,
    MCPToolError,
    acting_as,
    collect_tool_calls,
)
from axiom.mcp_clients.evidence import GKNEvidence, InMemoryEvidence
from axiom.mcp_clients.governance import RegulusGovernance
from axiom.memory.checkpoint import serializer

POLICY_FILE = SETTINGS.config_dir / "tool_gateway.yaml"
TOKENS = {name: f"token-{name}" for name in (*GATEWAY_STEPS, "operator")}
SECRETS = {"gkn": "gkn-secret", "regulus": "regulus-secret"}


# The rules -----------------------------------------------------------------------------------


def test_each_step_may_call_only_its_own_tool_and_collection() -> None:
    policy = load_policy(POLICY_FILE)
    job = {"query": "q", "job_id": "j", "k": 1}
    assert policy.check("drafter", "retrieve_evidence", {**job, "collection": "job"}) == ""
    assert "may not" in policy.check(
        "drafter", "retrieve_evidence", {**job, "collection": "lending_refs"}
    )
    assert "may not" in policy.check("drafter", "retrieve_evidence", job)  # no collection: refused
    assert (
        policy.check("compliance", "retrieve_evidence", {**job, "collection": "lending_refs"}) == ""
    )
    assert "may not" in policy.check(
        "compliance", "retrieve_evidence", {**job, "collection": "job"}
    )
    assert "may not call delete_job" in policy.check("drafter", "delete_job", {"job_id": "j"})
    assert "may not call lookup_provisions" in policy.check("intake", "lookup_provisions", {})
    assert policy.check("finalize", "lookup_provisions", {"description": "d", "k": 5}) == ""
    assert "unknown caller" in policy.check("someone", "delete_job", {"job_id": "j"})


def test_the_rules_cover_every_workflow_step_and_never_offer_unused_tools() -> None:
    policy = load_policy(POLICY_FILE)
    assert set(GATEWAY_STEPS) | {"operator"} == set(policy.callers)
    assert "recommend_mitigants" not in policy.tools
    assert "delete_job" not in {t for step in GATEWAY_STEPS for t in policy.callers[step]}


def test_a_rule_naming_an_unknown_tool_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(POLICY_FILE.read_text())
    raw["callers"]["drafter"]["recommend_mitigants"] = {}
    with pytest.raises(ValueError, match="unknown tools"):
        GatewayPolicy.model_validate(raw)


# Over HTTP -----------------------------------------------------------------------------------


def _serve(app: ASGIApp) -> tuple[str, uvicorn.Server, threading.Thread]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("test server did not start within 30 seconds")
    return f"http://127.0.0.1:{port}", server, thread


def _require(secret: str, inner: ASGIApp, seen: list[str]) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"authorization", b"").decode()
        if scope["type"] == "http":
            seen.append(supplied)
            if supplied != f"Bearer {secret}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
        await inner(scope, receive, send)

    return app


def _gkn() -> ASGIApp:
    memory = InMemoryEvidence(SETTINGS.reference_dirs)
    server = MCPServer(name="gkn-standin")

    @server.tool()
    async def ingest_documents(job_id: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        docs = [SimpleNamespace(**{**d, "title": d.get("title", "")}) for d in documents]
        hashes = await memory.ingest(job_id, docs)  # type: ignore[arg-type]
        return {"ingested": [{"doc_id": k, "text_hash": v} for k, v in hashes.items()]}

    @server.tool()
    async def retrieve_evidence(
        query: str, job_id: str, k: int = 5, collection: str = "job"
    ) -> dict[str, Any]:
        hits = await memory.retrieve(job_id, query, k, collection)  # type: ignore[arg-type]
        return {"hits": [h.model_dump() for h in hits]}

    @server.tool()
    async def delete_job(job_id: str) -> dict[str, Any]:
        await memory.delete(job_id)
        return {"deleted": job_id}

    return server.streamable_http_app(json_response=True, stateless_http=True)


def _regulus() -> ASGIApp:
    server = MCPServer(name="regulus-standin")

    @server.tool()
    def lookup_provisions(description: str, k: int = 5) -> dict[str, Any]:
        provision = {
            "citation": "Framework A, 1.1",
            "framework": "Framework A",
            "provision_id": "1.1",
            "source_url": "https://example.org/a",
            "text_hash": "0" * 64,
        }
        return {"provisions": [provision][:k]}

    return server.streamable_http_app(json_response=True, stateless_http=True)


class Stack(SimpleNamespace):
    url: str
    log_path: Path
    seen: dict[str, list[str]]


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    seen: dict[str, list[str]] = {"gkn": [], "regulus": []}
    servers = []
    backends = {}
    for name, app in (("gkn", _gkn()), ("regulus", _regulus())):
        base, server, thread = _serve(_require(SECRETS[name], app, seen[name]))
        servers.append((server, thread))
        backends[name] = MCPToolClient(f"{base}/mcp", SECRETS[name])
    log_path = tmp_path_factory.mktemp("gateway") / "tool_calls.jsonl"
    gateway = Gateway(load_policy(POLICY_FILE), TOKENS, backends, CallLog(log_path))
    base, server, thread = _serve(build_app(gateway, ["127.0.0.1:*"]))
    servers.append((server, thread))
    yield Stack(url=f"{base}/mcp", log_path=log_path, seen=seen)
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)


def _client(stack: Stack) -> GatewayClient:
    return GatewayClient(stack.url, TOKENS)


def test_the_gateway_rejects_a_missing_or_unknown_token(stack: Stack) -> None:
    base = stack.url.rsplit("/mcp", 1)[0]
    assert httpx.get(f"{base}/healthz").status_code == 200
    assert httpx.post(stack.url, json={}).status_code == 401
    assert (
        httpx.post(stack.url, json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    )
    # A tool service's own secret does not open the gateway.
    bad = {"Authorization": f"Bearer {SECRETS['gkn']}"}
    assert httpx.post(stack.url, json={}, headers=bad).status_code == 401


def test_a_call_with_no_step_is_never_sent(stack: Stack) -> None:
    with pytest.raises(MCPToolError, match="no tool gateway token"):
        asyncio.run(_client(stack).call("delete_job", {"job_id": "j"}))


def test_an_allowed_call_reaches_the_service_with_that_service_secret(stack: Stack) -> None:
    client = _client(stack)

    async def run() -> list[Any]:
        with acting_as("finalize"), collect_tool_calls() as calls:
            provisions = await RegulusGovernance(client).lookup("an AI system", k=1)
        return [provisions, calls]

    provisions, calls = asyncio.run(run())
    assert provisions[0].citation == "Framework A, 1.1"
    assert stack.seen["regulus"] and set(stack.seen["regulus"]) == {f"Bearer {SECRETS['regulus']}"}
    assert TOKENS["finalize"] not in " ".join(stack.seen["gkn"] + stack.seen["regulus"])
    [call] = calls
    assert call.node == "finalize" and call.tool == "lookup_provisions" and call.allowed
    assert call.call_id and call.result_sha256 and not call.error


def test_a_refused_call_is_recorded_with_its_call_id(stack: Stack) -> None:
    client = _client(stack)
    before = len(stack.seen["gkn"])

    async def run() -> list[Any]:
        with acting_as("drafter"), collect_tool_calls() as calls:
            with pytest.raises(MCPToolError, match="refused"):
                await GKNEvidence(client).retrieve("j", "covenants", collection="lending_refs")
            with pytest.raises(MCPToolError, match="refused"):
                await GKNEvidence(client).delete("j")
        return calls

    calls = asyncio.run(run())
    assert len(stack.seen["gkn"]) == before  # nothing reached the service
    assert [(c.tool, c.allowed) for c in calls] == [
        ("retrieve_evidence", False),
        ("delete_job", False),
    ]
    assert all(c.call_id for c in calls)
    logged = {
        json.loads(line)["call_id"]: json.loads(line)
        for line in stack.log_path.read_text().splitlines()
    }
    assert all(logged[c.call_id]["decision"] == "refused" for c in calls)


def test_the_log_is_chained_and_holds_no_raw_arguments(stack: Stack) -> None:
    secret_query = "a phrase that must never be logged"

    async def run() -> None:
        with acting_as("operator"):
            await GKNEvidence(_client(stack)).retrieve("j", secret_query, collection="job")

    asyncio.run(run())
    text = stack.log_path.read_text()
    assert secret_query not in text and "token-" not in text
    assert verify_log(stack.log_path) == (True, len(text.splitlines()))
    lines = text.splitlines()
    tampered = json.loads(lines[0]) | {"caller": "someone-else"}
    broken = stack.log_path.with_name("tampered.jsonl")
    broken.write_text("\n".join([json.dumps(tampered), *lines[1:]]) + "\n")
    assert verify_log(broken)[0] is False


def test_a_memo_runs_through_the_gateway_with_each_step_as_itself(stack: Stack) -> None:
    client = _client(stack)
    ctx = demo_context()
    ctx.evidence = GKNEvidence(client)
    ctx.governance = RegulusGovernance(client)
    saver = InMemorySaver(serde=serializer())
    package = load_package(SETTINGS.packages_dir, "pkg-01")

    async def run() -> Any:
        await run_workflow(ctx, "gw-1", package, saver)
        approve = ReviewDecision(decision="approve", reviewer="x", round=1)
        return await resume_workflow(ctx, "gw-1", approve, saver)

    state = asyncio.run(run())
    assert state["outcome"] == "approved" and state["related_guidance"].status == "attached"
    used = {(c.node, c.tool) for c in state["tool_calls"]}
    assert used == {
        ("intake", "ingest_documents"),
        ("drafter", "retrieve_evidence"),
        ("compliance", "retrieve_evidence"),
        ("finalize", "lookup_provisions"),
    }
    assert all(c.allowed and c.call_id for c in state["tool_calls"])
    # Each call becomes a tool_call event in the evidence chain, under its own step.
    result = {
        "completed": state["completed"],
        "tool_calls": [c.model_dump() for c in state["tool_calls"]],
        "model_calls": [],
        "guardrails": [],
    }
    events = run_events(result | {"draft": {"draft_recommendation": "x"}, "findings": []})
    tool_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_events) == len(state["tool_calls"])


def test_the_worker_uses_the_gateway_when_it_is_configured() -> None:
    from dataclasses import replace

    from axiom.jobs.worker import build_context

    settings = replace(SETTINGS, tool_gateway_url="http://gw/mcp", tool_gateway_tokens=TOKENS)
    ctx = build_context(settings)
    assert isinstance(ctx.evidence, GKNEvidence) and isinstance(ctx.evidence.client, GatewayClient)
    assert isinstance(ctx.governance, RegulusGovernance)


def test_tool_call_events_follow_the_input_checks_and_skip_earlier_segments() -> None:
    def call(node: str, n: int) -> dict[str, Any]:
        return {"node": node, "tool": "retrieve_evidence", "allowed": True, "call_id": str(n)}

    result = {
        "completed": ["drafter", "compliance"],
        "tool_calls": [call("drafter", 1), call("drafter", 2), call("compliance", 3)],
        "model_calls": [],
        "guardrails": [{"node": "drafter", "stage": "input", "control": "c"}],
        "draft": {"draft_recommendation": "approve"},
        "findings": [],
    }
    events = run_events(result)
    kinds = [(e.type, e.payload.get("call_id")) for e in events]
    assert kinds[:3] == [("guardrail_checked", None), ("tool_call", "1"), ("tool_call", "2")]
    later = run_events(result, completed_before=1, guardrails_before=1, tools_before=2)
    assert [e.payload["call_id"] for e in later if e.type == "tool_call"] == ["3"]
