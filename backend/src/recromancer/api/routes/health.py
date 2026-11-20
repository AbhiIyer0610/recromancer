"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Literal

import anyio
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from recromancer.service.ports import ReadinessProbe

_LOGGER_NAME = "recromancer.api.health"


class LivenessBody(BaseModel):
    """Body of ``GET /healthz``."""

    status: Literal["ok"]


class ReadyBody(BaseModel):
    """Body of a successful ``GET /readyz``."""

    status: Literal["ok"]
    db: Literal["ok"]


class DegradedBody(BaseModel):
    """Body of a failed ``GET /readyz``."""

    status: Literal["degraded"]
    db: Literal["unreachable"]


_READY = {"status": "ok", "db": "ok"}
_DEGRADED = {"status": "degraded", "db": "unreachable"}


def make_health_router(readiness: ReadinessProbe, *, timeout_s: float) -> APIRouter:
    """Build the health router around an injected readiness probe."""
    if not timeout_s > 0:
        raise ValueError("timeout_s must be greater than zero")

    router = APIRouter()

    @router.get("/healthz", response_model=LivenessBody, include_in_schema=True)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get(
        "/readyz",
        response_model=ReadyBody,
        responses={503: {"model": DegradedBody, "description": "Database unreachable"}},
        include_in_schema=True,
    )
    async def readyz() -> JSONResponse:
        ready: bool | None = None
        with anyio.move_on_after(timeout_s):
            try:
                ready = await readiness.is_ready()
            except Exception as exc:
                # Class name only: driver messages can contain connection details.
                structlog.get_logger(_LOGGER_NAME).warning(
                    "readiness_probe_failed", exception_class=type(exc).__name__
                )
                ready = False
        if ready is None:
            # Our own timeout fired. anyio re-raises an outer cancellation itself.
            return JSONResponse(_DEGRADED, status_code=503)
        if ready:
            return JSONResponse(_READY, status_code=200)
        return JSONResponse(_DEGRADED, status_code=503)

    return router
