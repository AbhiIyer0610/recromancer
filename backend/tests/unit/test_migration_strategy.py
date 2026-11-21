"""Guard test for the migration strategies.

``max_examples`` is fixed and ``derandomize=True`` on purpose here: these tests check
the strategies themselves and must be reproducible. The property tests that use the
strategies (8.6, 8.7) do not hard-code ``max_examples``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st

from recromancer.db.runner import Migration
from tests.fakes import FAIL_MARKER
from tests.property.strategies import migration_sets, migration_sets_with_failures

_NAME = re.compile(r"[0-9]{4}_[a-z0-9_]+\.sql")
_GUARD = settings(max_examples=150, database=None, derandomize=True)

MigrationSet = tuple[Migration, ...]


def _collect(strategy: st.SearchStrategy[MigrationSet]) -> list[MigrationSet]:
    """Draw 150 deterministic examples and return them."""
    seen: list[MigrationSet] = []

    @_GUARD
    @given(strategy)
    def draw_all(files: MigrationSet) -> None:
        seen.append(files)

    draw_all()
    return seen


def _starts_with_marker(migration: Migration) -> bool:
    return migration.sql.strip().startswith(FAIL_MARKER)


def _check_shape(files: MigrationSet) -> None:
    assert len(files) <= 8
    names = [m.name for m in files]
    assert all(_NAME.fullmatch(n) for n in names)
    numbers = [int(n[:4]) for n in names]
    assert all(a < b for a, b in zip(numbers, numbers[1:], strict=False))
    for m in files:
        assert m.checksum == hashlib.sha256(m.sql.encode("utf-8")).hexdigest()


def _any(sets: list[MigrationSet], predicate: Callable[[MigrationSet], bool]) -> bool:
    return any(predicate(files) for files in sets)


def test_migration_sets_invariants() -> None:
    sets = _collect(migration_sets())
    assert len(sets) >= 100
    for files in sets:
        _check_shape(files)
        assert not any(_starts_with_marker(m) for m in files)


def test_migration_sets_cover_interesting_cases() -> None:
    sets = _collect(migration_sets())
    assert _any(sets, lambda fs: any(m.sql == "" for m in fs))
    assert _any(sets, lambda fs: any(m.sql != "" and m.sql.strip() == "" for m in fs))
    assert _any(
        sets,
        lambda fs: any(
            int(b.name[:4]) - int(a.name[:4]) > 1
            for a, b in zip(fs, fs[1:], strict=False)
        ),
    )
    assert _any(sets, lambda fs: len(fs) == 0)


def test_migration_sets_with_failures_always_fail_somewhere() -> None:
    sets = _collect(migration_sets_with_failures())
    assert len(sets) >= 100
    for files in sets:
        _check_shape(files)
        failing = [m for m in files if _starts_with_marker(m)]
        assert failing
        assert all(m.sql.strip() != "" for m in failing)
    assert _any(sets, lambda fs: any(m.sql == "" for m in fs))
