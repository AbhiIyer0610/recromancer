"""Composition root: the only module that wires adapters to ports.

Start the API with ``uvicorn recromancer.bootstrap:app_factory --factory``. uvicorn
installs its own logging config when its ``Config`` is created and calls the factory
later, in ``Config.load``, so ``configure_logging`` (called by ``build_app``) runs last
and routes the uvicorn loggers through the JSON handler.
"""

import functools
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Protocol

import structlog
from fastapi import FastAPI
from psycopg_pool import PoolTimeout

from recromancer.api.app import Lifespan, create_app
from recromancer.config import Settings, load_dotenv
from recromancer.db.evidence_store import EMBEDDING_DIMENSIONS, PostgresEvidenceStore
from recromancer.db.postgres import (
    PostgresDiagnostics,
    PostgresReadinessProbe,
    build_pool,
)
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.ingest.bundle import read_bundle
from recromancer.ingest.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_item,
)
from recromancer.observability import configure_logging
from recromancer.service.ingestion import IngestionService
from recromancer.service.ports import (
    Clock,
    DbDiagnostics,
    Embedder,
    EvidenceStore,
    ReadinessProbe,
)

__all__ = [
    "SystemClock",
    "app_factory",
    "build_app",
    "build_db_diagnostics",
    "build_ingestion_service",
    "evidence_store",
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


@contextmanager
def evidence_store(settings: Settings) -> Iterator[EvidenceStore]:
    """Open a pool, yield the Postgres evidence store and always close the pool."""
    if not settings.database_url.get_secret_value():
        raise RecromancerError(ErrorCode.CONFIG_INVALID, "database url is not set")
    pool = build_pool(settings.database_url)
    try:
        try:
            pool.open(wait=True, timeout=10)
        except PoolTimeout:
            raise RecromancerError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "database unavailable"
            ) from None
        store: EvidenceStore = PostgresEvidenceStore(pool)
        yield store
    finally:
        pool.close()


def build_ingestion_service(
    settings: Settings,
    *,
    store: EvidenceStore,
    embedder: Embedder,
    clock: Clock,
) -> IngestionService:
    """Wire the ingestion service; reject an embedder that does not fit the column."""
    if embedder.dimensions != EMBEDDING_DIMENSIONS:
        raise RecromancerError(
            ErrorCode.CONFIG_INVALID,
            f"embedder dimensions {embedder.dimensions} != column dimensions "
            f"{EMBEDDING_DIMENSIONS}",
        )
    return IngestionService(
        read=read_bundle,
        chunk=functools.partial(
            chunk_item, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP
        ),
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        embedder=embedder,
        store=store,
        clock=clock,
    )
