"""tool-gateway: the only way the platform reaches its MCP tool services (ADR 0014).

Each caller (a workflow step, or the operator scripts) has its own bearer token. The gateway
works out the caller from the token, checks the call against config/tool_gateway.yaml,
forwards an allowed call to the service with that service's own secret, and appends every
call, allowed or refused, to a hash-chained log. The services' secrets live only here.

    python -m axiom.gateway.service

Settings: TOOL_GATEWAY_TOKENS ("caller:token,..."), TOOL_GATEWAY_OPERATOR_TOKEN,
GKN_MCP_URL and GKN_MCP_SECRET, REGULUS_MCP_URL and REGULUS_MCP_SECRET,
TOOL_GATEWAY_LOG (default /var/lib/axiom-gateway/tool_calls.jsonl), TOOL_GATEWAY_POLICY.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from axiom.gateway.policy import GatewayPolicy, load_policy
from axiom.mcp_clients.client import MCPToolClient, fingerprint
from axiom.settings import REPO_ROOT

log = logging.getLogger("axiom.gateway")
DEFAULT_PORT = 8003
DEFAULT_ALLOWED_HOSTS = "localhost:*,127.0.0.1:*,tool-gateway:*"
GENESIS = "0" * 64
REFUSED = "refused by the tool gateway"  # the client looks for this phrase


def parse_tokens(value: str) -> dict[str, str]:
    """'caller:token,...' into {caller: token}."""
    out: dict[str, str] = {}
    for item in value.split(","):
        caller, sep, token = item.strip().partition(":")
        if sep and caller and token:
            out[caller] = token
    return out


class CallLog:
    """Append-only JSON lines, each holding the hash of the line before it."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._last = GENESIS
        if path is not None and path.is_file():
            lines = path.read_text(encoding="utf-8").splitlines()
            if lines:
                self._last = json.loads(lines[-1])["entry_sha256"]

    async def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            entry = {**entry, "prev_sha256": self._last}
            entry["entry_sha256"] = fingerprint(entry)
            self._last = entry["entry_sha256"]
            line = json.dumps(entry, sort_keys=True)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            log.info("%s", line)
            return entry


def verify_log(path: Path) -> tuple[bool, int]:
    """Whether every line's hash and link are intact, and how many lines there are."""
    prev = GENESIS
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        entry = json.loads(line)
        stated = entry.pop("entry_sha256")
        if entry["prev_sha256"] != prev or fingerprint(entry) != stated:
            return False, len(lines)
        prev = stated
    return True, len(lines)


