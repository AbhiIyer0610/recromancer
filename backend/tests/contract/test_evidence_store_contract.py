"""One EvidenceStore contract suite run against the fake and the Postgres store."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files

import pytest
from psycopg_pool import ConnectionPool

from recromancer.db.evidence_store import PostgresEvidenceStore
from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import discover, run
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    Chunk,
    EmbeddedChunk,
    EvidenceItem,
    ItemWrite,
    SourceLocator,
    StoredChunk,
)
from recromancer.service.ports import EvidenceStore
from tests.fakes import FakeEvidenceStore
from tests.fakes_api import FakeClock

pytestmark = pytest.mark.anyio

DIMENSIONS = 384
BUNDLE = "bundle-a"
ITEM = "item-1"
T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
T1 = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


@dataclass
class Backend:
    kind: str
    store: EvidenceStore
    other_workspace: Callable[[], EvidenceStore]
    break_next_write: Callable[[], None]


def _vector(seed: int) -> tuple[float, ...]:
    return tuple(
        0.001 + ((seed * 31 + i * 7) % 997) / 1000.0 for i in range(DIMENSIONS)
    )


def _item(item_id: str = ITEM, title: str = "title") -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind="commit",
        author="priya-raman",
        timestamp=T0,
        title=title,
        body="body text",
        url="https://example.invalid/ledgerline/x",
        path=None,
        references=("ref-1", "ref-2"),
        content_hash="a" * 64,
    )


def _chunk(item: EvidenceItem, position: int, chunk_id: str | None = None) -> Chunk:
    start = position * 10
    return Chunk(
        chunk_id=chunk_id or f"{item.id}#{position:04d}",
        position=position,
        item_id=item.id,
        kind=item.kind,
        author=item.author,
        timestamp=item.timestamp,
        text=f"chunk text {position}",
        locator=SourceLocator(
            item_id=item.id,
            start_offset=start,
            end_offset=start + 10,
            url=item.url,
            path=item.path,
        ),
    )


def _write(
    count: int,
    *,
    bundle: str = BUNDLE,
    item_id: str = ITEM,
    signature: str = "sig-1",
    title: str = "title",
    ingested_at: datetime = T1,
    seed: int = 1,
) -> ItemWrite:
    item = _item(item_id, title)
    return ItemWrite(
        bundle_id=bundle,
        subject_handle="priya-raman",
        item=item,
        signature=signature,
        ingested_at=ingested_at,
        chunks=tuple(
            EmbeddedChunk(_chunk(item, i), _vector(seed + i), "model-x")
            for i in range(count)
        ),
    )


async def _assert_matches(
    store: EvidenceStore, write: ItemWrite, bundle: str | None = None
) -> None:
    bundle_id = bundle or write.bundle_id
    assert (await store.signatures(bundle_id))[write.item.id] == write.signature
    stored = await store.get_item(bundle_id, write.item.id)
    assert stored is not None
    assert stored.item == write.item
    assert stored.subject_handle == write.subject_handle
    assert stored.signature == write.signature
    assert stored.ingested_at == write.ingested_at
    assert stored.ingested_at.utcoffset() == UTC.utcoffset(None)
    assert stored.item.timestamp.utcoffset() == UTC.utcoffset(None)
    chunks = await store.get_chunks(bundle_id, write.item.id)
    assert [c.chunk for c in chunks] == [e.chunk for e in write.chunks]
    assert [c.embedding_model for c in chunks] == [e.model_id for e in write.chunks]
    for got, want in zip(chunks, write.chunks, strict=True):
        assert got.bundle_id == bundle_id
        assert got.vector == pytest.approx(want.vector, rel=1e-6)


@pytest.fixture(params=["fake", "postgres"])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    if request.param == "fake":
        fake = FakeEvidenceStore()

        def inject_failure() -> None:
            fake._fail_on_item_ids = frozenset({ITEM})

        def no_shared_backing() -> EvidenceStore:
            pytest.skip("fake store has no shared backing across workspaces")

        yield Backend("fake", fake, no_shared_backing, inject_failure)
        return
    url: str = request.getfixturevalue("postgres_url")
    schema: str = request.getfixturevalue("pg_schema")
    run(
        PostgresMigrationStore(url, schema=schema),
        discover(files("recromancer.db") / "migrations"),
        FakeClock(),
        lambda _message: None,
    )
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=2,
        open=True,
        kwargs={"options": f"-c search_path={schema},public"},
    )
    try:
        yield Backend(
            "postgres",
            PostgresEvidenceStore(pool),
            lambda: PostgresEvidenceStore(pool, workspace_id="other"),
            lambda: None,
        )
    finally:
        pool.close()


async def test_empty_bundle_has_no_signatures(backend: Backend) -> None:
    assert await backend.store.signatures(BUNDLE) == {}
    assert await backend.store.get_item(BUNDLE, ITEM) is None
    assert await backend.store.get_chunks(BUNDLE, ITEM) == ()


async def test_replace_then_reads_agree(backend: Backend) -> None:
    write = _write(3)
    await backend.store.replace_item(write)
    await _assert_matches(backend.store, write)
    assert await backend.store.signatures(BUNDLE) == {ITEM: "sig-1"}


async def test_replacing_with_fewer_chunks_keeps_only_new_chunks(
    backend: Backend,
) -> None:
    await backend.store.replace_item(_write(3))
    smaller = _write(1, signature="sig-2", title="new title", seed=50)
    await backend.store.replace_item(smaller)
    chunks: tuple[StoredChunk, ...] = await backend.store.get_chunks(BUNDLE, ITEM)
    assert [c.chunk.chunk_id for c in chunks] == [f"{ITEM}#0000"]
    await _assert_matches(backend.store, smaller)


async def test_wrong_vector_length_raises_and_keeps_old_state(
    backend: Backend,
) -> None:
    old = _write(2)
    await backend.store.replace_item(old)
    bad_item = _item(title="changed")
    bad = ItemWrite(
        bundle_id=BUNDLE,
        subject_handle="priya-raman",
        item=bad_item,
        signature="sig-bad",
        ingested_at=T1,
        chunks=(EmbeddedChunk(_chunk(bad_item, 0), (1.0, 2.0), "model-x"),),
    )
    with pytest.raises(RecromancerError):
        await backend.store.replace_item(bad)
    await _assert_matches(backend.store, old)


async def test_failure_after_delete_keeps_old_state(backend: Backend) -> None:
    old = _write(2)
    await backend.store.replace_item(old)
    item = _item(title="changed")
    # Postgres: a duplicate chunk id makes the database reject the batch after
    # the upsert and the delete. Fake: the injected item failure.
    bad = ItemWrite(
        bundle_id=BUNDLE,
        subject_handle="priya-raman",
        item=item,
        signature="sig-bad",
        ingested_at=T1,
        chunks=(
            EmbeddedChunk(_chunk(item, 0, "dup"), _vector(7), "model-x"),
            EmbeddedChunk(_chunk(item, 1, "dup"), _vector(8), "model-x"),
        ),
    )
    backend.break_next_write()
    with pytest.raises(RecromancerError) as info:
        await backend.store.replace_item(bad)
    assert info.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    await _assert_matches(backend.store, old)


async def test_same_item_id_in_two_bundles_does_not_interfere(
    backend: Backend,
) -> None:
    first = _write(2, bundle="bundle-a", signature="sig-a", seed=1)
    second = _write(1, bundle="bundle-b", signature="sig-b", title="other", seed=90)
    await backend.store.replace_item(first)
    await backend.store.replace_item(second)
    await _assert_matches(backend.store, first)
    await _assert_matches(backend.store, second)
    replaced = _write(3, bundle="bundle-b", signature="sig-b2", seed=120)
    await backend.store.replace_item(replaced)
    await _assert_matches(backend.store, first)
    await _assert_matches(backend.store, replaced)


async def test_other_workspace_sees_nothing(backend: Backend) -> None:
    other = backend.other_workspace()
    write = _write(2)
    await backend.store.replace_item(write)
    assert await other.signatures(BUNDLE) == {}
    assert await other.get_item(BUNDLE, ITEM) is None
    assert await other.get_chunks(BUNDLE, ITEM) == ()
    await _assert_matches(backend.store, write)
