"""The advisory-lock key is a stable signed 64-bit integer; import has no effects."""

import hashlib
import importlib
from typing import Any

import psycopg
import psycopg_pool
import pytest

import recromancer.db.postgres as postgres


def _expected() -> int:
    digest = hashlib.sha256(b"recromancer.migrations").digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def test_key_fits_bigint_and_matches_the_formula() -> None:
    assert -(2**63) <= postgres.MIGRATION_LOCK_KEY <= 2**63 - 1
    assert postgres.MIGRATION_LOCK_KEY == _expected()


def test_key_is_deterministic_and_import_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("connection opened at import")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(psycopg.Connection, "connect", forbidden)
    monkeypatch.setattr(psycopg_pool.ConnectionPool, "open", forbidden)
    before = postgres.MIGRATION_LOCK_KEY
    importlib.reload(postgres)
    assert postgres.MIGRATION_LOCK_KEY == before
