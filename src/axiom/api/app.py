"""The /v1 API.

uv run python -m axiom.api      # serves on 127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from axiom import __version__
from axiom.api.auth import Principal, require
from axiom.api.dashboard import Dashboard, build_dashboard
from axiom.api.errors import Problem, install_error_handlers, problem_response, problems
from axiom.api.explain import PolicyClause, SourceExcerpt, policy_clause, source_excerpt
from axiom.api.schemas import (
    EvidenceBundle,
    Health,
    JobAccepted,
    Links,
    Me,
    MemoJob,
    MemoList,
    MemoRequest,
    PackageOut,
    Readiness,
    ReviewAccepted,
    ReviewRequest,
    to_evidence,
    to_public,
    to_summary,
)
from axiom.apps.credit_memo.catalog import (
    UnknownPackage,
    has_recordings,
    list_packages,
    load_package,
)
from axiom.evidence.log import verify_chain
from axiom.jobs.store import InMemoryJobStore, JobRecord, JobStatus, JobStore, PostgresJobStore
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


def _query_names(dependant: Dependant) -> set[str]:
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _query_names(sub)
    return names


def reject_unknown_query(request: Request) -> None:
    """Refuse query parameters an operation does not declare, so a typo such as ?stauts=
    is an error rather than silently ignored."""
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return
    known = _query_names(route.dependant)
    unknown = sorted(set(request.query_params) - known)
    if unknown:
        raise RequestValidationError(
            [
                {"loc": ("query", name), "msg": "Unknown query parameter", "type": "extra"}
                for name in unknown
            ]
        )


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
        dependencies=[Depends(reject_unknown_query)],
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

    @app.get(
        "/v1/me",
        response_model=Me,
        tags=["account"],
        summary="Who this key belongs to",
        responses=problems(401),
    )
    async def me(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> Me:
        return Me(name=principal.name, role=principal.role)

    @app.get(
        "/v1/packages",
        response_model=list[PackageOut],
        tags=["memos"],
        summary="The synthetic borrower packages a memo can be requested for",
        responses=problems(401),
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

    build_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        """The OpenAPI document lists the package ids this deployment accepts, so a client
        (and the /docs page) sees the catalog rather than just the id pattern."""
        if app.openapi_schema:
            return app.openapi_schema
        spec = build_openapi()
        ids = [
            p["package_id"]
            for p in list_packages(settings.packages_dir)
            if demo_ready(p["package_id"])
        ]
        spec["components"]["schemas"]["MemoRequest"]["properties"]["package_id"]["enum"] = ids
        return spec

    app.openapi = openapi  # type: ignore[method-assign]

    @app.get("/v1/health", response_model=Health, tags=["operations"], summary="The API is up")
    async def health() -> Health:
        return Health(status="ok")

    @app.get(
        "/v1/ready",
        response_model=Readiness,
        tags=["operations"],
        summary="The API can reach its database",
        responses={503: {"model": Readiness, "description": "The database is not reachable"}},
    )
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
        responses={
            202: {"description": "Job accepted"},
            **problems(400, 401, 403, 413, 422, 429),
        },
        openapi_extra=IDEMPOTENCY_HEADER,
    )
    async def submit_memo(
        body: MemoRequest,
        response: Response,
        principal: Annotated[Principal, Depends(require("submitter"))],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> JobAccepted:
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
        result = await store.submit(
            principal.name, idempotency_key, request_hash, body.package_id, settings.daily_job_cap
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
        responses=problems(401, 422),
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
        responses=problems(401),
    )
    async def dashboard(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> Dashboard:
        return await build_dashboard(store, principal)

    async def visible_job(job_id: str, principal: Principal) -> JobRecord:
        job = await store.get(job_id) if len(job_id) <= 64 else None
        # Submitters see only their own jobs; others' jobs look the same as missing ones.
        if job is None or (principal.role == "submitter" and job.submitter != principal.name):
            raise Problem(404, "not-found", "No such memo job")
        return job

    @app.get(
        "/v1/memos/{job_id}", response_model=MemoJob, tags=["memos"], responses=problems(401, 404)
    )
    async def get_memo(
        job_id: str,
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> MemoJob:
        return to_public(await visible_job(job_id, principal))

    @app.get(
        "/v1/memos/{job_id}/source",
        response_model=SourceExcerpt,
        tags=["memos"],
        summary="The source text behind one citation",
        description="Resolves a citation in this memo: a line of a borrower document, or a "
        "passage the job retrieved. Says whether the text matches the fingerprint recorded "
        "during the job. No model is called.",
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
            package = load_package(settings.packages_dir, job.package_id)
        except UnknownPackage:
            package = None
        excerpt = source_excerpt(package, job.result or {}, doc_id, locator) if package else None
        if excerpt is None:
            raise Problem(
                404,
                "source-not-found",
                "No such source in this memo",
                "The document or locator is not part of this job's package or retrieved passages.",
            )
        return excerpt

    @app.get(
        "/v1/policy/{ref}",
        response_model=PolicyClause,
        tags=["memos"],
        summary="A credit policy clause or section, by reference (CP-4.1, CP-3)",
        responses=problems(401, 404),
    )
    async def get_policy(
        ref: str,
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> PolicyClause:
        clause = policy_clause(settings.reference_dirs["credit_policy"], ref)
        if clause is None:
            raise Problem(
                404,
                "policy-clause-not-found",
                "No such clause in the credit policy",
                "References outside the bank's credit policy, such as regulations, are cited "
                "by the passages the finding lists.",
            )
        return clause

    @app.post(
        "/v1/memos/{job_id}/review",
        status_code=202,
        response_model=ReviewAccepted,
        tags=["memos"],
        summary="Approve, reject, or request revision (reviewer role only)",
        responses={
            202: {"description": "Decision accepted; the job resumes"},
            **problems(400, 401, 403, 404, 409, 413, 422),
        },
        openapi_extra=IDEMPOTENCY_HEADER,
    )
    async def review_memo(
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
        responses=problems(401, 403, 404),
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

    uvicorn.run(
        app,
        host=os.environ.get("AXIOM_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("AXIOM_API_PORT", "8000")),
    )
