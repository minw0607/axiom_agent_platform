"""RFC 9457 problem details. Each error class has a stable type URI documented in
docs/api-errors.md."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

TYPE_BASE = "https://github.com/minw0607/axiom_agent_platform/blob/main/docs/api-errors.md#"
MEDIA_TYPE = "application/problem+json"


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


def install_error_handlers(app: FastAPI) -> None:
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
        return problem_response(exc.status_code, slug, title, None, request.url.path)
