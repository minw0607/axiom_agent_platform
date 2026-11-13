"""The /v1 API.

uv run python -m axiom.api      # serves on 127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from axiom import __version__
from axiom.api.auth import Principal, require
from axiom.api.dashboard import Dashboard, build_dashboard
from axiom.api.errors import Problem, install_error_handlers, problem_response
from axiom.api.schemas import (
    EvidenceBundle,
    JobAccepted,
    Links,
    Me,
    MemoJob,
    MemoList,
    MemoRequest,
    PackageOut,
    ReviewAccepted,
    ReviewRequest,
    to_evidence,
    to_public,
    to_summary,
)
from axiom.catalog import UnknownPackage, has_recordings, list_packages, load_package
from axiom.evidence.log import verify_chain
from axiom.jobs.store import InMemoryJobStore, JobStatus, JobStore, PostgresJobStore
from axiom.jobs.worker import run_worker
from axiom.settings import Settings
from axiom.telemetry.setup import setup_telemetry

MAX_BODY_BYTES = 64 * 1024
REVIEW_PAGE = Path(__file__).parent / "review_page"
# The review page loads only its own files and talks only to this API. Memo text is
# rendered as text, never as HTML (threat model T-03, improper output handling).
REVIEW_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
MAX_IDEMPOTENCY_KEY = 128
# Status, slug, title and detail for each reason a review is refused.
REVIEW_PROBLEMS: dict[str, tuple[int, str, str, str]] = {
    "not-found": (404, "not-found", "No such memo job", ""),
    "not-awaiting-review": (
        409,
        "not-awaiting-review",
        "The memo is not awaiting review",
        "Only a draft in awaiting_review can be reviewed. Poll the memo for its status.",
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
        "After two revisions the memo can only be approved or rejected.",
    ),
    "key-reused": (
        422,
        "idempotency-key-reused",
        "The Idempotency-Key was used with a different body",
        "Use a new key for a different request.",
    ),
}


def create_app(settings: Settings | None = None, store: JobStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if store is None:
        store = (
            PostgresJobStore(settings.database_url) if settings.database_url else InMemoryJobStore()
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await store.init()
        stop = asyncio.Event()
        task = None
        if settings.inprocess_worker:
            task = asyncio.create_task(run_worker(store, settings, stop))
        yield
        stop.set()
        if task is not None:
            await task

    app = FastAPI(
        title="axiom_agent_platform API",
        version=__version__,
        description="Submit a synthetic borrower package and poll for a draft credit memo. "
        "A credit officer makes every decision.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    install_error_handlers(app)

    @app.middleware("http")
    async def limit_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return problem_response(
                413,
                "payload-too-large",
                "The request body is too large",
                f"The limit is {MAX_BODY_BYTES} bytes.",
                request.url.path,
            )
        response = await call_next(request)
        if request.url.path.startswith("/review"):
            response.headers.update(REVIEW_PAGE_HEADERS)
        return response

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/review")

    @app.get("/review", include_in_schema=False)
    async def review_page() -> FileResponse:
        return FileResponse(REVIEW_PAGE / "index.html", media_type="text/html")

    app.mount("/review/assets", StaticFiles(directory=REVIEW_PAGE), name="review-assets")

    @app.get("/v1/me", response_model=Me, tags=["account"], summary="Who this key belongs to")
    async def me(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> Me:
        return Me(name=principal.name, role=principal.role)

    @app.get(
        "/v1/packages",
        response_model=list[PackageOut],
        tags=["memos"],
        summary="The synthetic borrower packages a memo can be requested for",
    )
    async def packages(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> list[PackageOut]:
        return [
            PackageOut.model_validate(p)
            for p in list_packages(settings.packages_dir)
            if demo_ready(p["package_id"])
        ]

    def demo_ready(package_id: str) -> bool:
        """Demo mode replays recordings, so it offers only packages that have them."""
        return settings.mode != "demo" or has_recordings(settings.recordings_dir, package_id)

    @app.get("/v1/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/ready", tags=["operations"])
    async def ready() -> JSONResponse:
        ok = await store.ping()
        return JSONResponse(
            {"status": "ready" if ok else "not_ready"}, status_code=200 if ok else 503
        )

    @app.post(
        "/v1/memos",
        status_code=202,
        response_model=JobAccepted,
        tags=["memos"],
        responses={202: {"description": "Job accepted"}},
    )
    async def submit_memo(
        body: MemoRequest,
        response: Response,
        principal: Annotated[Principal, Depends(require("submitter"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobAccepted:
        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            raise Problem(
                400,
                "idempotency-key-required",
                "An Idempotency-Key header is required",
                f"Send a unique key of at most {MAX_IDEMPOTENCY_KEY} characters per request.",
            )
        try:
            load_package(settings.packages_dir, body.package_id)
        except UnknownPackage:
            raise Problem(
                422,
                "unknown-package",
                "The package is not in the catalog",
                f"No package with id {body.package_id}.",
            ) from None
        if not demo_ready(body.package_id):
            raise Problem(
                422,
                "package-not-recorded",
                "The package has no recorded run for demo mode",
                f"Demo mode replays recorded model responses, and {body.package_id} has none. "
                "Run in local mode with model keys, or choose a package from /v1/packages.",
            )
        request_hash = hashlib.sha256(
            json.dumps(body.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        result = await store.submit(principal.name, idempotency_key, request_hash, body.package_id)
        if result.key_conflict or result.job is None:
            raise Problem(
                422,
                "idempotency-key-reused",
                "The Idempotency-Key was used with a different body",
                "Use a new key for a different request.",
            )
        location = f"/v1/memos/{result.job.job_id}"
        response.headers["Location"] = location
        if result.replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return JobAccepted(job_id=result.job.job_id, status="queued", links=Links(self=location))

    @app.get(
        "/v1/memos",
        response_model=MemoList,
        tags=["memos"],
        summary="List memo jobs, most recently updated first",
        description="Submitters see their own jobs; reviewers and auditors see all jobs.",
    )
    async def list_memos(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
        status: Annotated[list[JobStatus] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> MemoList:
        jobs = await store.list_jobs(
            principal.name if principal.role == "submitter" else None,
            tuple(status) if status else None,
            limit,
        )
        return MemoList(items=[to_summary(j) for j in jobs])

    @app.get(
        "/v1/dashboard",
        response_model=Dashboard,
        tags=["memos"],
        summary="A summary for the signed-in role",
        description="Relationship managers see their own requests; credit officers the review "
        "queue, riskiest first; auditors integrity checks, failures and decisions.",
    )
    async def dashboard(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> Dashboard:
        return await build_dashboard(store, principal)

    @app.get("/v1/memos/{job_id}", response_model=MemoJob, tags=["memos"])
    async def get_memo(
        job_id: str,
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> MemoJob:
        job = await store.get(job_id) if len(job_id) <= 64 else None
        # Submitters see only their own jobs; others' jobs look the same as missing ones.
        if job is None or (principal.role == "submitter" and job.submitter != principal.name):
            raise Problem(404, "not-found", "No such memo job")
        return to_public(job)

    @app.post(
        "/v1/memos/{job_id}/review",
        status_code=202,
        response_model=ReviewAccepted,
        tags=["memos"],
        summary="Approve, reject, or request revision (reviewer role only)",
        responses={202: {"description": "Decision accepted; the job resumes"}},
    )
    async def review_memo(
        job_id: str,
        body: ReviewRequest,
        response: Response,
        principal: Annotated[Principal, Depends(require("reviewer"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ReviewAccepted:
        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            raise Problem(
                400,
                "idempotency-key-required",
                "An Idempotency-Key header is required",
                f"Send a unique key of at most {MAX_IDEMPOTENCY_KEY} characters per request.",
            )
        if body.decision == "revise" and not body.comments.strip():
            raise Problem(
                422,
                "comments-required",
                "A revision needs comments",
                "Say what should change in the draft.",
            )
        if len(job_id) > 64:
            raise Problem(404, "not-found", "No such memo job")
        request_hash = hashlib.sha256(
            json.dumps(body.model_dump(), sort_keys=True).encode()
        ).hexdigest()
        result = await store.submit_review(
            job_id, principal.name, idempotency_key, request_hash, body.decision, body.comments
        )
        if result.conflict is not None:
            raise Problem(*REVIEW_PROBLEMS[result.conflict])
        assert result.job is not None and result.review is not None
        location = f"/v1/memos/{job_id}"
        response.headers["Location"] = location
        if result.replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return ReviewAccepted(
            job_id=job_id,
            status=result.job.status,
            round=result.review.round,
            links=Links(self=location),
        )

    @app.get(
        "/v1/memos/{job_id}/evidence",
        response_model=EvidenceBundle,
        tags=["audit"],
        summary="Evidence bundle for audit (auditor role only)",
    )
    async def get_evidence(
        job_id: str,
        principal: Annotated[Principal, Depends(require("auditor"))],
    ) -> EvidenceBundle:
        job = await store.get(job_id) if len(job_id) <= 64 else None
        if job is None:
            raise Problem(404, "not-found", "No such memo job")
        events = await store.events(job_id)
        return to_evidence(job, events, verify_chain(events))

    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    setup_telemetry("axiom-api", settings.otel_endpoint)
    app = create_app(settings)
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/v1/health,/v1/ready")
    import os

    uvicorn.run(app, host=os.environ.get("AXIOM_API_HOST", "127.0.0.1"), port=8000)
