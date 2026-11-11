"""The /v1 API.

uv run python -m axiom.api      # serves on 127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from axiom import __version__
from axiom.api.auth import Principal, require
from axiom.api.errors import Problem, install_error_handlers, problem_response
from axiom.api.schemas import JobAccepted, Links, MemoJob, MemoRequest, to_public
from axiom.catalog import UnknownPackage, load_package
from axiom.jobs.store import InMemoryJobStore, JobStore, PostgresJobStore
from axiom.jobs.worker import run_worker
from axiom.settings import Settings
from axiom.telemetry.setup import setup_telemetry

MAX_BODY_BYTES = 64 * 1024
MAX_IDEMPOTENCY_KEY = 128


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
        return await call_next(request)

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
