"""FakeEmbedder and FakeEvidenceStore behave as the design specifies (FK)."""

import math
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    Chunk,
    EmbeddedChunk,
    EvidenceItem,
    ItemWrite,
    SourceLocator,
)
from recromancer.service.ports import Embedder, EvidenceStore
from tests.fakes import FakeEmbedder, FakeEvidenceStore

pytestmark = pytest.mark.anyio

NOW = datetime(2024, 1, 1, tzinfo=UTC)
DIMS = 8


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


async def test_default_dimension_model_and_unit_norm() -> None:
    embedder: Embedder = FakeEmbedder()
    vectors = await embedder.embed(["alpha", "beta", ""])
    assert embedder.model_id == "fake-embed-v1"
    assert embedder.dimensions == 384
    assert all(len(v) == 384 for v in vectors)
    assert all(abs(_norm(v) - 1.0) < 1e-6 for v in vectors)


async def test_vector_depends_only_on_text() -> None:
    embedder = FakeEmbedder(dimensions=100)
    first = await embedder.embed(["a", "b"])
    second = await embedder.embed(["b", "c", "a"])
    assert first[0] == second[2]
    assert first[1] == second[0]
    assert first[0] != first[1]
    assert second[1] != first[0]


async def test_empty_call_returns_empty_and_is_recorded() -> None:
    embedder = FakeEmbedder()
    assert await embedder.embed([]) == []
    assert (embedder.calls, embedder.texts_embedded) == (1, 0)
    await embedder.embed(["x", "y"])
    assert (embedder.calls, embedder.texts_embedded) == (2, 2)


async def test_fail_on_call_raises_only_on_that_call() -> None:
    embedder = FakeEmbedder(fail_on_call=2)
    await embedder.embed(["x"])
    with pytest.raises(RecromancerError) as info:
        await embedder.embed(["secret text"])
    assert info.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert "secret" not in str(info.value)
    assert embedder.texts_embedded == 1
    assert len(await embedder.embed(["z"])) == 1


@given(
    st.lists(st.text(max_size=30), max_size=5),
    st.integers(min_value=1, max_value=150),
)
async def test_property_unit_norm_and_text_only(texts: list[str], dims: int) -> None:
    embedder = FakeEmbedder(dimensions=dims)
    vectors = await embedder.embed(texts)
    again = await embedder.embed(list(reversed(texts)))
    assert vectors == list(reversed(again))
    for vector in vectors:
        assert len(vector) == dims
        assert abs(_norm(vector) - 1.0) < 1e-6


# ---- FakeEvidenceStore ----


def _item(item_id: str) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind="doc",
        author="ada",
        timestamp=NOW,
        title="t",
        body="b",
        url=None,
        path="docs/a.md",
        references=(),
        content_hash="h",
    )


def _write(
    item_id: str, *, bundle: str = "b1", signature: str = "s1", dims: int = DIMS
) -> ItemWrite:
    chunks = tuple(
        EmbeddedChunk(
            chunk=Chunk(
                chunk_id=f"{item_id}-{i}",
                position=i,
                item_id=item_id,
                kind="doc",
                author="ada",
                timestamp=NOW,
                text=f"text {i}",
                locator=SourceLocator(item_id, 0, 6, None, "docs/a.md"),
            ),
            vector=tuple([1.0] + [0.0] * (dims - 1)),
            model_id="fake-embed-v1",
        )
        for i in range(2)
    )
    return ItemWrite(bundle, "ada", _item(item_id), signature, NOW, chunks)


async def test_replace_then_get() -> None:
    store: EvidenceStore = FakeEvidenceStore(dimensions=DIMS)
    await store.replace_item(_write("i1"))
    stored = await store.get_item("b1", "i1")
    assert stored is not None and stored.signature == "s1"
    assert [c.chunk.chunk_id for c in await store.get_chunks("b1", "i1")] == [
        "i1-0",
        "i1-1",
    ]
    assert await store.get_item("b1", "missing") is None
    assert await store.get_chunks("b2", "i1") == ()


async def test_replace_swaps_old_chunks() -> None:
    store = FakeEvidenceStore(dimensions=DIMS)
    await store.replace_item(_write("i1"))
    fewer = _write("i1", signature="s2")
    await store.replace_item(
        ItemWrite(
            fewer.bundle_id,
            fewer.subject_handle,
            fewer.item,
            "s2",
            NOW,
            fewer.chunks[:1],
        )
    )
    assert len(await store.get_chunks("b1", "i1")) == 1
    assert await store.signatures("b1") == {"i1": "s2"}


async def test_wrong_dimension_leaves_state_unchanged() -> None:
    store = FakeEvidenceStore(dimensions=DIMS)
    await store.replace_item(_write("i1"))
    before = store.snapshot()
    with pytest.raises(RecromancerError) as info:
        await store.replace_item(_write("i1", signature="s2", dims=DIMS + 1))
    assert info.value.code is ErrorCode.VALIDATION_ERROR
    assert store.snapshot() == before


async def test_injected_failure_leaves_state_unchanged() -> None:
    store = FakeEvidenceStore(dimensions=DIMS, fail_on_item_ids=frozenset({"bad"}))
    await store.replace_item(_write("i1"))
    before = store.snapshot()
    with pytest.raises(RecromancerError) as info:
        await store.replace_item(_write("bad"))
    assert info.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert store.snapshot() == before


async def test_snapshot_is_deterministic_regardless_of_write_order() -> None:
    one = FakeEvidenceStore(dimensions=DIMS)
    two = FakeEvidenceStore(dimensions=DIMS)
    for item_id in ("a", "b", "c"):
        await one.replace_item(_write(item_id))
    for item_id in ("c", "a", "b"):
        await two.replace_item(_write(item_id))
    assert one.snapshot() == two.snapshot()


async def test_signatures_are_per_bundle() -> None:
    store = FakeEvidenceStore(dimensions=DIMS)
    await store.replace_item(_write("i1", bundle="b1", signature="x"))
    await store.replace_item(_write("i2", bundle="b2", signature="y"))
    assert await store.signatures("b1") == {"i1": "x"}
    assert await store.signatures("b2") == {"i2": "y"}
    assert await store.signatures("b3") == {}
