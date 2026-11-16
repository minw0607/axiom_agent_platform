"""Week 9 acceptance check, against the cloud demo (or any running API).

1. The endpoint is HTTPS and serves /v1/health.
2. Uploads are refused: the API takes only a catalog package ID, and has no upload path.
3. A full job completes: a draft, one revision round (which retrieves from GKN again) and
   an approval by a different person, with a verified evidence chain.
4. With --check-cap: new jobs are refused with daily-cap-reached once the day's cap is
   used. This spends the rest of today's jobs, so run it last.

    SUBMITTER_KEY=... REVIEWER_KEY=... AUDITOR_KEY=... \\
      uv run python scripts/check_week9.py https://<api_url from terraform output>

The keys are the part after the colon in Key Vault's submitter-api-keys, reviewer-api-keys
and auditor-api-keys secrets. They are never printed. --allow-http runs the checks against a
local API (for testing this script).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# A cold start wakes the API, worker, gateway and Regulus from zero; allow for it.
JOB_TIMEOUT = 600
REVISE_COMMENT = "Add a sentence on liquidity that cites the current ratio."
results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}" + (f" ({detail})" if detail else ""), flush=True)


def http(
    method: str,
    url: str,
    key: str = "",
    body: Any = None,
    content_type: str = "application/json",
) -> tuple[int, Any, dict[str, str]]:
    headers = {"content-type": content_type, "Idempotency-Key": str(uuid.uuid4())}
    if key:
        headers["X-API-Key"] = key
    if body is None:
        data = None
    elif isinstance(body, bytes):
        data = body
    else:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            parsed = json.loads(raw) if "json" in r.headers.get("content-type", "") else None
            return r.status, parsed, dict(r.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return exc.code, parsed, dict(exc.headers)
    except OSError:
        return 0, None, {}


def wait_status(api: str, job_id: str, key: str, want: set[str]) -> str:
    end = time.monotonic() + JOB_TIMEOUT
    status = ""
    while time.monotonic() < end:
        code, job, _ = http("GET", f"{api}/v1/memos/{job_id}", key)
        status = job["status"] if code == 200 and job else f"http {code}"
        if status in want:
            return status
        time.sleep(3)
    return status


def run(api: str, allow_http: bool, check_cap: bool) -> None:
    names = ("SUBMITTER_KEY", "REVIEWER_KEY", "AUDITOR_KEY")
    sub, rev, aud = (os.environ.get(name, "") for name in names)
    check("Keys for submitter, reviewer and auditor", all((sub, rev, aud)), "from the environment")
    if not all((sub, rev, aud)):
        return

    started = time.monotonic()
    code = 0
    # The first request may wake the API from zero.
    while time.monotonic() - started < 180 and code != 200:
        code = http("GET", f"{api}/v1/health")[0]
        if code != 200:
            time.sleep(5)
    https = api.startswith("https://")
    check(
        "HTTPS endpoint serves /v1/health",
        code == 200 and (https or allow_http),
        f"{'https' if https else 'http (allowed for a local test)'}, "
        f"{time.monotonic() - started:.0f}s to answer",
    )
    if code != 200:
        return

    code, offered, _ = http("GET", f"{api}/v1/packages", sub)
    packages = [p["package_id"] for p in offered or []]
    check("The catalog is offered", code == 200 and bool(packages), f"{len(packages)} packages")
    if not packages:
        return

    # 2. Uploads are refused.
    documents = {"package_id": packages[0], "documents": [{"name": "x.pdf", "content": "AAAA"}]}
    code_json = http("POST", f"{api}/v1/memos", sub, documents)[0]
    form = (
        b'--b\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n\r\n'
        b"AAAA\r\n--b--\r\n"
    )
    code_form = http("POST", f"{api}/v1/memos", sub, form, "multipart/form-data; boundary=b")[0]
    code_path = http("POST", f"{api}/v1/uploads", sub, form, "multipart/form-data; boundary=b")[0]
    check(
        "Uploads are refused",
        code_json == 422 and code_form in (415, 422) and code_path in (404, 405),
        f"documents field {code_json}, multipart {code_form}, /v1/uploads {code_path}",
    )

    # 3. A full job.
    started = time.monotonic()
    code, body, _ = http("POST", f"{api}/v1/memos", sub, {"package_id": packages[0]})
    if code != 202:
        check("A full job completes", False, f"submit returned {code}")
        return
    job_id = body["job_id"]
    status = wait_status(api, job_id, sub, {"awaiting_review", "failed"})
    drafted = time.monotonic() - started
    if status == "awaiting_review":
        http(
            "POST",
            f"{api}/v1/memos/{job_id}/review",
            rev,
            {"decision": "revise", "comments": REVISE_COMMENT},
        )
        status = wait_status(api, job_id, sub, {"awaiting_review", "failed"})
    if status == "awaiting_review":
        http(
            "POST",
            f"{api}/v1/memos/{job_id}/review",
            rev,
            {"decision": "approve", "comments": "Week 9 check."},
        )
        status = wait_status(api, job_id, sub, {"approved", "failed"})
    code, evidence, _ = http("GET", f"{api}/v1/memos/{job_id}/evidence", aud)
    verified = code == 200 and bool(evidence) and evidence["chain"]["verified"] is True
    check(
        "A full job completes",
        status == "approved" and verified,
        f"{packages[0]}: {status}, chain {'verified' if verified else 'NOT verified'}, "
        f"first draft {drafted:.0f}s, total {time.monotonic() - started:.0f}s",
    )

    # 4. The cap.
    if not check_cap:
        print("(cap not checked: pass --check-cap; it uses up today's remaining jobs)")
        return
    accepted = 0
    for _ in range(500):
        code, body, headers = http("POST", f"{api}/v1/memos", sub, {"package_id": packages[0]})
        if code != 202:
            break
        accepted += 1
    kind = (body or {}).get("type", "")
    retry = {k.lower(): v for k, v in headers.items()}.get("retry-after", "")
    check(
        "The daily job cap works",
        code == 429 and kind.endswith("#daily-cap-reached") and retry.isdigit(),
        f"{accepted} more accepted, then {code}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("api", help="the API's base URL, e.g. terraform output -raw api_url")
    parser.add_argument("--allow-http", action="store_true", help="for a local API only")
    parser.add_argument("--check-cap", action="store_true", help="use up today's jobs to test it")
    args = parser.parse_args()
    run(args.api.rstrip("/"), args.allow_http, args.check_cap)
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
