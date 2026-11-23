"""Helpers for tests that talk to a real Postgres (never reads the environment)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql

from recromancer.db.postgres import MIGRATION_LOCK_KEY

CONNECT_TIMEOUT_SECONDS = 5
_SCHEMA_NAME = re.compile(r"t_[0-9a-f]{32}")


def connect(url: str) -> psycopg.Connection[Any]:
    return psycopg.Connection.connect(
        url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )


@contextmanager
def throwaway_schema(url: str) -> Iterator[str]:
    """Create ``t_<hex>``; drop it with CASCADE afterwards, even on failure."""
    name = f"t_{uuid.uuid4().hex}"
    if _SCHEMA_NAME.fullmatch(name) is None:
        raise ValueError("unexpected schema name")
    with connect(url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with connect(url) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
            )


def try_advisory_lock(url: str) -> bool:
    """Try the migration lock from a second session; release it when obtained."""
    with connect(url) as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
        ).fetchone()
        got = bool(row and row[0])
        if got:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
        return got


def recorded_rows(url: str, schema: str) -> list[tuple[str, str]]:
    with connect(url) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        rows = conn.execute(
            "SELECT filename, checksum FROM _schema_migrations ORDER BY filename"
        ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def table_names(url: str, schema: str) -> set[str]:
    with connect(url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        ).fetchall()
    return {str(r[0]) for r in rows}


def relation_exists(url: str, schema: str, table: str) -> bool:
    with connect(url) as conn:
        row = conn.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",)).fetchone()
    return bool(row and row[0] is not None)
