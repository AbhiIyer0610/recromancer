"""Application factory. Takes every dependency as an argument and reads no config."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI

from recromancer.api.errors import ErrorBody, register_exception_handlers
from recromancer.api.middleware import RequestContextMiddleware
from recromancer.api.routes.health import make_health_router
from recromancer.service.ports import Clock, IdGenerator, ReadinessProbe

APP_TITLE = "Recromancer"
APP_VERSION = "0.1.0"

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Not found"},
    422: {"model": ErrorBody, "description": "Validation error"},
    500: {"model": ErrorBody, "description": "Internal error"},
}


def create_app(
    *,
    readiness: ReadinessProbe,
    clock: Clock,
    new_id: IdGenerator,
    lifespan: Lifespan | None = None,
    demo_mode: bool = True,
    production: bool = False,
    readyz_timeout_s: float = 5.0,
) -> FastAPI:
    """Build the FastAPI app.

    Stack, outermost first: ServerErrorMiddleware (catch-all handler),
    RequestContextMiddleware, ExceptionMiddleware, router. In production the
    interactive docs and the OpenAPI document are not routed, so they answer
    with the standard 404 body.
    """
    if not readyz_timeout_s > 0:
        raise ValueError("readyz_timeout_s must be greater than zero")

    if production:
        app = FastAPI(
            title=APP_TITLE,
            version=APP_VERSION,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            lifespan=lifespan,
        )
    else:
        app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)

    app.state.demo_mode = demo_mode
    app.state.production = production

    register_exception_handlers(app, new_id)
    app.add_middleware(RequestContextMiddleware, clock=clock, new_id=new_id)
    app.include_router(
        make_health_router(readiness, timeout_s=readyz_timeout_s),
        responses=_ERROR_RESPONSES,
    )
    return app
