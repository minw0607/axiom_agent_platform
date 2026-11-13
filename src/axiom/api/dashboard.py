"""GET /v1/dashboard: a summary for the signed-in role, computed from the job store.

Each role gets the counts it acts on and a short list of memos to open, most urgent first:

- relationship manager: their own requests by stage, and failed jobs with the reason;
- credit officer: the review queue, led by memos with high-severity risk, and the revised
  drafts back from their own revision requests;
- auditor: integrity of the evidence chain, failed jobs, safety checks that found a
  problem, and recent decisions.

The page renders tiles and items as given; every number comes from here, not from the page.
Names of people are not included: who did what is in the evidence bundle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from axiom.api.auth import Principal, Role
from axiom.evidence.log import verify_chain
from axiom.jobs.store import JobRecord, JobStatus, JobStore, JobSummary

# How many of the most recently updated jobs a dashboard looks at.
SCAN_LIMIT = 200
# The auditor's dashboard re-verifies each job's evidence chain, so it looks at fewer.
AUDIT_SCAN_LIMIT = 50
MAX_ITEMS = 8
IN_PROGRESS: tuple[JobStatus, ...] = ("queued", "running", "revising", "finalizing")
DECIDED: tuple[JobStatus, ...] = ("approved", "rejected")
Tone = Literal["", "good", "warn", "bad", "accent"]


class Tile(BaseModel):
    key: str
    label: str
    value: int | None = Field(description="A count, or seconds when unit is seconds")
    unit: Literal["count", "seconds"] = "count"
    tone: Tone = ""
    detail: str = ""


class DashboardItem(BaseModel):
    job_id: str
    package_id: str
    status: JobStatus
    updated_at: datetime
    reason: str
    tone: Tone = ""
    code: str | None = Field(default=None, description="An error or rule code, when there is one")


class DashboardSection(BaseModel):
    title: str
    empty: str
    items: list[DashboardItem]


class Dashboard(BaseModel):
    role: Role
    as_of: datetime
    jobs_considered: int
    scan_limit: int
    tiles: list[Tile]
    sections: list[DashboardSection]


def _item(
    job: JobSummary | JobRecord, reason: str, tone: Tone = "", code: str | None = None
) -> DashboardItem:
    return DashboardItem(
        job_id=job.job_id,
        package_id=job.package_id,
        status=job.status,
        updated_at=job.updated_at,
        reason=reason,
        tone=tone,
        code=code,
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def high_risk(result: dict[str, Any]) -> list[str]:
    """Codes of high-severity risk flags and triggered high-severity policy findings."""
    flags = [f["code"] for f in result.get("risk_flags", []) if f.get("severity") == "high"]
    findings = [
        f["rule_id"]
        for f in result.get("findings", [])
        if f.get("outcome") == "triggered" and f.get("severity") == "high"
    ]
    return list(dict.fromkeys(flags + findings))


def _last_review(result: dict[str, Any]) -> dict[str, Any] | None:
    reviews = result.get("reviews") or []
    return reviews[-1] if reviews else None


async def _submitter(
    store: JobStore, principal: Principal
) -> tuple[list[Tile], list[DashboardSection], int]:
    jobs = await store.list_jobs(principal.name, None, SCAN_LIMIT)

    def count(*statuses: str) -> int:
        return sum(j.status in statuses for j in jobs)

    failed = [j for j in jobs if j.status == "failed"]
    tiles = [
        Tile(key="in_progress", label="Being drafted", value=count(*IN_PROGRESS)),
        Tile(
            key="awaiting_review",
            label="With a credit officer",
            value=count("awaiting_review"),
            tone="accent" if count("awaiting_review") else "",
        ),
        Tile(
            key="approved",
            label="Approved",
            value=count("approved"),
            tone="good" if count("approved") else "",
        ),
        Tile(key="rejected", label="Rejected", value=count("rejected")),
        Tile(key="failed", label="Failed", value=len(failed), tone="bad" if failed else ""),
    ]
    failed_items = []
    for j in failed[:MAX_ITEMS]:
        record = await store.get(j.job_id)
        code = record.error_code if record else None
        failed_items.append(_item(j, "The platform could not finish this memo", "bad", code))
    returned = [
        _item(
            j,
            f"Decided: memo {'approved' if j.status == 'approved' else 'rejected'}",
            "good" if j.status == "approved" else "",
        )
        for j in jobs
        if j.status in DECIDED
    ][:MAX_ITEMS]
    sections = [
        DashboardSection(
            title="Failed requests", empty="No request has failed.", items=failed_items
        ),
        DashboardSection(
            title="Recently decided", empty="No memo of yours has been decided yet.", items=returned
        ),
    ]
    return tiles, sections, len(jobs)


async def _reviewer(
    store: JobStore, principal: Principal, now: datetime
) -> tuple[list[Tile], list[DashboardSection], int]:
    jobs = await store.list_jobs(None, None, SCAN_LIMIT)
    waiting: list[tuple[JobRecord, list[str], bool]] = []
    with_platform = 0
    for j in jobs:
        if j.status not in ("awaiting_review", "revising"):
            continue
        record = await store.get(j.job_id)
        if record is None:
            continue
        result = record.result or {}
        if record.status == "revising":
            # The decision waits on the job until the worker picks it up.
            pending = record.pending_review
            with_platform += bool(pending and pending.reviewer == principal.name)
        else:
            last = _last_review(result)
            mine = bool(
                last and last.get("reviewer") == principal.name and last.get("decision") == "revise"
            )
            waiting.append((record, high_risk(result), mine))
    # Risk first, then the longest wait.
    waiting.sort(key=lambda w: (not w[1], w[0].updated_at))
    oldest = min((w[0].updated_at for w in waiting), default=None)
    risky = sum(bool(w[1]) for w in waiting)
    back = sum(w[2] for w in waiting)
    tiles = [
        Tile(
            key="high_risk",
            label="Waiting, with high-severity risk",
            value=risky,
            tone="bad" if risky else "good",
            detail="High-severity risk flags or policy findings",
        ),
        Tile(
            key="awaiting_review",
            label="Waiting for review",
            value=len(waiting),
            tone="accent" if waiting else "",
        ),
        Tile(
            key="oldest_wait",
            label="Longest wait",
            value=int((now - oldest).total_seconds()) if oldest else None,
            unit="seconds",
            tone="warn" if oldest and (now - oldest).total_seconds() > 86400 else "",
        ),
        Tile(
            key="revised_back",
            label="Revised drafts back for you",
            value=back,
            tone="accent" if back else "",
        ),
        Tile(key="revising", label="Your revision requests being redrafted", value=with_platform),
    ]
    items = []
    for record, codes, mine in waiting[:MAX_ITEMS]:
        if codes:
            reason = f"{_plural(len(codes), 'high-severity risk')}: {', '.join(codes)}"
        else:
            reason = "No high-severity risk raised"
        if mine:
            reason += " · revised after your comments"
        items.append(_item(record, reason, "bad" if codes else "", codes[0] if codes else None))
    sections = [
        DashboardSection(
            title="Review queue, riskiest first",
            empty="Nothing is waiting for review.",
            items=items,
        )
    ]
    return tiles, sections, len(jobs)


async def _auditor(store: JobStore) -> tuple[list[Tile], list[DashboardSection], int]:
    jobs = await store.list_jobs(None, None, AUDIT_SCAN_LIMIT)
    broken: list[DashboardItem] = []
    failed: list[DashboardItem] = []
    triggered: list[DashboardItem] = []
    decided: list[DashboardItem] = []
    for j in jobs:
        record = await store.get(j.job_id)
        if record is None:
            continue
        chain = verify_chain(await store.events(j.job_id))
        if not chain.verified:
            broken.append(
                _item(j, f"Evidence chain breaks at event {chain.first_broken_seq}", "bad")
            )
        result = record.result or {}
        controls = sorted(
            {g["control"] for g in result.get("guardrails", []) if g.get("outcome") == "triggered"}
        )
        if controls:
            fixed = record.status != "failed"
            triggered.append(
                _item(
                    j,
                    f"{'Found a problem, then passed' if fixed else 'Found a problem'}: "
                    f"{', '.join(controls)}",
                    "bad" if not fixed else "warn",
                    controls[0],
                )
            )
        if record.status == "failed":
            failed.append(_item(j, "Job failed", "bad", record.error_code))
        if record.status in DECIDED:
            last = _last_review(result)
            round_ = f" in review round {last['round']}" if last else ""
            decided.append(
                _item(
                    j,
                    f"Memo {record.status}{round_}",
                    "good" if record.status == "approved" else "",
                )
            )
    tiles = [
        Tile(
            key="chain_broken",
            label="Records failing the integrity check",
            value=len(broken),
            tone="bad" if broken else "good",
            detail=f"Hash chain re-verified for the {len(jobs)} most recent jobs",
        ),
        Tile(key="failed", label="Failed jobs", value=len(failed), tone="bad" if failed else ""),
        Tile(
            key="guardrails_triggered",
            label="Jobs where a safety check found a problem",
            value=len(triggered),
            tone="warn" if triggered else "",
        ),
        Tile(key="decided", label="Decided by a credit officer", value=len(decided)),
    ]
    sections = [
        DashboardSection(
            title="Integrity check failures",
            empty="Every evidence chain verifies.",
            items=broken[:MAX_ITEMS],
        ),
        DashboardSection(title="Failed jobs", empty="No job failed.", items=failed[:MAX_ITEMS]),
        DashboardSection(
            title="Safety checks that found a problem",
            empty="No safety check found a problem.",
            items=triggered[:MAX_ITEMS],
        ),
        DashboardSection(
            title="Recent decisions",
            empty="No memo has been decided yet.",
            items=decided[:MAX_ITEMS],
        ),
    ]
    return tiles, sections, len(jobs)


async def build_dashboard(
    store: JobStore, principal: Principal, now: datetime | None = None
) -> Dashboard:
    now = now or datetime.now(UTC)
    if principal.role == "submitter":
        tiles, sections, n = await _submitter(store, principal)
        limit = SCAN_LIMIT
    elif principal.role == "reviewer":
        tiles, sections, n = await _reviewer(store, principal, now)
        limit = SCAN_LIMIT
    else:
        tiles, sections, n = await _auditor(store)
        limit = AUDIT_SCAN_LIMIT
    return Dashboard(
        role=principal.role,
        as_of=now,
        jobs_considered=n,
        scan_limit=limit,
        tiles=tiles,
        sections=sections,
    )
