"""Authenticated MCP tool client for the Regulus and GKN services (streamable HTTP)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


class MCPToolError(RuntimeError):
    """A tool call reached the server but the tool reported an error."""


class MCPToolClient:
    """Calls tools on one MCP server, sending the shared secret as a bearer token."""

    def __init__(self, url: str, secret: str, timeout: float = 30.0) -> None:
        if not secret:
            raise ValueError("An MCP shared secret is required.")
        self.url = url
        self._headers = {"Authorization": f"Bearer {secret}"}
        self._timeout = timeout

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
        async with (
            httpx2.AsyncClient(headers=self._headers, timeout=self._timeout) as http,
            Client(streamable_http_client(self.url, http_client=http)) as client,
        ):
            yield client
