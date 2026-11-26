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


# --- task 1.12: fixed boundary examples ----------------------------------------------


def _spans(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    chunks = chunk_item(_item(text), size=size, overlap=overlap)
    return [(c.locator.start_offset, c.locator.end_offset) for c in chunks]


def test_text_of_exactly_size_is_one_chunk() -> None:
    assert _spans("x" * 10, 10, 2) == [(0, 10)]


def test_text_of_size_plus_one_gives_two_overlapping_chunks() -> None:
    assert _spans("x" * 11, 10, 2) == [(0, 10), (8, 11)]


def test_only_a_newline_is_one_chunk() -> None:
    (chunk,) = chunk_item(_item("\n"), size=10, overlap=2)
    assert chunk.text == "\n"


def test_single_newline_snap_ends_after_the_newline() -> None:
    chunks = chunk_item(_item("a" * 5 + "\n" + "b" * 20), size=10, overlap=2)
    assert chunks[0].text == "a" * 5 + "\n"


def test_paragraph_break_inside_window_snaps_after_both_newlines() -> None:
    # size 10, overlap 2: window 7, lo 3, hi 10; the break sits at 3..4.
    chunks = chunk_item(_item("a" * 3 + "\n\n" + "b" * 20), size=10, overlap=2)
    assert chunks[0].locator.end_offset == 5


def test_paragraph_break_straddling_lo_falls_back_to_the_single_newline() -> None:
    # The break sits at 2..3, so only its second newline (index 3) is inside [3, 10).
    chunks = chunk_item(_item("a" * 2 + "\n\n" + "b" * 20), size=10, overlap=2)
    assert chunks[0].locator.end_offset == 4


def test_paragraph_break_straddling_hi_falls_back_to_the_first_newline() -> None:
    # The break sits at 9..10, so only its first newline (index 9) is inside [3, 10).
    chunks = chunk_item(_item("b" * 9 + "\n\n" + "c" * 20), size=10, overlap=2)
    assert chunks[0].locator.end_offset == 10
    assert chunks[0].text == "b" * 9 + "\n"


def test_overlap_zero_gives_adjacent_chunks() -> None:
    assert _spans("a" * 25, 10, 0) == [(0, 10), (10, 20), (20, 25)]


def test_overlap_size_minus_one_advances_one_character_at_a_time() -> None:
    assert _spans("abcdefgh", 5, 4) == [(0, 5), (1, 6), (2, 7), (3, 8)]


def test_size_one_gives_one_chunk_per_character() -> None:
    chunks = chunk_item(_item("abc"), size=1, overlap=0)
    assert [c.text for c in chunks] == ["a", "b", "c"]


def test_size_two_with_and_without_overlap() -> None:
    zero = chunk_item(_item("abcde"), size=2, overlap=0)
    assert [c.text for c in zero] == ["ab", "cd", "e"]
    one = chunk_item(_item("abcd"), size=2, overlap=1)
    assert [c.text for c in one] == ["ab", "bc", "cd"]


@pytest.mark.parametrize("space", ["\x1c", "\x85", "\u2028", "\u3000"])
def test_whitespace_snap_uses_str_isspace(space: str) -> None:
    # Design Risk 11: str.isspace() is true for these, so the snap picks them.
    assert space.isspace()
    chunks = chunk_item(_item("aaaa" + space + "b" * 30), size=10, overlap=2)
    assert chunks[0].text == "aaaa" + space


# --- task 1.12: EX-PARAMS ------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11), (1, 1), (1, 2)],
)
def test_ex_params_invalid_raise_chunking_error(size: int, overlap: int) -> None:
    with pytest.raises(ChunkingError):
        chunk_item(_item("some text"), size=size, overlap=overlap)


@pytest.mark.parametrize(("size", "overlap"), [(1, 0), (5, 4)])
def test_ex_params_extreme_valid_parameters_return_chunks(
    size: int, overlap: int
) -> None:
    chunks = chunk_item(_item("some text to split"), size=size, overlap=overlap)
    assert chunks
    assert chunks[-1].locator.end_offset == len("some text to split")


def test_ex_params_more_than_10000_chunks_raises() -> None:
    # size 2, overlap 1 advances one character per chunk: 10002 characters give
    # starts 0..10000, which is 10001 chunks.
    with pytest.raises(ChunkingError):
        chunk_item(
            _item("", "x" * 10000), size=2, overlap=1
        )  # 10002 = 0 + "\n\n" + body
    assert len(source_text(_item("", "x" * 10000))) == 10002


def test_exactly_10000_chunks_is_allowed() -> None:
    chunks = chunk_item(_item("x" * 10001), size=2, overlap=1)
    assert len(chunks) == 10000
