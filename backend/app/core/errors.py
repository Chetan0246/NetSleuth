"""Structured error types and FastAPI exception handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class NetSleuthError(Exception):
    """Base class for all application errors carrying an HTTP status."""

    status_code = 400
    code = "netsleuth_error"

    def __init__(self, detail: str, *, field: str | None = None, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.field = field
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "detail": self.detail}}
        if self.field:
            body["error"]["field"] = self.field
        if self.extra:
            body["error"]["context"] = self.extra
        return body


class NotFoundError(NetSleuthError):
    status_code = 404
    code = "not_found"


class ValidationError(NetSleuthError):
    status_code = 422
    code = "validation_error"


class ConflictError(NetSleuthError):
    status_code = 409
    code = "conflict"


class NotImplementedForModeError(NetSleuthError):
    status_code = 400
    code = "unsupported_mode"


def _error_response(status_code: int, code: str, detail: str, field: str | None = None,
                    context: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "detail": detail}}
    if field:
        body["error"]["field"] = field
    if context:
        body["error"]["context"] = context
    return JSONResponse(status_code=status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NetSleuthError)
    async def _netsleuth_error(_: Request, exc: NetSleuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        return _error_response(
            422,
            "request_validation_error",
            first.get("msg", "Request validation failed"),
            field=loc or None,
            context={"errors": [
                {"loc": ".".join(str(p) for p in e.get("loc", [])), "msg": e.get("msg", "")}
                for e in exc.errors()[:10]
            ]},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        # Never leak stack traces or internal paths to the browser.
        return _error_response(
            500, "internal_error",
            "An unexpected internal error occurred. See the server log for details.",
            context={"exception_type": type(exc).__name__},
        )
