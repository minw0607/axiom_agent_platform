"""Gate 1 acceptance check against the running docker compose stack.

1. Every service is up.
2. An MCP call to Regulus returns cited provisions.
3. A retrieval for one job cannot return another job's documents.

Usage: docker compose up -d --build && uv run python scripts/check_gate1.py
Reads MCP_SHARED_SECRET (and optional service URLs) from the environment, .env or the
demo's generated .demo/keys.env.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from axiom.mcp_clients.client import MCPToolClient

REGULUS_URL = os.environ.get("REGULUS_MCP_URL_HOST", "http://localhost:8001/mcp")
GKN_URL = os.environ.get("GKN_MCP_URL_HOST", "http://localhost:8002/mcp")
PHOENIX_URL = os.environ.get("PHOENIX_URL_HOST", "http://localhost:6006")

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


def load_secret() -> str:
    secret = os.environ.get("MCP_SHARED_SECRET", "")
    root = Path(__file__).resolve().parents[1]
    # .env first, then the keyless demo's generated keys.
    for env_file in (root / ".env", root / ".demo" / "keys.env"):
        if not secret and env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("MCP_SHARED_SECRET="):
                    secret = line.split("=", 1)[1].strip()
    return secret


async def main() -> int:
    secret = load_secret()
    if not secret:
        print("MCP_SHARED_SECRET is not set (environment or .env).")
        return 2

    # 1. Services up, and MCP endpoints reject unauthenticated calls.
    for name, url in (("regulus-mcp", REGULUS_URL), ("gkn-mcp", GKN_URL)):
        base = url.rsplit("/mcp", 1)[0]
        check(f"{name} healthy", http_status(f"{base}/healthz") == 200)
        check(f"{name} rejects calls without the secret", http_status(url, "POST") == 401)
    check("phoenix UI reachable", http_status(PHOENIX_URL) == 200)

    # 2. Regulus returns cited provisions.
    regulus = MCPToolClient(REGULUS_URL, secret)
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

    # 3. Job isolation in GKN. Both documents match the query; only scope separates them.
    gkn = MCPToolClient(GKN_URL, secret)
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
        hits_a = await gkn.call("retrieve_evidence", {"query": query, "job_id": job_a, "k": 20})
        hits_b = await gkn.call("retrieve_evidence", {"query": query, "job_id": job_b, "k": 20})
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
