"""Property tests for the migration runner (Properties 4 and 5)."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from recromancer.db.runner import Migration, MigrationError, RunReport, run
from recromancer.domain.errors import ErrorCode
from tests.fakes import FAIL_MARKER, FakeClock, FakeMigrationStore
from tests.property.invariants import assert_same_state
from tests.property.strategies import migration_sets, migration_sets_with_failures


def _make(name: str, sql: str) -> Migration:
    return Migration(name, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest())


def _run(
    store: FakeMigrationStore,
    clock: FakeClock,
    migrations: tuple[Migration, ...],
) -> tuple[RunReport, list[str]]:
    warnings: list[str] = []
    report = run(store, migrations, clock, warnings.append)
    return report, warnings


# Feature: platform-foundation, Property 4: Applying a migration set twice is idempotent
# Validates: Requirements 7.5, 10.5
@settings(deadline=None)
@given(migration_sets())
def test_apply_twice_is_idempotent(migrations: tuple[Migration, ...]) -> None:
    store = FakeMigrationStore()
    clock = FakeClock()
    first_report, _ = _run(store, clock, migrations)
    first = store.snapshot()
    second_report, _ = _run(store, clock, migrations)
    second = store.snapshot()

    assert_same_state(first, second)
    assert second_report.applied == ()
    assert set(second_report.skipped) == set(first_report.applied) | set(
        first_report.skipped
    )
    assert second_report.empty == first_report.empty
    blank = {m.name for m in migrations if m.sql.strip() == ""}
    assert blank.isdisjoint(store.recorded)


# Complements Property 4 for sets containing a failing file: a failed run applies
# the files before the failure, and repeating it changes nothing.
@settings(deadline=None)
@given(migration_sets_with_failures())
def test_failed_run_twice_leaves_same_state(migrations: tuple[Migration, ...]) -> None:
    failing = next(m.name for m in migrations if m.sql.lstrip().startswith(FAIL_MARKER))
    store = FakeMigrationStore()
    clock = FakeClock()
    messages: list[str] = []
    snapshots = []
    for _ in range(2):
        with pytest.raises(MigrationError) as info:
            _run(store, clock, migrations)
        assert info.value.code is ErrorCode.MIGRATION_FAILED
        messages.append(str(info.value))
        snapshots.append(store.snapshot())

    assert messages[0] == messages[1]
    assert failing in messages[0]
    assert_same_state(snapshots[0], snapshots[1])
    later = [m.name for m in migrations if m.name > failing]
    assert failing not in store.recorded
    assert all(name not in store.recorded for name in later)


def test_blank_file_between_real_ones_is_warned_each_run_never_recorded() -> None:
    migrations = (
        _make("0001_a.sql", "create table a (id int);"),
        _make("0002_blank.sql", " \n\t"),
        _make("0003_b.sql", "create table b (id int);"),
    )
    store = FakeMigrationStore()
    clock = FakeClock()
    first_report, first_warnings = _run(store, clock, migrations)
    first = store.snapshot()
    second_report, second_warnings = _run(store, clock, migrations)

    assert_same_state(first, store.snapshot())
    assert first_report.applied == ("0001_a.sql", "0003_b.sql")
    assert second_report.applied == ()
    assert second_report.skipped == ("0001_a.sql", "0003_b.sql")
    assert first_report.empty == second_report.empty == ("0002_blank.sql",)
    assert len(first_warnings) == len(second_warnings) == 1
    assert "0002_blank.sql" in first_warnings[0]
    assert "0002_blank.sql" not in store.recorded


# FakeClock().now() is constant, so one instance per store gives both stores the same
# applied_at for every file; each store below still gets its own fresh clock.
def _prefix_then_full(
    migrations: tuple[Migration, ...], k: int
) -> tuple[FakeMigrationStore, FakeMigrationStore]:
    prefixed = FakeMigrationStore()
    clock_a = FakeClock()
    _run(prefixed, clock_a, migrations[:k])
    _run(prefixed, clock_a, migrations)
    direct = FakeMigrationStore()
    _run(direct, FakeClock(), migrations)
    return prefixed, direct


# Feature: platform-foundation, Property 5: Prefix-then-rest equals all-at-once
# Validates: Requirements 10.6
@settings(deadline=None)
@given(st.data())
def test_prefix_equivalence(data: st.DataObject) -> None:
    migrations = data.draw(migration_sets())
    k = data.draw(st.integers(0, len(migrations)))
    prefixed, direct = _prefix_then_full(migrations, k)
    assert_same_state(prefixed.snapshot(), direct.snapshot())


# Feature: platform-foundation, Property 5: Prefix-then-rest equals all-at-once
# Validates: Requirements 10.6
@settings(deadline=None)
@given(st.data())
def test_prefix_equivalence_with_blank_in_middle(data: st.DataObject) -> None:
    drawn = data.draw(migration_sets().filter(lambda s: len(s) >= 3))
    index = data.draw(st.integers(1, len(drawn) - 2))
    blank = data.draw(st.one_of(st.just(""), st.text(alphabet=" \t\n", max_size=6)))
    migrations = (
        *drawn[:index],
        _make(drawn[index].name, blank),
        *drawn[index + 1 :],
    )
    k = data.draw(st.integers(0, len(migrations)))
    prefixed, direct = _prefix_then_full(migrations, k)
    assert_same_state(prefixed.snapshot(), direct.snapshot())
    assert drawn[index].name not in prefixed.recorded


_FIXED_SET = (
    _make("0001_a.sql", "create table a (id int);"),
    _make("0002_b.sql", "create table b (id int);"),
    _make("0003_c.sql", "create table c (id int);"),
)


@pytest.mark.parametrize("k", [0, len(_FIXED_SET)])
def test_prefix_equivalence_boundaries(k: int) -> None:
    prefixed, direct = _prefix_then_full(_FIXED_SET, k)
    assert_same_state(prefixed.snapshot(), direct.snapshot())
    assert set(direct.recorded) == {m.name for m in _FIXED_SET}


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_prefix_equivalence_blank_file_in_middle(k: int) -> None:
    migrations = (
        _make("0001_a.sql", "create table a (id int);"),
        _make("0005_blank.sql", " \n\t"),
        _make("0009_b.sql", "create table b (id int);"),
    )
    # No MigrationError: the blank file is exempt from the out-of-order check.
    prefixed, direct = _prefix_then_full(migrations, k)
    assert_same_state(prefixed.snapshot(), direct.snapshot())
    assert "0005_blank.sql" not in prefixed.recorded
    assert "0005_blank.sql" not in direct.recorded
    assert set(direct.recorded) == {"0001_a.sql", "0009_b.sql"}
