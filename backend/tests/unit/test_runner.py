"""Unit tests for db/runner.py: discovery and the forward-only run (R7)."""

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

import recromancer.db.runner as runner_module
from recromancer.db.runner import (
    Migration,
    MigrationError,
    StoreError,
    discover,
    run,
)
from recromancer.domain.errors import ErrorCode
from tests.fakes import FAIL_MARKER, FakeClock, FakeMigrationStore


def _m(name: str, sql: str = "SELECT 1;") -> Migration:
    return Migration(name, sql, hashlib.sha256(sql.encode()).hexdigest())


def _record(store: FakeMigrationStore, *migrations: Migration) -> None:
    clock = FakeClock()
    for migration in migrations:
        run(store, (migration,), clock, lambda _msg: None)


def _applies(store: FakeMigrationStore) -> list[str]:
    return [e for e in store.events if e.startswith("apply:")]


def _write(directory: Path, files: dict[str, bytes]) -> Path:
    directory.mkdir(exist_ok=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory


# --------------------------------------------------------------------- discover


def test_discover_sorted_ignores_other_entries_and_hashes_bytes(tmp_path: Path) -> None:
    data = b"SELECT 2;\r\n"
    _write(tmp_path, {"0002_b.sql": data, "0001_a.sql": b"SELECT 1;"})
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "sub.sql").mkdir()
    (tmp_path / "subdir").mkdir()
    result = discover(tmp_path)
    assert isinstance(tmp_path, Path)
    assert [m.name for m in result] == ["0001_a.sql", "0002_b.sql"]
    assert result[1].sql == "SELECT 2;\r\n"
    assert result[1].checksum == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("bad", ["Bad.sql", "1_x.sql", "0001-x.sql", "0001_.sql"])
def test_discover_rejects_bad_names(tmp_path: Path, bad: str) -> None:
    _write(tmp_path, {"0001_ok.sql": b"x", bad: b"x"})
    with pytest.raises(MigrationError, match=bad) as info:
        discover(tmp_path)
    assert info.value.code is ErrorCode.MIGRATION_FAILED


def test_discover_rejects_duplicate_prefix(tmp_path: Path) -> None:
    _write(tmp_path, {"0001_a.sql": b"x", "0001_b.sql": b"x"})
    with pytest.raises(MigrationError) as info:
        discover(tmp_path)
    assert "0001_a.sql" in info.value.message
    assert "0001_b.sql" in info.value.message


def test_discover_missing_directory_names_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(MigrationError) as info:
        discover(missing)
    assert str(missing) in info.value.message


def test_discover_rejects_bom(tmp_path: Path) -> None:
    _write(tmp_path, {"0001_a.sql": b"\xef\xbb\xbfSELECT 1;"})
    with pytest.raises(MigrationError, match="0001_a.sql"):
        discover(tmp_path)


def test_discover_rejects_non_utf8_without_decoder_text(tmp_path: Path) -> None:
    _write(tmp_path, {"0001_a.sql": b"\xff\xfe\x00"})
    with pytest.raises(MigrationError) as info:
        discover(tmp_path)
    message = info.value.message
    assert "0001_a.sql" in message
    for word in ("codec", "byte", "position"):
        assert word not in message.lower()


# ------------------------------------------------------------------- run: basics


def test_run_applies_pending_in_order_and_skips_recorded() -> None:
    a, b, c = _m("0001_a.sql"), _m("0002_b.sql", "SELECT 2;"), _m("0003_c.sql", "S3;")
    store = FakeMigrationStore()
    _record(store, a)
    clock = FakeClock()
    report = run(store, (a, b, c), clock, lambda _msg: None)
    assert report.applied == (b.name, c.name)
    assert report.skipped == (a.name,)
    assert report.empty == ()
    assert _applies(store) == [
        "apply:0001_a.sql",
        "apply:0002_b.sql",
        "apply:0003_c.sql",
    ]
    for name in (b.name, c.name):
        applied_at = store.recorded[name][1]
        assert applied_at == clock.now()
        assert applied_at.tzinfo is not None
        assert applied_at.utcoffset() is not None


