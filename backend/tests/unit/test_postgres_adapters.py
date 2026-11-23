"""Postgres adapter behaviour that needs no database."""

from typing import Any, cast

import psycopg
import psycopg.errors
import psycopg_pool
import pytest

from recromancer.db.postgres import (
    PostgresDiagnostics,
    PostgresMigrationStore,
    PostgresReadinessProbe,
)
from recromancer.db.runner import MigrationStore, StoreError
from recromancer.service.ports import DbDiagnostics, ReadinessProbe

_LEAKY = (
    'connection to server at "h" failed: password authentication failed for user "u" '
    "password=SECRETPW"
)


def _leaks(message: str) -> bool:
    return any(part in message for part in ("SECRETPW", "password=", '"h"'))


@pytest.mark.parametrize("schema", ["public", "_x", "a1_b2", "a" * 63])
def test_valid_schema_names_are_accepted(schema: str) -> None:
    PostgresMigrationStore("postgresql://x", schema=schema)


@pytest.mark.parametrize("schema", ["Public", "1x", "a-b", "x;drop", "a" * 64, ""])
def test_invalid_schema_names_are_rejected(schema: str) -> None:
    with pytest.raises(
        ValueError, match=r"^schema must match \[a-z_\]\[a-z0-9_\]\{0,62\}$"
    ):
        PostgresMigrationStore("postgresql://x", schema=schema)


def test_adapters_satisfy_their_ports() -> None:
    store: MigrationStore = PostgresMigrationStore("postgresql://x")
    diagnostics: DbDiagnostics = PostgresDiagnostics("postgresql://x")
    probe: ReadinessProbe = PostgresReadinessProbe(
        cast(psycopg_pool.ConnectionPool, object())
    )
    assert all(item is not None for item in (store, diagnostics, probe))


def _failing_connect(_conninfo: str) -> Any:
    raise psycopg.OperationalError(_LEAKY)


def test_connect_failure_becomes_a_clean_store_error() -> None:
    store = PostgresMigrationStore("postgresql://x", connect=_failing_connect)
    with pytest.raises(StoreError) as caught, store.locked():
        pass
    assert not _leaks(str(caught.value))
    assert "OperationalError" in str(caught.value)
    assert caught.value.__cause__ is None


def test_diagnostics_ping_failure_is_clean() -> None:
    diagnostics = PostgresDiagnostics("postgresql://x", connect=_failing_connect)
    with pytest.raises(StoreError) as caught:
        diagnostics.ping()
    assert not _leaks(str(caught.value))
    assert caught.value.__cause__ is None


class _BrokenConnection:
    def execute(self, query: object, params: object = None) -> Any:
        if "search_path" in str(query):
            return None
        raise psycopg.errors.UndefinedTable(
            'relation "_schema_migrations" does not exist'
        )

    def close(self) -> None:
        pass


def test_table_errors_become_clean_store_errors() -> None:
    def connect(_conninfo: str) -> "psycopg.Connection[Any]":
        return cast("psycopg.Connection[Any]", _BrokenConnection())

    store = PostgresMigrationStore("postgresql://x", connect=connect)
    for call in (store.ensure_table, store.applied):
        with pytest.raises(StoreError) as caught:
            call()
        assert "UndefinedTable" in str(caught.value)
        assert "42P01" in str(caught.value)
        assert caught.value.__cause__ is None


class _TimeoutPool:
    def connection(self, timeout: float) -> Any:
        raise psycopg_pool.PoolTimeout("couldn't get a connection")


@pytest.mark.anyio
async def test_readiness_probe_returns_false_on_pool_timeout() -> None:
    probe = PostgresReadinessProbe(cast(psycopg_pool.ConnectionPool, _TimeoutPool()))
    assert await probe.is_ready() is False
