"""Contract of the in-memory migration store fake."""

from datetime import UTC, datetime, timedelta

import pytest

from recromancer.db.runner import Migration, MigrationStore, StoreError
from tests.fakes import FAIL_MARKER, FakeClock, FakeMigrationStore

AT = datetime(2026, 1, 1, tzinfo=UTC)


def _m(name: str = "0001_a.sql", sql: str = "SELECT 1;") -> Migration:
    return Migration(name, sql, f"sum-{sql}")


def _ready() -> FakeMigrationStore:
    store = FakeMigrationStore()
    with store.locked():
        store.ensure_table()
    return store


def test_protocol_fit() -> None:
    store: MigrationStore = FakeMigrationStore()
    assert store is not None


def test_apply_records_history_and_recorded_together() -> None:
    store = FakeMigrationStore()
    migration = _m()
    with store.locked():
        store.ensure_table()
        store.apply(migration, AT)
        assert store.applied() == {migration.name: migration.checksum}
    assert store.history == ("SELECT 1;",)
    assert store.recorded == {migration.name: (migration.checksum, AT)}
    assert store.schema_state() == store.history


@pytest.mark.parametrize("sql", [FAIL_MARKER, f"  \n{FAIL_MARKER} now"])
def test_failing_apply_changes_nothing(sql: str) -> None:
    store = _ready()
    with store.locked():
        store.apply(_m("0001_a.sql"), AT)
        before = store.snapshot()
        with pytest.raises(StoreError) as info:
            store.apply(_m("0002_b.sql", sql), AT)
    assert str(info.value) == "simulated failure"
    assert store.snapshot() == before


def test_lock_reentry_raises_and_releases_after_error() -> None:
    store = FakeMigrationStore()
    with store.locked(), pytest.raises(RuntimeError, match="already held"):
        with store.locked():
            pass
    with pytest.raises(ValueError, match="boom"), store.locked():
        raise ValueError("boom")
    with store.locked():
        pass
    assert store.events == ["lock", "unlock", "lock", "unlock", "lock", "unlock"]


def test_operations_require_lock_and_table() -> None:
    store = FakeMigrationStore()
    with pytest.raises(RuntimeError, match="not held"):
        store.ensure_table()
    with pytest.raises(RuntimeError, match="not held"):
        store.applied()
    with pytest.raises(RuntimeError, match="not held"):
        store.apply(_m(), AT)
    with store.locked():
        with pytest.raises(RuntimeError, match="table"):
            store.applied()
        with pytest.raises(RuntimeError, match="table"):
            store.apply(_m(), AT)


def test_ensure_table_idempotent_and_events_ordered() -> None:
    store = FakeMigrationStore()
    with store.locked():
        store.ensure_table()
        store.ensure_table()
        store.apply(_m(), AT)
    assert store.events == ["lock", "ensure", "ensure", "apply:0001_a.sql", "unlock"]


def test_snapshot_is_immutable_and_comparable() -> None:
    first, second = _ready(), _ready()
    for store in (first, second):
        with store.locked():
            store.apply(_m("0002_b.sql", "SELECT 2;"), AT)
            store.apply(_m("0001_a.sql"), AT)
    snap = first.snapshot()
    assert snap == second.snapshot()
    assert isinstance(snap[0], tuple)
    assert isinstance(snap[1], tuple)
    assert [row[0] for row in snap[1]] == ["0001_a.sql", "0002_b.sql"]
    with first.locked():
        first.apply(_m("0003_c.sql", "SELECT 3;"), AT)
    assert first.snapshot() != snap
    assert len(snap[0]) == 2


def test_fake_clock_is_utc_aware() -> None:
    clock = FakeClock()
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
