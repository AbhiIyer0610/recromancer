"""Composition root: the only module that wires adapters to ports.

Start the API with ``uvicorn recromancer.bootstrap:app_factory --factory``. uvicorn
installs its own logging config when its ``Config`` is created and calls the factory
later, in ``Config.load``, so ``configure_logging`` (called by ``build_app``) runs last
and routes the uvicorn loggers through the JSON handler.
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol

import structlog
from fastapi import FastAPI

from recromancer.api.app import Lifespan, create_app
from recromancer.config import Settings, load_dotenv
from recromancer.db.postgres import (
    PostgresDiagnostics,
    PostgresReadinessProbe,
    build_pool,
)
from recromancer.observability import configure_logging
from recromancer.service.ports import Clock, DbDiagnostics, ReadinessProbe

__all__ = [
    "SystemClock",
    "app_factory",
    "build_app",
    "build_db_diagnostics",
    "pool_lifespan",
    "uuid4_str",
]


class SystemClock:
    """Wall-clock and monotonic time from the operating system."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.perf_counter()


def uuid4_str() -> str:
    return str(uuid.uuid4())


class _Pool(Protocol):
    def open(self, *, wait: bool) -> None: ...

    def close(self) -> None: ...


def pool_lifespan(pool: _Pool) -> Lifespan:
    """Open the pool without waiting on startup and always close it on shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool.open(wait=False)
        try:
            yield
        finally:
            pool.close()

    return lifespan


def build_app(settings: Settings, *, clock: Clock | None = None) -> FastAPI:
    """Build the app. Never opens the pool or touches the network."""
    clock = clock or SystemClock()
    configure_logging(
        level=settings.log_level,
        clock=clock,
        secret_values=settings.secret_values(),
    )
    pool = build_pool(settings.database_url)
    probe: ReadinessProbe = PostgresReadinessProbe(pool)
    app = create_app(
        readiness=probe,
        clock=clock,
        new_id=uuid4_str,
        lifespan=pool_lifespan(pool),
        demo_mode=settings.demo_mode,
        production=settings.production,
    )
    structlog.get_logger(__name__).info(
        "starting", settings=settings.log_safe_summary()
    )
    return app


def app_factory() -> FastAPI:
    """Load ``.env`` (development only), read settings and build the app."""
    load_dotenv()
    return build_app(Settings.from_env())


def build_db_diagnostics(settings: Settings) -> DbDiagnostics:
    return PostgresDiagnostics(settings.database_url)
