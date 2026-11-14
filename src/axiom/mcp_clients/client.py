"""MCP tool clients: direct to one service, or through the tool gateway as a workflow step.

Through the gateway (ADR 0014), every call carries the token of the workflow step making it,
so the gateway can allow only that step's tools. The step comes from `acting_as`, which the
workflow sets around each node; a model never chooses it. Every gateway call, allowed or
refused, is also recorded here for the evidence chain.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel


class MCPToolError(RuntimeError):
    """A tool call reached the server but the tool reported an error."""


class ToolCall(BaseModel):
    """One tool call through the gateway, for the evidence bundle. Arguments and results
    are kept as fingerprints only."""

    node: str
    tool: str
    allowed: bool
    call_id: str
    arguments_sha256: str
    result_sha256: str
    latency_ms: int
    error: str = ""


_CALLER: ContextVar[str | None] = ContextVar("axiom_tool_caller", default=None)
_TOOL_SINK: ContextVar[list[ToolCall] | None] = ContextVar("axiom_tool_calls", default=None)


@contextmanager
def acting_as(caller: str) -> Iterator[None]:
    """Make tool calls inside the block as `caller` (a workflow step, or "operator")."""
    token = _CALLER.set(caller)
    try:
        yield
    finally:
        _CALLER.reset(token)


@contextmanager
def collect_tool_calls() -> Iterator[list[ToolCall]]:
    sink: list[ToolCall] = []
    token = _TOOL_SINK.set(sink)
    try:
        yield sink
    finally:
        _TOOL_SINK.reset(token)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class MCPToolClient:
    """Calls tools on one MCP server, sending a bearer token."""

    def __init__(self, url: str, secret: str, timeout: float = 30.0) -> None:
        if not secret:
            raise ValueError("An MCP shared secret is required.")
        self.url = url
        self._secret = secret
        self._timeout = timeout

    def _token(self) -> str:
        return self._secret

    async def list_tools(self) -> list[str]:
        async with self._session() as client:
            result = await client.list_tools()
        return sorted(tool.name for tool in result.tools)

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool and return its structured result."""
        async with self._session() as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content)
            raise MCPToolError(f"{tool} failed: {text}".strip())
        if result.structured_content is None:
            raise MCPToolError(f"{tool} returned no structured content.")
        return dict(result.structured_content)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=self._timeout) as http,
            Client(streamable_http_client(self.url, http_client=http)) as client,
        ):
            yield client


class GatewayClient(MCPToolClient):
    """Calls tools through the tool gateway with the current step's own token."""

    def __init__(self, url: str, tokens: Mapping[str, str], timeout: float = 30.0) -> None:
        if not tokens or not all(tokens.values()):
            raise ValueError("Tool gateway tokens are required.")
        super().__init__(url, "unused", timeout)
        self._tokens = dict(tokens)

    def _token(self) -> str:
        caller = _CALLER.get()
        if caller is None or caller not in self._tokens:
            raise MCPToolError(f"no tool gateway token for caller {caller!r}")
        return self._tokens[caller]

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        caller = _CALLER.get() or "none"
        started = time.monotonic()
        result: dict[str, Any] = {}
        error = ""
        try:
            result = await super().call(tool, arguments)
            return result
        except Exception as exc:
            error = str(exc)[:300] or type(exc).__name__
            raise
        finally:
            sink = _TOOL_SINK.get()
            if sink is not None:
                call_id = str(result.get("gateway_call_id", "")) or _call_id_from(error)
                sink.append(
                    ToolCall(
                        node=caller,
                        tool=tool,
                        allowed="refused by the tool gateway" not in error,
                        call_id=call_id,
                        arguments_sha256=fingerprint(arguments),
                        result_sha256=fingerprint(result) if result else "",
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error=error,
                    )
                )


def _call_id_from(error: str) -> str:
    """The gateway puts its call id in brackets at the end of a refusal or failure."""
    if error.endswith("]") and "[call " in error:
        return error.rsplit("[call ", 1)[1].rstrip("]")
    return ""
