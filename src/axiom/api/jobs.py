"""The job routes every app gets: submit, list, get, source, review and evidence (ADR 0016).

The controls behind them live here once: authorization by role, idempotency, the daily job
cap, separation of duties and the revision limit. An app supplies its request body, public
job and evidence bundle, so each app's routes have their own schemas in OpenAPI.

No `from __future__ import annotations`: the route signatures use each app's models, which
FastAPI must see as real classes when the routes are built.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from axiom.api.auth import Principal, require
from axiom.api.errors import Problem, problems
from axiom.api.explain import SourceExcerpt, source_excerpt
from axiom.api.schemas import (
    JobAccepted,
    Links,
    MemoList,
    ReviewAccepted,
    ReviewRequest,
    to_summary,
)
from axiom.core.app import App
from axiom.core.types import UnknownInput
from axiom.evidence.log import verify_chain
from axiom.jobs.store import JobRecord, JobStatus, JobStore
from axiom.settings import Settings

MAX_IDEMPOTENCY_KEY = 128
NUMBERS = ("no", "one", "two", "three", "four", "five")

# Read from the request rather than declared as a FastAPI parameter, so a missing key gets the
# documented 400 problem; the OpenAPI document still shows the header as required.
IDEMPOTENCY_HEADER = {
    "parameters": [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "description": "A unique key per request; a retry with the same key and body "
            "returns the original job instead of creating another",
            "schema": {"type": "string", "minLength": 1, "maxLength": MAX_IDEMPOTENCY_KEY},
        }
    ]
}


def require_idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key or len(key) > MAX_IDEMPOTENCY_KEY:
        raise Problem(
            400,
            "idempotency-key-required",
            "An Idempotency-Key header is required",
            f"Send a unique key of at most {MAX_IDEMPOTENCY_KEY} characters per request.",
        )
    return key


def review_problems(app: App) -> dict[str, tuple[int, str, str, str]]:
    """Status, slug, title and detail for each reason a review is refused."""
    noun = app.noun
    limit = NUMBERS[app.max_revisions] if app.max_revisions < len(NUMBERS) else app.max_revisions
    return {
        "not-found": (404, "not-found", f"No such {noun} job", ""),
        "not-awaiting-review": (
            409,
            "not-awaiting-review",
            f"The {noun} is not awaiting review",
            f"Only a draft in awaiting_review can be reviewed. Poll the {noun} for its status.",
        ),
        "separation-of-duties": (
            403,
            "separation-of-duties",
            "The submitter cannot review their own job",
            "A different authorized reviewer must make this decision.",
        ),
        "revision-limit": (
            409,
            "revision-limit",
            "The revision limit is reached",
            f"After {limit} revisions the {noun} can only be approved or rejected.",
        ),
        "key-reused": (
            422,
            "idempotency-key-reused",
            "The Idempotency-Key was used with a different body",
            "Use a new key for a different request.",
        ),
    }


def demo_ready(app: App, settings: Settings, input_id: str) -> bool:
    """Demo mode replays recordings, so it offers only inputs that have them."""
    return settings.mode != "demo" or app.has_recordings(settings, input_id)


def _hash(body: BaseModel) -> str:
    return hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()


def job_routes(
    app: App, settings: Settings, store: JobStore, prefix: str, tags: list[str | Enum]
) -> APIRouter:
    """The job routes for one app under `prefix` (/v1/apps/{app_id}/jobs, or a legacy route)."""
    router = APIRouter(prefix=prefix)
    noun = app.noun
    refused = review_problems(app)
    RequestModel = app.request_model
    JobModel = app.job_model
    EvidenceModel = app.evidence_model

    async def visible_job(job_id: str, principal: Principal) -> JobRecord:
        job = await store.get(job_id) if len(job_id) <= 64 else None
        # Submitters see only their own jobs; others' jobs, and other apps' jobs, look the
        # same as missing ones.
        if (
            job is None
            or job.app_id != app.app_id
            or (principal.role == "submitter" and job.submitter != principal.name)
        ):
            raise Problem(404, "not-found", f"No such {noun} job")
        return job

    @router.post(
        "",
        tags=tags,
        status_code=202,
        response_model=JobAccepted,
        responses={
            202: {"description": "Job accepted"},
            **problems(400, 401, 403, 413, 422, 429),
        },
        openapi_extra=IDEMPOTENCY_HEADER,
    )
    async def submit(
        body: RequestModel,  # type: ignore[valid-type]
        response: Response,
        principal: Annotated[Principal, Depends(require("submitter"))],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> JobAccepted:
        input_id: str = body.package_id  # type: ignore[attr-defined]
        try:
            app.load_input(settings, input_id)
        except UnknownInput:
            raise Problem(
                422,
                "unknown-package",
                "The package is not in the catalog",
                f"No package with id {input_id}.",
            ) from None
        if not demo_ready(app, settings, input_id):
            raise Problem(
                422,
                "package-not-recorded",
                "The package has no recorded run for demo mode",
                f"Demo mode replays recorded model responses, and {input_id} has none. "
                "Run in local mode with model keys, or choose a package from /v1/packages.",
            )
        result = await store.submit(
            principal.name,
            idempotency_key,
            _hash(body),
            input_id,
            settings.daily_job_cap,
            app.app_id,
        )
        if result.cap_reached:
            now = datetime.now(UTC)
            reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            raise Problem(
                429,
                "daily-cap-reached",
                "Today's job limit is reached",
                f"This environment accepts {settings.daily_job_cap} new jobs a day. "
                "The limit resets at midnight UTC.",
                headers={"Retry-After": str(int((reset - now).total_seconds()) + 1)},
            )
        if result.key_conflict or result.job is None:
            raise Problem(*refused["key-reused"])
        location = f"{prefix}/{result.job.job_id}"
        response.headers["Location"] = location
        if result.replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return JobAccepted(job_id=result.job.job_id, status="queued", links=Links(self=location))

    @router.get(
        "",
        tags=tags,
        response_model=MemoList,
        summary=f"List {noun} jobs, most recently updated first",
        description="Submitters see their own jobs; reviewers and auditors see all jobs.",
        responses=problems(401, 422),
    )
    async def list_jobs(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        status: Annotated[list[JobStatus] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> MemoList:
        jobs = await store.list_jobs(
            principal.name if principal.role == "submitter" else None,
            tuple(status) if status else None,
            limit,
            app.app_id,
        )
        return MemoList(items=[to_summary(j) for j in jobs])

    @router.get("/{job_id}", response_model=JobModel, tags=tags, responses=problems(401, 404))
    async def get_job(
        job_id: str,
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> Any:
        return app.to_public(await visible_job(job_id, principal))

    @router.get(
        "/{job_id}/source",
        tags=tags,
        response_model=SourceExcerpt,
        summary="The source text behind one citation",
        description=app.source_description,
        responses=problems(401, 404, 422),
    )
    async def get_source(
        job_id: str,
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        doc_id: Annotated[str, Query(max_length=128)],
        locator: Annotated[str, Query(max_length=128)],
    ) -> SourceExcerpt:
        job = await visible_job(job_id, principal)
        try:
            inp = app.load_input(settings, job.package_id)
        except UnknownInput:
            inp = None
        excerpt = source_excerpt(inp, job.result or {}, doc_id, locator) if inp else None
        if excerpt is None:
            raise Problem(
                404,
                "source-not-found",
                f"No such source in this {noun}",
                "The document or locator is not part of this job's package or retrieved passages.",
            )
        return excerpt

    @router.post(
        "/{job_id}/review",
        tags=tags,
        status_code=202,
        response_model=ReviewAccepted,
        summary="Approve, reject, or request revision (reviewer role only)",
        responses={
            202: {"description": "Decision accepted; the job resumes"},
            **problems(400, 401, 403, 404, 409, 413, 422),
        },
        openapi_extra=IDEMPOTENCY_HEADER,
    )
    async def review(
        job_id: str,
        body: ReviewRequest,
        response: Response,
        principal: Annotated[Principal, Depends(require("reviewer"))],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> ReviewAccepted:
        if body.decision == "revise" and not body.comments.strip():
            raise Problem(
                422,
                "comments-required",
                "A revision needs comments",
                "Say what should change in the draft.",
            )
        job = await store.get(job_id) if len(job_id) <= 64 else None
        if job is None or job.app_id != app.app_id:
            raise Problem(*refused["not-found"])
        result = await store.submit_review(
            job_id,
            principal.name,
            idempotency_key,
            _hash(body),
            body.decision,
            body.comments,
            app.max_revisions,
        )
        if result.conflict is not None:
            raise Problem(*refused[result.conflict])
        assert result.job is not None and result.review is not None
        location = f"{prefix}/{job_id}"
        response.headers["Location"] = location
        if result.replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return ReviewAccepted(
            job_id=job_id,
            status=result.job.status,
            round=result.review.round,
            links=Links(self=location),
        )

    @router.get(
        "/{job_id}/evidence",
        response_model=EvidenceModel,
        tags=["audit"],
        summary="Evidence bundle for audit (auditor role only)",
        responses=problems(401, 403, 404),
    )
    async def get_evidence(
        job_id: str,
        principal: Annotated[Principal, Depends(require("auditor"))],
    ) -> Any:
        job = await store.get(job_id) if len(job_id) <= 64 else None
        if job is None or job.app_id != app.app_id:
            raise Problem(404, "not-found", f"No such {noun} job")
        events = await store.events(job_id)
        return app.to_evidence(job, events, verify_chain(events))

    return router
