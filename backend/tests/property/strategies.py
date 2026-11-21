"""Shared Hypothesis strategies for property tests.

``migration_sets()`` never produces SQL that starts with ``FAIL_MARKER`` (the fake
store's failure injection), so Properties 4 and 5 stay deterministic.
``migration_sets_with_failures()`` always contains at least one such file.
"""

from __future__ import annotations

import hashlib
import string

from hypothesis import strategies as st

from recromancer.db.runner import Migration
from tests.fakes import FAIL_MARKER

__all__ = ["migration_sets", "migration_sets_with_failures"]

# No "-" here, so no SQL comment (and hence no FAIL_MARKER) can be formed.
_SAFE_ALPHABET = string.ascii_letters + string.digits + " \t\n;(),_'"
_WHITESPACE = " \t\n"
_NAME_ALPHABET = string.ascii_lowercase + string.digits + "_"
_MAX_FILES = 8


def _make(name: str, sql: str) -> Migration:
    return Migration(name, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest())


def _safe_text() -> st.SearchStrategy[str]:
    return st.text(alphabet=_SAFE_ALPHABET, max_size=40).filter(
        lambda sql: not sql.lstrip().startswith(FAIL_MARKER)
    )


@st.composite
def _sql(draw: st.DrawFn) -> str:
    """Safe SQL; about one file in four is blank or whitespace-only."""
    kind = draw(st.integers(0, 7))
    if kind == 0:
        return ""
    if kind == 1:
        return draw(st.text(alphabet=_WHITESPACE, min_size=1, max_size=6))
    return draw(_safe_text())


@st.composite
def _numbers(draw: st.DrawFn) -> list[int]:
    """Distinct increasing numbers from 1..9999; gaps are the norm."""
    chosen = draw(st.sets(st.integers(1, 9999), max_size=_MAX_FILES))
    return sorted(chosen)


@st.composite
def _names(draw: st.DrawFn) -> list[str]:
    numbers = draw(_numbers())
    words = [
        draw(st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=8)) for _ in numbers
    ]
    return [
        f"{number:04d}_{word}.sql" for number, word in zip(numbers, words, strict=True)
    ]


@st.composite
def migration_sets(draw: st.DrawFn) -> tuple[Migration, ...]:
    """0 to 8 migrations, strictly increasing names, SQL that never fails."""
    names = draw(_names())
    return tuple(_make(name, draw(_sql())) for name in names)


@st.composite
def migration_sets_with_failures(draw: st.DrawFn) -> tuple[Migration, ...]:
    """Like ``migration_sets`` but one non-blank file starts with ``FAIL_MARKER``."""
    names = draw(_names().filter(bool))
    position = draw(st.integers(0, len(names) - 1))
    lead = draw(st.text(alphabet=_WHITESPACE, max_size=3))
    tail = draw(st.text(alphabet=_SAFE_ALPHABET, max_size=20))
    files: list[Migration] = []
    for index, name in enumerate(names):
        if index == position:
            files.append(_make(name, lead + FAIL_MARKER + tail))
        else:
            files.append(_make(name, draw(_sql())))
    return tuple(files)
