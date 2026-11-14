"""Gate 2 acceptance check: a stranger with no API keys runs the demo in under 10 minutes.

Checks a running stack:
1. The stack needs no .env: nothing in docker-compose.yml requires one.
2. Every service is up: the API, the tool gateway (with the operator token) and Phoenix.
3. The review page loads, with its strict content security policy.
4. Demo mode offers every catalog package.
5. Every package runs end to end through the API: a draft, two revision rounds and an
   approval, with a verified evidence chain and related guidance attached.
6. No model was called: every model call in every evidence bundle is a replayed recording.
7. Every tool call went through the gateway, was allowed, and came from the step that owns
   the tool (ADR 0014).

    uv run python scripts/check_gate2.py

With --clean-run it first does what the README asks of a stranger, and times it: clone the
repository into an empty folder with no .env, `docker compose up -d --build`, and wait until
the API is ready. Then it runs the checks against that copy and removes it. Stop any other
copy of the stack first (they share ports). For a cold start, remove the stack's images
first (`docker compose down --rmi all`); --no-cache also rebuilds the images without the
local build cache.

    uv run python scripts/check_gate2.py --clean-run --no-cache

Keys come from the environment, then .env, then the demo's generated .demo/keys.env, in the
folder of the stack being checked. Keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/minw0607/axiom_agent_platform.git"
API = os.environ.get("AXIOM_API_URL_HOST", "http://localhost:8000")
GATEWAY_URL = os.environ.get("TOOL_GATEWAY_URL_HOST", "http://localhost:8003/mcp")
# Which step may call which tool, as config/tool_gateway.yaml allows.
STEP_TOOLS = {
    "intake": "ingest_documents",
    "drafter": "retrieve_evidence",
    "compliance": "retrieve_evidence",
    "finalize": "lookup_provisions",
}
PHOENIX_URL = os.environ.get("PHOENIX_URL_HOST", "http://localhost:6006")
TARGET_SECONDS = 600
COMMENTS = (
    "Add a sentence on liquidity that cites the current ratio.",
    "State the main condition you would propose, or say that none is needed.",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)


def setting(root: Path, name: str) -> str:
    """From the environment, then .env, then the generated demo keys."""
    value = os.environ.get(name, "")
    for path in (root / ".env", root / ".demo" / "keys.env"):
        if value or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip()
    return value


def role_key(root: Path, name: str) -> str:
    entry = setting(root, name).split(",")[0]
    return entry.split(":", 1)[1] if ":" in entry else ""


def http(
    method: str, url: str, key: str = "", body: dict[str, Any] | None = None
) -> tuple[int, Any, dict[str, str]]:
    headers = {"content-type": "application/json", "Idempotency-Key": str(uuid.uuid4())}
    if key:
        headers["X-API-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            ctype = r.headers.get("content-type", "")
            parsed = json.loads(raw) if "json" in ctype else raw.decode()
            return r.status, parsed, dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, None, dict(exc.headers)
    except OSError:
        return 0, None, {}


def wait_ready(timeout: float, url: str = f"{API}/v1/ready") -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if http("GET", url)[0] == 200:
            return True
        time.sleep(2)
    return False


async def mcp_tools(url: str, secret: str) -> list[str]:
    from axiom.mcp_clients.client import MCPToolClient

    return await MCPToolClient(url, secret).list_tools()


def wait_status(job_id: str, key: str, want: set[str], timeout: float = 240) -> str:
    end = time.monotonic() + timeout
    status = ""
    while time.monotonic() < end:
        code, job, _ = http("GET", f"{API}/v1/memos/{job_id}", key)
        status = job["status"] if code == 200 else f"http {code}"
        if status in want:
            return status
        time.sleep(1)
    return status


def run_checks(root: Path) -> None:
    compose = (root / "docker-compose.yml").read_text()
    check("The stack needs no .env", ":?" not in compose, "no required settings in compose")

    sub = role_key(root, "SUBMITTER_API_KEYS")
    rev = role_key(root, "REVIEWER_API_KEYS")
    aud = role_key(root, "AUDITOR_API_KEYS")
    operator = setting(root, "TOOL_GATEWAY_OPERATOR_TOKEN")
    source = "from .env" if (root / ".env").is_file() else "generated by demo-keys"
    check("A key for every role, and a gateway token", all((sub, rev, aud, operator)), source)
    if not all((sub, rev, aud, operator)):
        return

    check("API ready", http("GET", f"{API}/v1/ready")[0] == 200)
    try:
        tools = asyncio.run(mcp_tools(GATEWAY_URL, operator))
        wanted = set(STEP_TOOLS.values()) | {"delete_job"}
        check("The tool gateway answers", set(tools) == wanted, ", ".join(tools))
    except Exception as exc:  # report the failure as a check result
        check("The tool gateway answers", False, type(exc).__name__)
    # Phoenix starts more slowly than the API; give it a minute.
    check("Phoenix trace viewer up", wait_ready(60, PHOENIX_URL))

    code, _, headers = http("GET", f"{API}/review")
    csp = {k.lower(): v for k, v in headers.items()}.get("content-security-policy", "")
    check("Review page loads with a strict CSP", code == 200 and "default-src 'none'" in csp)

    from axiom.catalog import list_packages
    from axiom.settings import Settings

    catalog = [p["package_id"] for p in list_packages(Settings.from_env().packages_dir)]
    code, offered, _ = http("GET", f"{API}/v1/packages", sub)
    listed = [p["package_id"] for p in offered] if code == 200 else []
    check(
        "Demo mode offers every catalog package",
        listed == catalog,
        f"{len(listed)} of {len(catalog)}",
    )

    started = time.monotonic()
    jobs = {}
    for package_id in listed:
        code, body, _ = http("POST", f"{API}/v1/memos", sub, {"package_id": package_id})
        if code == 202:
            jobs[package_id] = body["job_id"]
    failures: list[str] = []
    origins: set[str] = set()
    calls = 0
    tool_calls = 0
    tool_problems: list[str] = []
    first_draft: float | None = None
    for package_id, job_id in jobs.items():
        status = wait_status(job_id, sub, {"awaiting_review", "failed"})
        first_draft = first_draft or time.monotonic() - started
        for comments in COMMENTS:
            if status != "awaiting_review":
                break
            http(
                "POST",
                f"{API}/v1/memos/{job_id}/review",
                rev,
                {"decision": "revise", "comments": comments},
            )
            status = wait_status(job_id, sub, {"awaiting_review", "failed"}, timeout=240)
        if status == "awaiting_review":
            http(
                "POST",
                f"{API}/v1/memos/{job_id}/review",
                rev,
                {"decision": "approve", "comments": "Gate 2 check."},
            )
            status = wait_status(job_id, sub, {"approved", "failed"})
        code, bundle, _ = http("GET", f"{API}/v1/memos/{job_id}/evidence", aud)
        verified = code == 200 and bundle["chain"]["verified"]
        guidance = code == 200 and (bundle.get("related_guidance") or {}).get("status")
        reviews = len(http("GET", f"{API}/v1/memos/{job_id}", aud)[1].get("reviews", []))
        if code == 200:
            origins |= {c["origin"] for c in bundle["model_calls"]}
            calls += len(bundle["model_calls"])
            used = bundle.get("tool_calls", [])
            tool_calls += len(used)
            steps = {t["node"] for t in used}
            if steps != set(STEP_TOOLS):
                tool_problems.append(f"{package_id}: steps {sorted(steps)}")
            for t in used:
                if not t["allowed"] or STEP_TOOLS.get(t["node"]) != t["tool"] or not t["call_id"]:
                    tool_problems.append(f"{package_id}: {t['node']} {t['tool']}")
        if status != "approved" or not verified or guidance != "attached" or reviews != 3:
            failures.append(f"{package_id}: {status}, chain {verified}, guidance {guidance}")
    check(
        "Every package: draft, two revisions, approval, verified chain, guidance attached",
        len(jobs) == len(listed) and not failures,
        f"{len(jobs) - len(failures)} of {len(listed)} in {time.monotonic() - started:.0f} s; "
        f"first draft after {first_draft or 0:.0f} s"
        + (f"; {'; '.join(failures[:3])}" if failures else ""),
    )
    check(
        "No model was called: every response is a replayed recording",
        calls > 0 and origins == {"recorded"},
        f"{calls} model calls, origin {', '.join(sorted(origins)) or 'none'}",
    )
    check(
        "Every tool call went through the gateway, from the step that owns the tool",
        tool_calls > 0 and not tool_problems,
        f"{tool_calls} tool calls" + (f"; {'; '.join(tool_problems[:3])}" if tool_problems else ""),
    )


def compose(folder: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", "axiom-gate2", *args],
        cwd=folder,
        env=env,
        capture_output=True,
        text=True,
    )


def clean_run(repo: str, ref: str, no_cache: bool, keep: bool) -> int:
    if http("GET", f"{API}/v1/health")[0]:
        print("Another copy of the stack is using port 8000. Stop it first: docker compose stop")
        return 2
    # Only what a stranger's shell would have: no settings from this repo's .env.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_HOST", "TMPDIR")}
    folder = Path(tempfile.mkdtemp(prefix="axiom-gate2-")) / "axiom_agent_platform"
    print(f"Clean run: cloning {repo}@{ref} into an empty folder with no .env", flush=True)
    t0 = time.monotonic()
    code = 1
    try:
        clone = subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--branch", ref, repo, str(folder)],
            env=env,
            capture_output=True,
            text=True,
        )
        if clone.returncode:
            print(clone.stderr)
            return 1
        check("The clone has no .env", not (folder / ".env").exists())
        if no_cache:
            build = compose(folder, "build", "--no-cache", env=env)
            if build.returncode:
                print(build.stderr[-2000:])
                return 1
        up = compose(folder, "up", "-d", "--build", env=env)
        if up.returncode:
            print(up.stderr[-2000:])
            return 1
        ready = wait_ready(timeout=1200)
        setup = time.monotonic() - t0
        check(
            "From git clone to a ready stack in under 10 minutes",
            ready and setup < TARGET_SECONDS,
            f"{setup / 60:.1f} min" + ("" if ready else ", never became ready"),
        )
        if not ready:
            return 1
        logs = compose(folder, "logs", "demo-keys", "--no-log-prefix", env=env).stdout
        check("demo-keys prints a sign-in key for each role", logs.count("generated now") == 1)
        run_checks(folder)
        code = 0 if all(r[1] for r in results) else 1
        return code
    finally:
        if not keep:
            compose(folder, "down", "-v", env=env)
            shutil.rmtree(folder.parent, ignore_errors=True)
        else:
            print(f"Kept the copy in {folder} (project axiom-gate2)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--clean-run", action="store_true", help="clone, start and time it")
    parser.add_argument("--repo", default=REPO_URL)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--no-cache", action="store_true", help="build without the build cache")
    parser.add_argument("--keep", action="store_true", help="leave the clean copy running")
    args = parser.parse_args()
    if args.clean_run:
        clean_run(args.repo, args.ref, args.no_cache, args.keep)
    else:
        run_checks(ROOT)
    passed = sum(r[1] for r in results)
    verdict = "PASSED" if passed == len(results) else "FAILED"
    print(f"\nGate 2: {verdict} ({passed}/{len(results)} checks)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