def test_run_with_no_migrations_reports_nothing() -> None:
    report = run(FakeMigrationStore(), (), FakeClock(), lambda _msg: None)
    assert (report.applied, report.skipped, report.empty) == ((), (), ())


# ------------------------------------------------------ checksum and out-of-order


def test_checksum_mismatch_applies_nothing() -> None:
    a = _m("0001_a.sql")
    store = FakeMigrationStore()
    _record(store, a)
    before = store.snapshot()
    changed = Migration(a.name, "SELECT 99;", "different")
    with pytest.raises(MigrationError, match="0001_a.sql") as info:
        run(store, (changed, _m("0002_b.sql")), FakeClock(), lambda _msg: None)
    assert info.value.code is ErrorCode.MIGRATION_FAILED
    assert store.snapshot() == before
    assert _applies(store) == ["apply:0001_a.sql"]


def test_out_of_order_rejected_and_above_highest_still_applies() -> None:
    c = _m("0003_c.sql", "S3;")
    store = FakeMigrationStore()
    _record(store, c)
    before = store.snapshot()
    with pytest.raises(MigrationError, match="0002_b.sql") as info:
        run(store, (_m("0002_b.sql", "S2;"), c), FakeClock(), lambda _msg: None)
    assert info.value.code is ErrorCode.MIGRATION_FAILED
    assert store.snapshot() == before

    report = run(store, (c, _m("0004_d.sql", "S4;")), FakeClock(), lambda _msg: None)
    assert report.applied == ("0004_d.sql",)
    assert report.skipped == (c.name,)


# --------------------------------------------------------------- blank-file rule


@pytest.mark.parametrize("blank", ["", "   ", "\n\t  ", "\n"])
def test_blank_file_skipped_with_one_warning_and_never_recorded(blank: str) -> None:
    store = FakeMigrationStore()
    warnings: list[str] = []
    report = run(
        store, (_m("0001_a.sql", blank), _m("0002_b.sql")), FakeClock(), warnings.append
    )
    assert report.empty == ("0001_a.sql",)
    assert report.applied == ("0002_b.sql",)
    assert len(warnings) == 1
    assert "0001_a.sql" in warnings[0]
    assert "0001_a.sql" not in store.recorded


def test_blank_below_highest_is_warning_only_then_filled_raises() -> None:
    c = _m("0003_c.sql", "S3;")
    store = FakeMigrationStore()
    _record(store, c)
    before = store.snapshot()
    blank = _m("0002_b.sql", "\n\t  ")
    warnings: list[str] = []
    for _ in range(2):
        report = run(store, (blank, c), FakeClock(), warnings.append)
        assert report.empty == (blank.name,)
        assert report.applied == ()
        assert store.snapshot() == before
    assert len(warnings) == 2
    assert all("0002_b.sql" in w for w in warnings)

    with pytest.raises(MigrationError, match="0002_b.sql") as info:
        run(store, (_m("0002_b.sql", "S2;"), c), FakeClock(), lambda _msg: None)
    assert info.value.code is ErrorCode.MIGRATION_FAILED
    assert store.snapshot() == before


def test_blank_then_filled_above_highest_applies() -> None:
    store = FakeMigrationStore()
    run(store, (_m("0001_a.sql", ""),), FakeClock(), lambda _msg: None)
    assert store.recorded == {}
    report = run(store, (_m("0001_a.sql"),), FakeClock(), lambda _msg: None)
    assert report.applied == ("0001_a.sql",)


# --------------------------------------------------------------- StoreError wrap


