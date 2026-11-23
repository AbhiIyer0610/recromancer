"""One MigrationStore contract suite run against the fake and the Postgres store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import Migration, MigrationStore, StoreError
from tests.fakes import FAIL_MARKER, FakeMigrationStore
from tests.pg_support import try_advisory_lock

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

StoreFactory = Callable[[], MigrationStore]


@pytest.fixture(params=["fake", "postgres"])
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def make_store(kind: str, request: pytest.FixtureRequest) -> StoreFactory:
    if kind == "fake":
        store = FakeMigrationStore()
        return lambda: store
    url: str = request.getfixturevalue("postgres_url")
    schema: str = request.getfixturevalue("pg_schema")
    return lambda: PostgresMigrationStore(url, schema=schema)


@pytest.fixture
def failing_sql(kind: str) -> str:
    return FAIL_MARKER if kind == "fake" else "SELECT 1/0;"


def _migration(name: str, sql: str, checksum: str) -> Migration:
    return Migration(name, sql, checksum)


def test_ensure_table_is_idempotent(make_store: StoreFactory) -> None:
    store = make_store()
    with store.locked():
        store.ensure_table()
        store.ensure_table()
        assert dict(store.applied()) == {}


def test_applied_reflects_apply(make_store: StoreFactory) -> None:
    store = make_store()
    with store.locked():
        store.ensure_table()
        store.apply(_migration("0001_a.sql", "SELECT 1;", "c1"), NOW)
        store.apply(_migration("0002_b.sql", "SELECT 2;", "c2"), NOW)
        assert dict(store.applied()) == {"0001_a.sql": "c1", "0002_b.sql": "c2"}


def test_failing_sql_records_nothing(
    make_store: StoreFactory, failing_sql: str
) -> None:
    store = make_store()
    with store.locked():
        store.ensure_table()
        with pytest.raises(StoreError):
            store.apply(_migration("0001_bad.sql", failing_sql, "c1"), NOW)
        assert dict(store.applied()) == {}


def test_lock_is_exclusive(
    kind: str, make_store: StoreFactory, request: pytest.FixtureRequest
) -> None:
    store = make_store()
    if kind == "fake":
        with store.locked(), pytest.raises(RuntimeError), store.locked():
            pass
        return
    url: str = request.getfixturevalue("postgres_url")
    with store.locked():
        assert try_advisory_lock(url) is False
    assert try_advisory_lock(url) is True
