"""MCPToolClient against a real MCP server over HTTP on a local port."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from axiom.mcp_clients.client import MCPToolClient, MCPToolError

SECRET = "test-secret"


def _build_app() -> ASGIApp:
    server = MCPServer(name="test-mcp")

    @server.tool()
    def add(a: int, b: int) -> dict[str, int]:
        """Add two integers."""
        return {"sum": a + b}

    @server.tool()
    def refuse(reason: str) -> dict[str, str]:
        """Always fail with the given reason."""
        raise ToolError(reason)

    inner = server.streamable_http_app(json_response=True, stateless_http=True)

    async def require_secret(scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        if scope["type"] == "http" and headers.get(b"authorization") != f"Bearer {SECRET}".encode():
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await inner(scope, receive, send)

    return require_secret


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(_build_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("test MCP server did not start within 30 seconds")
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_call_returns_structured_result(server_url: str) -> None:
    client = MCPToolClient(server_url, SECRET)
    assert asyncio.run(client.list_tools()) == ["add", "refuse"]
    assert asyncio.run(client.call("add", {"a": 2, "b": 3})) == {"sum": 5}


def test_tool_error_is_raised(server_url: str) -> None:
    client = MCPToolClient(server_url, SECRET)
    with pytest.raises(MCPToolError, match="not allowed"):
        asyncio.run(client.call("refuse", {"reason": "not allowed"}))


def test_wrong_secret_is_rejected(server_url: str) -> None:
    client = MCPToolClient(server_url, "wrong")
    with pytest.raises(Exception):  # noqa: B017 - the transport surfaces the 401 as an error
        asyncio.run(client.list_tools())


def test_secret_is_required() -> None:
    with pytest.raises(ValueError):
        MCPToolClient("http://localhost/mcp", "")
