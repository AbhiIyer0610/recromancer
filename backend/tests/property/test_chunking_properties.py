"""Property tests for the chunker (evidence-ingestion)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from hypothesis import example, given
from hypothesis import strategies as st

from recromancer.domain.errors import ChunkingError
from recromancer.domain.evidence import EvidenceItem
from recromancer.ingest.chunking import MAX_CHUNKS_PER_ITEM, chunk_item, source_text
from tests.property.invariants import (
    assert_boundary_rule,
    assert_chunk_ids,
    assert_chunk_metadata,
    assert_lossless_cover,
)
from tests.property.strategies import (
    chunk_params,
    chunk_texts,
    evidence_items,
    long_lf_texts,
)

_BREAKY = ["\n", "\n\n", " ", "\t", "\u00e9", "\U0001f680", "a", "b"]


@st.composite
def _dense_texts(draw: st.DrawFn) -> str:
    """Short texts dense in newlines, paragraph breaks and spaces.

    Together with small sizes this puts breaks at every offset around the window edge.
    """
    return "".join(draw(st.lists(st.sampled_from(_BREAKY), max_size=120)))


@st.composite
def _small_params(draw: st.DrawFn) -> tuple[int, int]:
    size = draw(st.integers(1, 30))
    return size, draw(st.integers(0, size - 1))


@st.composite
def _long_params(draw: st.DrawFn) -> tuple[int, int]:
    size = draw(st.integers(20, 1000))
    return size, draw(st.integers(0, size // 2))


@st.composite
def _cases(draw: st.DrawFn) -> tuple[EvidenceItem, int, int]:
    base = draw(evidence_items())
    text = draw(
        st.one_of(
            st.just(""),
            st.text(alphabet=" \t\n", max_size=60),
            chunk_texts(),
            _dense_texts(),
            long_lf_texts(),
        )
    )
    long_text = len(text) >= 5000
    size, overlap = draw(
        _long_params()
        if long_text
        else st.one_of(chunk_params(), _small_params(), _small_params())
    )
    return dataclasses.replace(base, title=text, body=""), size, overlap


# Feature: evidence-ingestion, Property 1: Chunking covers the text without loss and follows the boundary rule  # noqa: E501
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.10, 3.11, 8.2
#
# Mutation_Checks (R8.5, R8.6), each applied alone to chunking.py and each makes this
# property fail (details in tasks.md, "Notes (task 1.12)"):
#   (a) drop the final character of the last chunk;
#   (b) start the next chunk at ``end + 1`` instead of ``end - overlap``;
#   (c) use ``rfind(..., lo, hi + 1)`` in both rfind calls.
@given(_cases())
def test_chunks_cover_text_without_loss(
    case: tuple[EvidenceItem, int, int],
) -> None:
    item, size, overlap = case
    text = source_text(item)
    try:
        chunks = chunk_item(item, size=size, overlap=overlap)
    except ChunkingError:
        # Starts strictly increase, so more than the limit implies a long text.
        assert len(text) > MAX_CHUNKS_PER_ITEM
        return
    assert_lossless_cover(text, chunks, overlap)
    assert_boundary_rule(text, chunks, size, overlap)
    assert_chunk_metadata(item, chunks)


_PREFIX_IDS = ["abc", "abc.1", "abc.1.1", "abc-1", "a", "a.1"]

_EX_BASE = EvidenceItem(
    id="x",
    kind="commit",
    author="ada",
    timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    title="",
    body="",
    url="https://example.invalid/x",
    path=None,
    references=(),
    content_hash="",
)


@st.composite
def _id_items(draw: st.DrawFn) -> EvidenceItem:
    """An item with a short dense title (one or many chunks) and a pool or free id."""
    base = draw(evidence_items())
    item_id = draw(st.one_of(st.sampled_from(_PREFIX_IDS), st.just(base.id)))
    title = draw(_dense_texts())
    return dataclasses.replace(base, id=item_id, title=title, body="")


@st.composite
def _id_cases(draw: st.DrawFn) -> tuple[list[EvidenceItem], int, int]:
    items = draw(
        st.lists(_id_items(), min_size=2, max_size=6, unique_by=lambda i: i.id)
    )
    size, overlap = draw(_small_params())
    return items, size, overlap


# Feature: evidence-ingestion, Property 2: Chunk ids are deterministic, well formed and unique  # noqa: E501
# Validates: Requirements 3.6, 3.9, 8.2
#
# Mutation_Check (R8.5, R8.6), applied alone to chunking.py:
# ``chunk_id=f"{position:04d}"`` (position only). It makes the cross-item
# uniqueness assertion fail (details in tasks.md, "Notes (task 1.13)"). That
# assertion runs before the format assertion.
@given(_id_cases())
@example(
    (
        [
            dataclasses.replace(_EX_BASE, id="abc", title="a b c d e"),
            dataclasses.replace(_EX_BASE, id="abc.1", title="f g h i j"),
        ],
        3,
        1,
    )
)
def test_chunk_ids_are_deterministic_and_unique(
    case: tuple[list[EvidenceItem], int, int],
) -> None:
    items, size, overlap = case
    all_ids: list[str] = []
    for item in items:
        chunks = chunk_item(item, size=size, overlap=overlap)
        all_ids.extend(chunk.chunk_id for chunk in chunks)
    assert len(all_ids) == len(set(all_ids)), "chunk ids collide across items"
    for item in items:
        chunks = chunk_item(item, size=size, overlap=overlap)
        ids = [chunk.chunk_id for chunk in chunks]
        assert len(ids) == len(set(ids)), "chunk ids collide within an item"
        assert_chunk_ids(item.id, chunks)
        assert chunk_item(item, size=size, overlap=overlap) == chunks
