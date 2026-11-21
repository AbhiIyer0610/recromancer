"""Forward-only migration planning, the `MigrationStore` port and `run()`.

Pure logic: no driver import. The Postgres store lives in `db/postgres.py`.
A UTF-8 BOM at the start of a file is rejected (not stripped), so the SQL text
and the checksummed bytes never disagree about what the file contains.
"""

import hashlib
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from importlib.resources.abc import Traversable
from typing import Protocol

from recromancer.domain.errors import ErrorCode, RecromancerError

__all__ = [
    "Migration",
    "MigrationError",
    "MigrationStore",
    "RunReport",
    "StoreError",
    "discover",
    "run",
]

_NAME_PATTERN = re.compile(r"[0-9]{4}_[a-z0-9_]+\.sql")
_BOM = "\ufeff"


@dataclass(frozen=True)
class Migration:
    """One migration file: its name, decoded SQL and SHA-256 of the file bytes."""

    name: str
    sql: str
    checksum: str


@dataclass(frozen=True)
class RunReport:
    """Outcome of a run: applied, already-recorded (skipped) and blank (empty) files."""

    applied: tuple[str, ...]
    skipped: tuple[str, ...]
    empty: tuple[str, ...]


class StoreError(Exception):
    """A store failure carrying only a message; stores wrap driver errors in it."""


class MigrationError(RecromancerError):
    """Migration discovery or execution failed."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.MIGRATION_FAILED, _truncate(message))


class _Clock(Protocol):
    def now(self) -> datetime: ...


class MigrationStore(Protocol):
    """Storage port for applied migrations."""

    def locked(self) -> AbstractContextManager[None]:
        """Context manager holding the exclusive migration lock."""
        ...

    def ensure_table(self) -> None: ...

    def applied(self) -> Mapping[str, str]:
        """Recorded filename -> checksum."""
        ...

    def apply(self, migration: Migration, applied_at: datetime) -> None:
        """Execute the SQL and record it in one transaction."""
        ...


def _truncate(message: str) -> str:
    limit = RecromancerError.MAX_MESSAGE_LENGTH
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _number(name: str) -> int:
    return int(name[:4])


def discover(directory: Traversable) -> tuple[Migration, ...]:
    """Read `NNNN_name.sql` files from `directory`, sorted by filename."""
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    found: dict[str, Traversable] = {}
    for entry in directory.iterdir():
        if entry.name.endswith(".sql") and entry.is_file():
            if _NAME_PATTERN.fullmatch(entry.name) is None:
                raise MigrationError(
                    f"invalid migration file name: {entry.name} "
                    "(expected NNNN_lowercase_name.sql)"
                )
            found[entry.name] = entry
    migrations: list[Migration] = []
    prefixes: dict[int, str] = {}
    for name in sorted(found):
        number = _number(name)
        if number in prefixes:
            raise MigrationError(
                f"duplicate migration number: {prefixes[number]} and {name}"
            )
        prefixes[number] = name
        data = found[name].read_bytes()
        try:
            sql = data.decode("utf-8")
        except UnicodeDecodeError:
            raise MigrationError(f"migration file is not valid UTF-8: {name}") from None
        if sql.startswith(_BOM):
            raise MigrationError(f"migration file has a UTF-8 BOM, remove it: {name}")
        migrations.append(Migration(name, sql, hashlib.sha256(data).hexdigest()))
    return tuple(migrations)


def run(
    store: MigrationStore,
    migrations: tuple[Migration, ...],
    clock: _Clock,
    warn: Callable[[str], None],
) -> RunReport:
    """Apply pending migrations in filename order under the store's lock."""
    with store.locked():
        store.ensure_table()
        recorded = dict(store.applied())
        for migration in migrations:
            stored = recorded.get(migration.name)
            if stored is not None and stored != migration.checksum:
                raise MigrationError(
                    f"checksum mismatch: {migration.name} changed after apply"
                )
        highest = max((_number(name) for name in recorded), default=0)
        for migration in migrations:
            if (
                migration.name not in recorded
                and migration.sql.strip() != ""
                and _number(migration.name) < highest
            ):
                raise MigrationError(
                    f"out-of-order migration: {migration.name} is numbered below the "
                    f"highest applied ({highest:04d}); renumber it above {highest:04d}"
                )
        applied: list[str] = []
        skipped: list[str] = []
        empty: list[str] = []
        for migration in migrations:
            if migration.name in recorded:
                skipped.append(migration.name)
            elif migration.sql.strip() == "":
                warn(f"empty migration skipped: {migration.name}")
                empty.append(migration.name)
            else:
                try:
                    store.apply(migration, clock.now())
                except StoreError as exc:
                    raise MigrationError(f"{migration.name}: {exc}") from None
                applied.append(migration.name)
        return RunReport(tuple(applied), tuple(skipped), tuple(empty))
