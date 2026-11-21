"""In-memory fakes for the migration runner tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from recromancer.db.runner import Migration, StoreError
from tests.fakes_api import FakeClock as FakeClock

# SQL prefix that makes FakeMigrationStore.apply fail (strategies import it).
FAIL_MARKER = "-- fail"

Snapshot = tuple[tuple[str, ...], tuple[tuple[str, str, datetime], ...]]


class FakeMigrationStore:
    """Deterministic ``MigrationStore``.

    ``history`` (the executed SQL, in order) models the schema. ``recorded`` maps
    filename -> (checksum, applied_at). Both change together inside ``apply``.
    """

    def __init__(self) -> None:
        self.history: tuple[str, ...] = ()
        self.recorded: dict[str, tuple[str, datetime]] = {}
        self.events: list[str] = []
        self._held = False
        self._table = False

    @contextmanager
    def locked(self) -> Iterator[None]:
        if self._held:
            raise RuntimeError("migration lock is already held")
        self._held = True
        self.events.append("lock")
        try:
            yield
        finally:
            self._held = False
            self.events.append("unlock")

    def _require_lock(self) -> None:
        if not self._held:
            raise RuntimeError("migration lock is not held")

    def ensure_table(self) -> None:
        self._require_lock()
        self.events.append("ensure")
        self._table = True

    def applied(self) -> dict[str, str]:
        self._require_lock()
        if not self._table:
            raise RuntimeError("migrations table does not exist")
        return {name: checksum for name, (checksum, _) in self.recorded.items()}

    def apply(self, migration: Migration, applied_at: datetime) -> None:
        self._require_lock()
        if not self._table:
            raise RuntimeError("migrations table does not exist")
        if migration.sql.lstrip().startswith(FAIL_MARKER):
            raise StoreError("simulated failure")
        new_history = (*self.history, migration.sql)
        new_recorded = {
            **self.recorded,
            migration.name: (migration.checksum, applied_at),
        }
        self.history = new_history
        self.recorded = new_recorded
        self.events.append(f"apply:{migration.name}")

    def schema_state(self) -> tuple[str, ...]:
        return self.history

    def snapshot(self) -> Snapshot:
        recorded = tuple(
            (name, checksum, at)
            for name, (checksum, at) in sorted(self.recorded.items())
        )
        return (self.history, recorded)
