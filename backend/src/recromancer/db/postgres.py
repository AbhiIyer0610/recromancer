"""Postgres adapters: migration store, readiness probe, diagnostics, pool builder.

The sync `psycopg_pool.ConnectionPool` plus `anyio.to_thread` is used because psycopg's
async API refuses the default Windows `ProactorEventLoop`. Driver errors can quote the
connection string, so they are never chained or stringified; `_safe_message` keeps only
the operation, the exception class name and the SQLSTATE.
"""

import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import anyio.to_thread
import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout, TooManyRequests
from pydantic import SecretStr

from recromancer.db.runner import Migration, StoreError

__all__ = [
    "MIGRATION_LOCK_KEY",
    "PostgresDiagnostics",
    "PostgresMigrationStore",
    "PostgresReadinessProbe",
    "build_pool",
]

MIGRATION_LOCK_KEY: int = int.from_bytes(
    hashlib.sha256(b"recromancer.migrations").digest()[:8], "big", signed=True
)

_CONNECT_TIMEOUT_SECONDS = 10
_PROBE_TIMEOUT_SECONDS = 4.0
_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_SCHEMA_ERROR = "schema must match [a-z_][a-z0-9_]{0,62}"
_DRIVER_ERRORS = (psycopg.Error, PoolClosed, TooManyRequests)

Connect = Callable[[str], "psycopg.Connection[Any]"]


def _secret(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _safe_message(op: str, exc: BaseException, *, detail: bool = False) -> str:
    """Describe a driver failure without the conninfo: class name and SQLSTATE only.

    With `detail=True` the first line of the server's primary message is appended. That
    text describes the SQL (for example a missing relation), never credentials, and is
    only present on server-reported errors.
    """
    message = f"{op} failed: {type(exc).__name__}"
    state = getattr(exc, "sqlstate", None)
    if isinstance(state, str) and state:
        message += f" (SQLSTATE {state})"
    if detail and isinstance(exc, psycopg.Error):
        primary = exc.diag.message_primary
        if primary:
            message += f": {primary.splitlines()[0]}"
    return message


def _default_connect(conninfo: str) -> "psycopg.Connection[Any]":
    return psycopg.Connection.connect(
        conninfo, autocommit=True, connect_timeout=_CONNECT_TIMEOUT_SECONDS
    )


def build_pool(database_url: SecretStr) -> ConnectionPool:
    """Build an unopened pool; the caller opens it with `open(wait=False)`."""
    return ConnectionPool(
        database_url.get_secret_value(),
        kwargs={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        min_size=1,
        max_size=4,
        open=False,
    )


class PostgresMigrationStore:
    """`MigrationStore` over one autocommit connection guarded by an advisory lock."""

    def __init__(
        self,
        conninfo: SecretStr | str,
        *,
        schema: str = "public",
        connect: Connect | None = None,
    ) -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError(_SCHEMA_ERROR)
        self._conninfo = _secret(conninfo)
        self._schema = schema
        self._connect: Connect = connect or _default_connect
        self._conn: psycopg.Connection[Any] | None = None

    def _open(self) -> "psycopg.Connection[Any]":
        if self._conn is None:
            try:
                conn = self._connect(self._conninfo)
                conn.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self._schema)
                    )
                )
            except _DRIVER_ERRORS as exc:
                raise StoreError(_safe_message("connect", exc)) from None
            self._conn = conn
        return self._conn

    def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except _DRIVER_ERRORS:
                pass

    @contextmanager
    def locked(self) -> Iterator[None]:
        conn = self._open()
        try:
            try:
                conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            except _DRIVER_ERRORS as exc:
                raise StoreError(_safe_message("lock", exc)) from None
            body_failed = False
            try:
                yield
            except BaseException:
                body_failed = True
                raise
            finally:
                try:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
                except _DRIVER_ERRORS as exc:
                    # Closing the session releases the lock anyway; never mask the
                    # error that is already propagating.
                    if not body_failed:
                        raise StoreError(_safe_message("unlock", exc)) from None
        finally:
            self._close()

    def ensure_table(self) -> None:
        conn = self._open()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_migrations ("
                "filename text PRIMARY KEY, "
                "checksum text NOT NULL, "
                "applied_at timestamptz NOT NULL)"
            )
        except _DRIVER_ERRORS as exc:
            raise StoreError(_safe_message("ensure_table", exc)) from None

    def applied(self) -> Mapping[str, str]:
        conn = self._open()
        try:
            rows = conn.execute(
                "SELECT filename, checksum FROM _schema_migrations"
            ).fetchall()
        except _DRIVER_ERRORS as exc:
            raise StoreError(_safe_message("applied", exc)) from None
        return {row[0]: row[1] for row in rows}

    def apply(self, migration: Migration, applied_at: datetime) -> None:
        conn = self._open()
        try:
            with conn.transaction():
                # Trusted, packaged, multi-statement SQL run with no parameters. mypy
                # treats LiteralString as str, so no cast is needed (a cast is flagged
                # as redundant); values are never interpolated into this text.
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO _schema_migrations (filename, checksum, applied_at) "
                    "VALUES (%s, %s, %s)",
                    (migration.name, migration.checksum, applied_at),
                )
        except _DRIVER_ERRORS as exc:
            raise StoreError(_safe_message("apply", exc, detail=True)) from None


class PostgresReadinessProbe:
    """`ReadinessProbe` running `SELECT 1` on a pooled connection in a worker thread."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _check(self) -> bool:
        try:
            with self._pool.connection(timeout=_PROBE_TIMEOUT_SECONDS) as conn:
                conn.execute("SELECT 1")
        except (*_DRIVER_ERRORS, PoolTimeout):
            return False
        return True

    async def is_ready(self) -> bool:
        return await anyio.to_thread.run_sync(self._check, abandon_on_cancel=True)


class PostgresDiagnostics:
    """`DbDiagnostics` for the doctor: a one-shot connection per call."""

    def __init__(
        self, conninfo: SecretStr | str, *, connect: Connect | None = None
    ) -> None:
        self._conninfo = _secret(conninfo)
        self._connect: Connect = connect or _default_connect

    def ping(self) -> None:
        try:
            conn = self._connect(self._conninfo)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
        except _DRIVER_ERRORS as exc:
            raise StoreError(_safe_message("ping", exc)) from None

    def has_extension(self, name: str) -> bool:
        """True when the extension is installed in the target database."""
        try:
            conn = self._connect(self._conninfo)
            try:
                row = conn.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = %s", (name,)
                ).fetchone()
            finally:
                conn.close()
        except _DRIVER_ERRORS as exc:
            raise StoreError(_safe_message("has_extension", exc)) from None
        return row is not None
