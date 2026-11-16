"""RFC 9457 problem details. Each error class has a stable type URI documented in
docs/api-errors.md."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

TYPE_BASE = "https://github.com/minw0607/axiom_agent_platform/blob/main/docs/api-errors.md#"
MEDIA_TYPE = "application/problem+json"


class ValidationIssue(BaseModel):
    location: str
    message: str


class ProblemDetail(BaseModel):
    """An RFC 9457 problem. `type` links to its section of docs/api-errors.md."""

    type: str
    title: str
    status: int
    instance: str
    detail: str | None = None
    errors: list[ValidationIssue] | None = Field(
        default=None, description="invalid-request only: each problem with the request"
    )


PROBLEM_MEANINGS = {
    400: "An Idempotency-Key header is missing or too long",
    401: "The X-API-Key header is missing or not a valid key",
    403: "The key's role cannot perform this action",
    404: "Not found (another submitter's job looks the same as a missing one)",
    409: "The memo is not in a state that allows this decision",
    413: "The request body is over 64 KiB",
    422: "The request is invalid",
    429: "Today's job limit is reached (environments with a daily cap only)",
}


def problems(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The error responses an operation declares in the OpenAPI document."""
    return {
        status: {
            "description": PROBLEM_MEANINGS[status],
            "content": {MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}},
        }
        for status in statuses
    }


class Problem(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status, self.slug, self.title, self.detail = status, slug, title, detail
        self.headers = headers


def problem_response(
    status: int,
    slug: str,
    title: str,
    detail: str | None,
    instance: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": TYPE_BASE + slug,
        "title": title,
        "status": status,
        "instance": instance,
    }
    if detail:
        body["detail"] = detail
    body.update(extra or {})
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE, headers=headers)


_HTTP_SLUGS = {404: ("not-found", "Not found"), 405: ("method-not-allowed", "Method not allowed")}


def _publish_problem_schema(app: FastAPI) -> None:
    """Add ProblemDetail to the OpenAPI components, where problems() refers to it."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        spec = build()
        schema = ProblemDetail.model_json_schema(ref_template="#/components/schemas/{model}")
        components = spec.setdefault("components", {}).setdefault("schemas", {})
        components.update(schema.pop("$defs", {}))
        components["ProblemDetail"] = schema
        app.openapi_schema = spec
        return spec

    app.openapi = openapi  # type: ignore[method-assign]


def install_error_handlers(app: FastAPI) -> None:
    _publish_problem_schema(app)

    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem) -> JSONResponse:
        return problem_response(
            exc.status, exc.slug, exc.title, exc.detail, request.url.path, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return problem_response(
            422,
            "invalid-request",
            "The request is invalid",
            None,
            request.url.path,
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug, title = _HTTP_SLUGS.get(exc.status_code, ("http-error", "HTTP error"))
        headers = dict(exc.headers or {})
        if exc.status_code == 405:
            # Starlette lists only the first matching route's methods; RFC 9110 wants every
            # method the path accepts.
            headers["Allow"] = ", ".join(sorted(_allowed_methods(request)))
        return problem_response(exc.status_code, slug, title, None, request.url.path, headers)


def _allowed_methods(request: Request) -> set[str]:
    methods: set[str] = set()
    for route in request.app.router.routes:
        match, _ = route.matches({**request.scope, "method": "GET"})
        if match != Match.NONE:
            methods |= getattr(route, "methods", None) or set()
    return methods
