"""Readiness probe and diagnostics against a real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import resources

import pytest
from pydantic import SecretStr

from recromancer.db.postgres import (
    PostgresDiagnostics,
    PostgresMigrationStore,
    PostgresReadinessProbe,
    build_pool,
)
from recromancer.db.runner import discover, run


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, tzinfo=UTC)


@pytest.mark.anyio
async def test_probe_is_ready_against_real_database(postgres_url: str) -> None:
    pool = build_pool(SecretStr(postgres_url))
    pool.open(wait=True, timeout=10)
    try:
        assert await PostgresReadinessProbe(pool).is_ready() is True
    finally:
        pool.close()


@pytest.mark.anyio
async def test_probe_is_not_ready_on_unused_port(postgres_url: str) -> None:
    pool = build_pool(SecretStr("postgresql://u:p@127.0.0.1:1/db"))
    pool.open(wait=False)
    try:
        assert await PostgresReadinessProbe(pool).is_ready() is False
    finally:
        pool.close()


def test_diagnostics_ping_and_extension(postgres_url: str, pg_schema: str) -> None:
    migrations = discover(resources.files("recromancer.db") / "migrations")
    run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        migrations,
        _Clock(),
        lambda _: None,
    )
    diagnostics = PostgresDiagnostics(postgres_url)
    diagnostics.ping()
    assert diagnostics.has_extension("vector") is True
