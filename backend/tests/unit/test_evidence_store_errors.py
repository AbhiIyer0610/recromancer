"""Evidence store behaviour that needs no database."""

from datetime import UTC, datetime
from typing import Any, cast

import psycopg
import psycopg_pool
import pytest

from recromancer.db.evidence_store import (
    EMBEDDING_DIMENSIONS,
    PostgresEvidenceStore,
    _vector_literal,
)
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    Chunk,
    EmbeddedChunk,
    EvidenceItem,
    ItemWrite,
    SourceLocator,
)

_LEAKY = (
    'connection to server at "h" failed: password authentication failed for user "u" '
    "password=SECRETPW"
)
_TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class _TouchedPool:
    def __init__(self, error: Exception | None = None) -> None:
        self.touched = 0
        self._error = error

    def connection(self, *args: object, **kwargs: object) -> Any:
        self.touched += 1
        raise self._error or AssertionError("pool must not be touched")


def _store(pool: _TouchedPool) -> PostgresEvidenceStore:
    return PostgresEvidenceStore(cast(psycopg_pool.ConnectionPool, pool))


def _write(vector: tuple[float, ...], chunk_id: str = "i1#0000") -> ItemWrite:
    item = EvidenceItem(
        "i1",
        "commit",
        "a",
        _TS,
        "t",
        "body",
        "https://example.invalid/x",
        None,
        (),
        "h",
    )
    locator = SourceLocator("i1", 0, 4, "https://example.invalid/x", None)
    chunk = Chunk(chunk_id, 0, "i1", "commit", "a", _TS, "body", locator)
    return ItemWrite("b", "s", item, "sig", _TS, (EmbeddedChunk(chunk, vector, "m"),))


def _good() -> tuple[float, ...]:
    return (0.5,) * EMBEDDING_DIMENSIONS


@pytest.mark.anyio
async def test_driver_error_has_no_conninfo() -> None:
    pool = _TouchedPool(psycopg.OperationalError(_LEAKY))
    store = _store(pool)
    with pytest.raises(RecromancerError) as caught:
        await store.replace_item(_write(_good()))
    message = str(caught.value)
    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert "OperationalError" in message
    assert not any(p in message for p in ("SECRETPW", "password=", '"h"', "conninfo"))
    assert caught.value.__cause__ is None
    assert pool.touched == 1


@pytest.mark.anyio
async def test_read_driver_error_has_no_conninfo() -> None:
    store = _store(_TouchedPool(psycopg.OperationalError(_LEAKY)))
    with pytest.raises(RecromancerError) as caught:
        await store.get_item("b", "i1")
    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert "SECRETPW" not in str(caught.value)


def test_vector_literal_format_and_round_trip() -> None:
    values = [0.1, -2.5, 1e-7, 1 / 3, 3.0, 1e300]
    literal = _vector_literal(values)
    assert literal == "[" + ",".join(repr(v) for v in values) + "]"
    assert [float(p) for p in literal[1:-1].split(",")] == values
    assert _vector_literal([]) == "[]"


@pytest.mark.anyio
@pytest.mark.parametrize("length", [EMBEDDING_DIMENSIONS - 1, EMBEDDING_DIMENSIONS + 1])
async def test_wrong_dimension_is_rejected_before_connecting(length: int) -> None:
    pool = _TouchedPool()
    with pytest.raises(RecromancerError) as caught:
        await _store(pool).replace_item(_write((0.1,) * length, "i1#0007"))
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    message = str(caught.value)
    assert "i1#0007" in message
    assert str(length) in message
    assert str(EMBEDDING_DIMENSIONS) in message
    assert pool.touched == 0


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_value_is_rejected_before_connecting(bad: float) -> None:
    pool = _TouchedPool()
    vector = (*_good()[:-1], bad)
    with pytest.raises(RecromancerError) as caught:
        await _store(pool).replace_item(_write(vector, "i1#0003"))
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert "i1#0003" in str(caught.value)
    assert "non-finite value" in str(caught.value)
    assert pool.touched == 0