class Gateway:
    def __init__(
        self,
        policy: GatewayPolicy,
        tokens: Mapping[str, str],
        backends: Mapping[str, MCPToolClient],
        call_log: CallLog,
    ) -> None:
        missing = sorted(set(policy.callers) - set(tokens))
        if missing:
            raise ValueError(f"no token for callers: {', '.join(missing)}")
        self.policy = policy
        self.tokens = dict(tokens)
        self.backends = dict(backends)
        self.log = call_log

    def caller_for(self, authorization: str) -> str | None:
        found = None
        for caller, token in self.tokens.items():
            if hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
                found = caller
        return found

    async def call(self, caller: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        call_id = uuid.uuid4().hex[:16]
        started = time.monotonic()
        entry: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "call_id": call_id,
            "caller": caller,
            "tool": tool,
            "backend": self.policy.tools.get(tool, ""),
            "arguments_sha256": fingerprint(arguments),
        }
        reason = self.policy.check(caller, tool, arguments)
        if reason:
            await self.log.append({**entry, "decision": "refused", "reason": reason})
            raise ToolError(f"{REFUSED}: {reason} [call {call_id}]")
        try:
            result = await self.backends[self.policy.tools[tool]].call(tool, arguments)
        except Exception as exc:  # any failure is logged and reported to the caller
            await self.log.append(
                {
                    **entry,
                    "decision": "failed",
                    "reason": str(exc)[:300],
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            )
            raise ToolError(f"{exc} [call {call_id}]") from exc
        await self.log.append(
            {
                **entry,
                "decision": "allowed",
                "result_sha256": fingerprint(result),
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )
        return {**result, "gateway_call_id": call_id}


def _caller(gateway: Gateway, ctx: Context) -> str:
    request = ctx.request_context.request
    authorization = request.headers.get("authorization", "") if request is not None else ""
    caller = gateway.caller_for(authorization)
    if caller is None:  # the middleware already refused unknown tokens
        raise ToolError("unknown caller")
    return caller


def build_server(gateway: Gateway) -> MCPServer:
    server = MCPServer(
        name="axiom-tool-gateway",
        instructions="The Axiom platform's only route to its MCP tool services.",
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool()
    async def ingest_documents(
        job_id: str, documents: list[dict[str, Any]], ctx: Context
    ) -> dict[str, Any]:
        """Index a job's documents in GKN (job-scoped)."""
        args = {"job_id": job_id, "documents": documents}
        return await gateway.call(_caller(gateway, ctx), "ingest_documents", args)

    @server.tool(annotations=read_only)
    async def retrieve_evidence(
        query: str, job_id: str, ctx: Context, k: int = 5, collection: str | None = None
    ) -> dict[str, Any]:
        """Retrieve passages from one job's documents or a read-only reference collection."""
        args: dict[str, Any] = {"query": query, "job_id": job_id, "k": k}
        if collection is not None:
            args["collection"] = collection
        return await gateway.call(_caller(gateway, ctx), "retrieve_evidence", args)

    @server.tool()
    async def delete_job(job_id: str, ctx: Context) -> dict[str, Any]:
        """Delete a job's indexed documents from GKN."""
        return await gateway.call(_caller(gateway, ctx), "delete_job", {"job_id": job_id})

    @server.tool(annotations=read_only)
    async def lookup_provisions(description: str, ctx: Context, k: int = 5) -> dict[str, Any]:
        """Cited AI governance provisions from Regulus."""
        args = {"description": description, "k": k}
        return await gateway.call(_caller(gateway, ctx), "lookup_provisions", args)

    return server


class TokenAuth:
    """ASGI middleware: a known caller token on every route except the health check."""

    def __init__(self, app: ASGIApp, gateway: Gateway) -> None:
        self.app = app
        self.gateway = gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/healthz":
            await _send_json(send, 200, b'{"status":"ok"}')
            return
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"authorization", b"").decode("latin-1")
        if self.gateway.caller_for(supplied) is None:
            await _send_json(send, 401, b'{"error":"unauthorized"}')
            return
        await self.app(scope, receive, send)


async def _send_json(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_app(gateway: Gateway, allowed_hosts: list[str] | None = None) -> ASGIApp:
    app = build_server(gateway).streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts or DEFAULT_ALLOWED_HOSTS.split(","),
        ),
    )
    return TokenAuth(app, gateway)


def gateway_from_env() -> Gateway:
    env = os.environ
    tokens = parse_tokens(env.get("TOOL_GATEWAY_TOKENS", ""))
    if env.get("TOOL_GATEWAY_OPERATOR_TOKEN"):
        tokens["operator"] = env["TOOL_GATEWAY_OPERATOR_TOKEN"]
    backends = {}
    for name in ("gkn", "regulus"):
        url, secret = env.get(f"{name.upper()}_MCP_URL"), env.get(f"{name.upper()}_MCP_SECRET")
        if not url or not secret:
            raise ValueError(f"{name.upper()}_MCP_URL and {name.upper()}_MCP_SECRET must be set")
        backends[name] = MCPToolClient(url, secret)
    policy_path = Path(env.get("TOOL_GATEWAY_POLICY", REPO_ROOT / "config" / "tool_gateway.yaml"))
    log_path = Path(env.get("TOOL_GATEWAY_LOG", "/var/lib/axiom-gateway/tool_calls.jsonl"))
    return Gateway(load_policy(policy_path), tokens, backends, CallLog(log_path))


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    hosts = os.environ.get("MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
    uvicorn.run(
        build_app(gateway_from_env(), hosts),
        host=os.environ.get("TOOL_GATEWAY_HOST", "0.0.0.0"),
        port=int(os.environ.get("TOOL_GATEWAY_PORT", str(DEFAULT_PORT))),
    )


if __name__ == "__main__":
    main()
