from datetime import UTC, datetime

import pytest

from recromancer.domain.errors import ChunkingError
from recromancer.domain.evidence import EvidenceItem
from recromancer.ingest.chunking import chunk_item, source_text


def _item(title: str, body: str = "") -> EvidenceItem:
    return EvidenceItem(
        id="pr-1",
        kind="pull_request",
        author="dana",
        timestamp=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        title=title,
        body=body,
        url="https://example.test/pr/1",
        path=None,
        references=(),
        content_hash="h",
    )


def test_source_text_joins_title_and_body() -> None:
    assert source_text(_item("T")) == "T"
    assert source_text(_item("T", "B")) == "T\n\nB"


def test_single_chunk_carries_metadata() -> None:
    item = _item("hello", "world")
    (chunk,) = chunk_item(item)
    assert chunk.text == "hello\n\nworld"
    assert chunk.chunk_id == "pr-1#0000"
    assert chunk.position == 0
    assert (chunk.kind, chunk.author) == (item.kind, item.author)
    assert chunk.timestamp == item.timestamp
    assert (chunk.locator.start_offset, chunk.locator.end_offset) == (0, 12)
    assert chunk.locator.url == item.url
    assert chunk.locator.path is None


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_item(_item("")) == ()


def test_paragraph_break_snap() -> None:
    text = "a" * 10 + "\n\n" + "b" * 30
    chunks = chunk_item(_item(text), size=20, overlap=2)
    assert chunks[0].text == "a" * 10 + "\n\n"


def test_whitespace_snap() -> None:
    chunks = chunk_item(_item("aaaa bbbbbbbbbbbbbbbbbbbbbb"), size=10, overlap=2)
    assert chunks[0].text == "aaaa "


def test_hard_cut_without_break() -> None:
    chunks = chunk_item(_item("x" * 25), size=10, overlap=2)
    assert chunks[0].text == "x" * 10


def test_consecutive_chunks_overlap_and_cover_text() -> None:
    text = "".join(chr(97 + i % 26) for i in range(95))
    chunks = chunk_item(_item(text), size=20, overlap=5)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.locator.start_offset == prev.locator.end_offset - 5
    assert chunks[-1].locator.end_offset == len(text)
    assert [c.chunk_id for c in chunks[:2]] == ["pr-1#0000", "pr-1#0001"]
    for c in chunks:
        assert text[c.locator.start_offset : c.locator.end_offset] == c.text


def test_zero_window_still_terminates() -> None:
    chunks = chunk_item(_item("x" * 12), size=3, overlap=2)
    starts = [c.locator.start_offset for c in chunks]
    assert starts == sorted(set(starts))
    assert chunks[-1].locator.end_offset == 12


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, -1), (10, 10), (10, 11)])
def test_invalid_parameters_raise(size: int, overlap: int) -> None:
    with pytest.raises(ChunkingError):
        chunk_item(_item("text"), size=size, overlap=overlap)