def test_store_error_wrapped_with_filename_and_no_cause() -> None:
    store = FakeMigrationStore()
    migrations = (
        _m("0001_a.sql"),
        _m("0002_b.sql", f"{FAIL_MARKER}\nSELECT 2;"),
        _m("0003_c.sql", "S3;"),
    )
    with pytest.raises(MigrationError) as info:
        run(store, migrations, FakeClock(), lambda _msg: None)
    assert info.value.message == "0002_b.sql: simulated failure"
    assert info.value.code is ErrorCode.MIGRATION_FAILED
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True
    assert list(store.recorded) == ["0001_a.sql"]
    assert _applies(store) == ["apply:0001_a.sql"]
    assert store.history == ("SELECT 1;",)


def test_long_store_error_truncated_to_500() -> None:
    class LongErrorStore(FakeMigrationStore):
        def apply(self, migration: Migration, applied_at: datetime) -> None:
            raise StoreError("x" * 600)

    with pytest.raises(MigrationError) as info:
        run(LongErrorStore(), (_m("0001_a.sql"),), FakeClock(), lambda _msg: None)
    message = info.value.message
    assert len(message) == 500
    assert message.endswith("...")
    assert message.startswith("0001_a.sql: xxx")


# -------------------------------------------------------------------------- lock


class _HeldRecordingStore(FakeMigrationStore):
    """Records whether the lock flag was held at every store call."""

    def __init__(self) -> None:
        super().__init__()
        self.held_at: list[tuple[str, bool]] = []

    def ensure_table(self) -> None:
        self.held_at.append(("ensure", self._held))
        super().ensure_table()

    def applied(self) -> dict[str, str]:
        self.held_at.append(("applied", self._held))
        return super().applied()

    def apply(self, migration: Migration, applied_at: datetime) -> None:
        self.held_at.append((f"apply:{migration.name}", self._held))
        super().apply(migration, applied_at)


def test_lock_held_for_whole_run() -> None:
    store = _HeldRecordingStore()
    run(store, (_m("0001_a.sql"), _m("0002_b.sql", "S2;")), FakeClock(), print)
    assert store.events == [
        "lock",
        "ensure",
        "apply:0001_a.sql",
        "apply:0002_b.sql",
        "unlock",
    ]
    assert [name for name, _ in store.held_at] == [
        "ensure",
        "applied",
        "apply:0001_a.sql",
        "apply:0002_b.sql",
    ]
    assert all(held for _, held in store.held_at)


def _checksum_mismatch(store: FakeMigrationStore) -> tuple[Migration, ...]:
    _record(store, _m("0001_a.sql"))
    return (Migration("0001_a.sql", "other", "other"), _m("0002_b.sql", "S2;"))


def _out_of_order(store: FakeMigrationStore) -> tuple[Migration, ...]:
    _record(store, _m("0003_c.sql", "S3;"))
    return (_m("0002_b.sql", "S2;"), _m("0003_c.sql", "S3;"))


def _store_failure(store: FakeMigrationStore) -> tuple[Migration, ...]:
    return (_m("0001_a.sql"), _m("0002_b.sql", FAIL_MARKER))


@pytest.mark.parametrize(
    "scenario", [_checksum_mismatch, _out_of_order, _store_failure]
)
def test_lock_released_last_even_on_error(
    scenario: Callable[[FakeMigrationStore], tuple[Migration, ...]],
) -> None:
    store = _HeldRecordingStore()
    migrations = scenario(store)
    start = len(store.events)
    with pytest.raises(MigrationError):
        run(store, migrations, FakeClock(), lambda _msg: None)
    events = store.events[start:]
    assert events[0] == "lock"
    assert events[-1] == "unlock"
    assert "unlock" not in events[:-1]
    assert all(held for _, held in store.held_at)


# ------------------------------------------------------------------ forward-only


@pytest.mark.parametrize(
    "name", ["down", "rollback", "revert", "downgrade", "undo", "migrate_down"]
)
def test_no_down_migration_api(name: str) -> None:
    assert not hasattr(runner_module, name)
