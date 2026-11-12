"""Week 4 acceptance check against the running docker compose stack.

A job pauses at awaiting_review, survives a restart of Postgres, the API and the worker,
and finalizes only on approval by a reviewer who did not submit it.

1. Submit a package and wait for the draft (awaiting_review).
2. The submitter's key cannot review it.
3. Restart postgres, api and worker. The job is still awaiting_review with the same draft.
4. Approve with the reviewer key. The job resumes from its checkpoint and is approved.
5. The evidence chain verifies, names a reviewer other than the submitter, shows finalize
   only after the approval, and carries related guidance from Regulus.

Usage: docker compose up -d --build && uv run python scripts/check_week4.py
Reads SUBMITTER_API_KEYS, REVIEWER_API_KEYS and AUDITOR_API_KEYS from the environment or
.env, and uses the first key of each. Keys are never printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("AXIOM_API_URL_HOST", "http://localhost:8000")
PACKAGE = "pkg-08"

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def first_key(name: str) -> str:
    raw = os.environ.get(name, "")
    env_file = ROOT / ".env"
    if not raw and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                raw = line.split("=", 1)[1].strip()
    entry = raw.split(",")[0]
    return entry.split(":", 1)[1] if ":" in entry else ""


def call(
    method: str, path: str, key: str, body: dict[str, Any] | None = None, idem: bool = False
) -> tuple[int, dict[str, Any]]:
    headers = {"X-API-Key": key, "content-type": "application/json"}
    if idem:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")
    except OSError:
        return 0, {}


def wait_for(
    job_id: str, key: str, statuses: tuple[str, ...], seconds: int = 240
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for _ in range(seconds):
        status, body = call("GET", f"/v1/memos/{job_id}", key)
        if status == 200 and body.get("status") in statuses:
            return body
        time.sleep(1)
    return body


def wait_ready(seconds: int = 120) -> bool:
    for _ in range(seconds):
        try:
            with urllib.request.urlopen(f"{API}/v1/ready", timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.HTTPError, OSError):
            pass
        time.sleep(1)
    return False


def memo_sha(job: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(job.get("memo"), sort_keys=True).encode()).hexdigest()


def main() -> int:
    sub, rev, aud = (
        first_key(k) for k in ("SUBMITTER_API_KEYS", "REVIEWER_API_KEYS", "AUDITOR_API_KEYS")
    )
    if not (sub and rev and aud):
        print("Set SUBMITTER_API_KEYS, REVIEWER_API_KEYS and AUDITOR_API_KEYS in .env first.")
        return 2
    if not wait_ready(10):
        print(f"The API at {API} is not ready. Start the stack: docker compose up -d --build")
        return 2

    # 1. Submit and wait for the draft.
    status, accepted = call("POST", "/v1/memos", sub, {"package_id": PACKAGE}, idem=True)
    job_id = accepted.get("job_id", "")
    check("submission accepted", status == 202 and bool(job_id), job_id)
    drafted = wait_for(job_id, sub, ("awaiting_review", "failed"))
    check(
        "job pauses at awaiting_review",
        drafted.get("status") == "awaiting_review",
        str(drafted.get("status")),
    )
    before = memo_sha(drafted)

    # 2. The submitter's key cannot decide.
    status, _ = call("POST", f"/v1/memos/{job_id}/review", sub, {"decision": "approve"}, idem=True)
    check("the submitter's key cannot review", status == 403, f"HTTP {status}")

    # 3. Restart the database, the API and the worker while the job is paused.
    restart = subprocess.run(
        ["docker", "compose", "restart", "postgres", "api", "worker"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    check(
        "postgres, api and worker restarted",
        restart.returncode == 0,
        restart.stderr.strip()[-200:] if restart.returncode else "",
    )
    check("API ready after restart", wait_ready())
    status, after = call("GET", f"/v1/memos/{job_id}", sub)
    check(
        "still awaiting_review after restart",
        after.get("status") == "awaiting_review",
        str(after.get("status")),
    )
    check("draft unchanged by the restart", memo_sha(after) == before)

    # 4. Approve with the reviewer key; the worker resumes from the checkpoint.
    status, _ = call(
        "POST",
        f"/v1/memos/{job_id}/review",
        rev,
        {"decision": "approve", "comments": "Week 4 acceptance check."},
        idem=True,
    )
    check("reviewer's approval accepted", status == 202, f"HTTP {status}")
    final = wait_for(job_id, sub, ("approved", "rejected", "failed"))
    check("job finalizes as approved", final.get("status") == "approved", str(final.get("status")))

    # 5. The evidence shows who decided, in what order.
    status, bundle = call("GET", f"/v1/memos/{job_id}/evidence", aud)
    events = bundle.get("events", [])
    types = [e["type"] for e in events]
    check(
        "evidence chain verifies",
        bool(bundle.get("chain", {}).get("verified")),
        f"{len(events)} events",
    )
    reviewers = {e["actor"] for e in events if e["type"] == "review_submitted"}
    check(
        "reviewer is not the submitter",
        bool(reviewers) and bundle.get("submitter") not in reviewers,
    )
    finalized = [
        i
        for i, e in enumerate(events)
        if e["type"] == "node_completed" and e["payload"].get("node") == "finalize"
    ]
    approved_at = types.index("review_submitted") if "review_submitted" in types else len(types)
    check(
        "finalize ran once, after the approval", len(finalized) == 1 and finalized[0] > approved_at
    )
    check("job_approved is the last event", bool(types) and types[-1] == "job_approved")
    guidance = bundle.get("related_guidance") or {}
    check(
        "related guidance from Regulus attached",
        guidance.get("status") == "attached",
        f"{len(guidance.get('provisions', []))} provisions",
    )

    failed = [r for r in results if not r[1]]
    print(
        f"\nWeek 4: {'PASSED' if not failed else 'FAILED'} ({len(results) - len(failed)}"
        f"/{len(results)} checks)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
