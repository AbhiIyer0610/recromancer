"""Error responses: the only ErrorCode -> HTTP status map and the exception handlers."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog.contextvars import bound_contextvars

from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.service.ports import IdGenerator

REQUEST_ID_HEADER = "X-Request-Id"

INTERNAL_ERROR_MESSAGE = "An internal error occurred"
VALIDATION_MESSAGE = "Request validation failed"
NOT_FOUND_MESSAGE = "Not found"
METHOD_NOT_ALLOWED_MESSAGE = "Method not allowed"

MAX_MESSAGE_LENGTH = RecromancerError.MAX_MESSAGE_LENGTH
MAX_LOCATIONS = 10
MAX_LOCATION_LENGTH = 100


class ErrorBody(BaseModel):
    """Body of every error response."""

    code: str
    message: str
    request_id: str


ERROR_HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.VALIDATION_ERROR: 422,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.METHOD_NOT_ALLOWED: 405,
        ErrorCode.CONFIG_INVALID: 500,
        ErrorCode.MIGRATION_FAILED: 500,
        ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
        ErrorCode.INTERNAL_ERROR: 500,
    }
)


def http_status_for(code: ErrorCode) -> int:
    return ERROR_HTTP_STATUS[code]


def request_id_for(request: Request, new_id: IdGenerator) -> str:
    """Return the id the middleware stored in the scope, or a fresh one."""
    stored = getattr(request.state, "request_id", None)
    if isinstance(stored, str) and stored:
        return stored
    return new_id()


def validation_locations(exc: RequestValidationError) -> list[str]:
    """Field locations only. Never reads input, ctx, msg or the exception text."""
    found: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        parts = [
            str(p) for p in loc if isinstance(p, str | int) and not isinstance(p, bool)
        ]
        if not parts:
            continue
        text = ".".join(parts)[:MAX_LOCATION_LENGTH]
        if text not in found:
            found.append(text)
        if len(found) == MAX_LOCATIONS:
            break
    return found


def validation_message(exc: RequestValidationError) -> str:
    locations = validation_locations(exc)
    if not locations:
        return VALIDATION_MESSAGE
    return f"{VALIDATION_MESSAGE}: {', '.join(locations)}"[:MAX_MESSAGE_LENGTH]


def _error_response(
    status: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(code=code.value, message=message, request_id=request_id)
    return JSONResponse(
        body.model_dump(), status_code=status, headers=dict(headers or {})
    )


def register_exception_handlers(app: FastAPI, new_id: IdGenerator) -> None:
    """Install every error handler on ``app``."""
    logger = structlog.get_logger("recromancer.api.errors")

    async def handle_recromancer(request: Request, exc: Exception) -> JSONResponse:
        error = cast(RecromancerError, exc)
        return _error_response(
            http_status_for(error.code),
            error.code,
            error.message,
            request_id_for(request, new_id),
        )

    async def handle_validation(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            http_status_for(ErrorCode.VALIDATION_ERROR),
            ErrorCode.VALIDATION_ERROR,
            validation_message(cast(RequestValidationError, exc)),
            request_id_for(request, new_id),
        )

    async def handle_http(request: Request, exc: Exception) -> JSONResponse:
        http_exc = cast(StarletteHTTPException, exc)
        request_id = request_id_for(request, new_id)
        status = http_exc.status_code
        if status == 404:
            return _error_response(
                status, ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE, request_id
            )
        if status == 405:
            allow = {
                k: v
                for k, v in (http_exc.headers or {}).items()
                if k.lower() == "allow"
            }
            return _error_response(
                status,
                ErrorCode.METHOD_NOT_ALLOWED,
                METHOD_NOT_ALLOWED_MESSAGE,
                request_id,
                allow,
            )
        return _error_response(
            status, ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE, request_id
        )

    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Runs in ServerErrorMiddleware, outside RequestContextMiddleware: the
        # contextvars are gone and the middleware no longer wraps send.
        request_id = request_id_for(request, new_id)
        method = request.method
        path = request.url.path
        with bound_contextvars(request_id=request_id, method=method, path=path):
            logger.error(
                "unhandled_exception",
                exc_info=exc,
                request_id=request_id,
                method=method,
                path=path,
                exception_class=type(exc).__name__,
            )
        return _error_response(
            http_status_for(ErrorCode.INTERNAL_ERROR),
            ErrorCode.INTERNAL_ERROR,
            INTERNAL_ERROR_MESSAGE,
            request_id,
            {REQUEST_ID_HEADER: request_id},
        )

    app.add_exception_handler(RecromancerError, handle_recromancer)
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(StarletteHTTPException, handle_http)
    app.add_exception_handler(Exception, handle_unexpected)
