"""The /v1 API.

uv run python -m axiom.api      # serves on 127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from axiom import __version__
from axiom.api.auth import Principal, require
from axiom.api.dashboard import Dashboard, build_dashboard
from axiom.api.errors import install_error_handlers, problem_response, problems
from axiom.api.jobs import (
    demo_ready,
    job_routes,
)
from axiom.api.schemas import AppOut, Health, Me, Readiness
from axiom.core.registry import enabled_apps
from axiom.jobs.store import InMemoryJobStore, JobStore, PostgresJobStore
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

    apps = enabled_apps()

    @app.get(
        "/v1/apps",
        response_model=list[AppOut],
        tags=["account"],
        summary="The apps this deployment runs, and what each calls the three roles",
        responses=problems(401),
    )
    async def list_apps(
        principal: Annotated[Principal, Depends(require("submitter", "reviewer", "auditor"))],
    ) -> list[AppOut]:
        return [AppOut(app_id=a.app_id, title=a.title, roles=dict(a.roles)) for a in apps.values()]

    build_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        """The OpenAPI document lists the input ids each app accepts, so a client (and the
        /docs page) sees the catalog rather than just the id pattern."""
        if app.openapi_schema:
            return app.openapi_schema
        spec = build_openapi()
        for a in apps.values():
            ids = [
                p["package_id"]
                for p in a.inputs(settings)
                if demo_ready(a, settings, p["package_id"])
            ]
            body = spec["components"]["schemas"][a.request_model.__name__]
            body["properties"]["package_id"]["enum"] = ids
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

    for a in apps.values():
        # Each app's routes, then its route from before ADR 0016, then its other routes.
        app.include_router(job_routes(a, settings, store, f"/v1/apps/{a.app_id}/jobs", [a.title]))
        if a.legacy_route:
            app.include_router(job_routes(a, settings, store, a.legacy_route, [f"{a.noun}s"]))
        extra = a.extra_routes(settings)
        if extra is not None:
            app.include_router(extra)

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
