"""Gate 1 acceptance check against the running docker compose stack.

1. Every service is up, and the tool services are reachable only through the tool gateway.
2. The gateway refuses calls without a known token, and refuses a workflow step's calls
   outside its own tools (ADR 0014).
3. An MCP call to Regulus returns cited provisions.
4. A retrieval for one job cannot return another job's documents.

Usage: docker compose up -d --build && uv run python scripts/check_gate1.py
Reads the tool gateway tokens from the environment, .env or the demo's generated
.demo/keys.env. Tokens are never printed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from axiom.mcp_clients.client import MCPToolClient, MCPToolError

GATEWAY_URL = os.environ.get("TOOL_GATEWAY_URL_HOST", "http://localhost:8003/mcp")
PHOENIX_URL = os.environ.get("PHOENIX_URL_HOST", "http://localhost:6006")
# Where the tool services listened before the gateway; nothing should answer there now.
OLD_PORTS = {"regulus-mcp": 8001, "gkn-mcp": 8002}

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def http_status(url: str, method: str = "GET") -> int:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def load_setting(name: str) -> str:
    value = os.environ.get(name, "")
    root = Path(__file__).resolve().parents[1]
    # .env first, then the keyless demo's generated keys.
    for env_file in (root / ".env", root / ".demo" / "keys.env"):
        if not value and env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    value = line.split("=", 1)[1].strip()
    return value


async def refused(client: MCPToolClient, tool: str, arguments: dict[str, object]) -> bool:
    try:
        await client.call(tool, arguments)
    except MCPToolError as exc:
        return "refused" in str(exc)
    return False


async def main() -> int:
    operator = load_setting("TOOL_GATEWAY_OPERATOR_TOKEN")
    steps = dict(
        item.split(":", 1) for item in load_setting("TOOL_GATEWAY_TOKENS").split(",") if ":" in item
    )
    if not operator or "drafter" not in steps:
        print("Tool gateway tokens are not set (environment, .env or .demo/keys.env).")
        return 2

    # 1. Services up; the tool services answer only inside the stack.
    base = GATEWAY_URL.rsplit("/mcp", 1)[0]
    check("tool-gateway healthy", http_status(f"{base}/healthz") == 200)
    for name, port in OLD_PORTS.items():
        status = http_status(f"http://localhost:{port}/healthz")
        check(f"{name} is not reachable from outside the stack", status == 0, f"port {port}")
    check("phoenix UI reachable", http_status(PHOENIX_URL) == 200)

    # 2. The gateway's own checks.
    check("The gateway rejects calls without a token", http_status(GATEWAY_URL, "POST") == 401)
    drafter = MCPToolClient(GATEWAY_URL, steps["drafter"])
    job = f"gate1-scope-{uuid.uuid4().hex[:8]}"
    check(
        "The gateway refuses the drafter a tool it does not need",
        await refused(drafter, "delete_job", {"job_id": job}),
        "delete_job",
    )
    check(
        "The gateway refuses the drafter a collection it does not need",
        await refused(
            drafter,
            "retrieve_evidence",
            {"query": "covenant", "job_id": job, "k": 1, "collection": "lending_refs"},
        ),
        "retrieve_evidence in lending_refs",
    )

    # 3. Regulus returns cited provisions, through the gateway.
    regulus = MCPToolClient(GATEWAY_URL, operator)
    lookup = await regulus.call(
        "lookup_provisions",
        {
            "description": "An AI assistant drafts credit memos; outputs must be tested "
            "for bias across borrower groups and logged for audit.",
            "k": 5,
        },
    )
    provisions = lookup["provisions"]
    cited = all(p["citation"] and p["source_url"].startswith("http") for p in provisions)
    check(
        "Regulus returns cited provisions",
        bool(provisions) and cited,
        f"{len(provisions)} provisions; top: {provisions[0]['citation'] if provisions else '-'}",
    )

    # 4. Job isolation in GKN, through the gateway. Both documents match the query; only the
    # job scope separates them.
    gkn = MCPToolClient(GATEWAY_URL, operator)
    run = uuid.uuid4().hex[:8]
    job_a, job_b = f"gate1-a-{run}", f"gate1-b-{run}"
    query = "revenue EBITDA total debt term loan"
    try:
        await gkn.call(
            "ingest_documents",
            {
                "job_id": job_a,
                "documents": [
                    {
                        "doc_id": "a",
                        "text": "Alpha revenue 12.4m, EBITDA 2.1m, total debt 6.0m on a term loan.",
                    }
                ],
            },
        )
        await gkn.call(
            "ingest_documents",
            {
                "job_id": job_b,
                "documents": [
                    {
                        "doc_id": "b",
                        "text": "Bravo revenue 30.2m, EBITDA 4.4m, total debt 9.5m on a term loan.",
                    }
                ],
            },
        )
        hits_a = await gkn.call(
            "retrieve_evidence", {"query": query, "job_id": job_a, "k": 20, "collection": "job"}
        )
        hits_b = await gkn.call(
            "retrieve_evidence", {"query": query, "job_id": job_b, "k": 20, "collection": "job"}
        )
        docs_a = {h["doc_id"] for h in hits_a["hits"]}
        docs_b = {h["doc_id"] for h in hits_b["hits"]}
        check(
            "GKN retrieval for one job cannot return another job's documents",
            docs_a == {"a"} and docs_b == {"b"},
            f"job A saw {sorted(docs_a)}, job B saw {sorted(docs_b)}",
        )
    finally:
        for job in (job_a, job_b):
            await gkn.call("delete_job", {"job_id": job})

    failed = [name for name, passed, _ in results if not passed]
    print(
        f"\nGate 1: {'PASSED' if not failed else 'FAILED'} ({len(results) - len(failed)}"
        f"/{len(results)} checks)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
