"""Reusable invariant checks for property tests. Contains no test functions.

Failure messages never include the secret or the inspected text, so a failing run
cannot leak either into pytest or Hypothesis output.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from urllib.parse import quote

from recromancer.domain.errors import ErrorCode
from recromancer.domain.evidence import Chunk, EvidenceItem
from tests.fakes import EvidenceSnapshot, Snapshot


def secret_forms(secret: str) -> dict[str, str]:
    """Named textual forms in which a secret could appear in observable output."""
    return {
        "raw": secret,
        "json-ascii": json.dumps(secret, ensure_ascii=True)[1:-1],
        "json-unicode": json.dumps(secret, ensure_ascii=False)[1:-1],
        "percent": quote(secret),
        "percent-strict": quote(secret, safe=""),
    }


def assert_secret_absent(secret: str, *texts: str) -> None:
    """Assert that no form of ``secret`` occurs in any of ``texts``.

    An empty secret is skipped. The error names the matching form and the text index
    only.
    """
    if not secret:
        return
    forms = secret_forms(secret)
    for index, text in enumerate(texts):
        for name, form in forms.items():
            if form and form in text:
                raise AssertionError(f"form {name!r} present in text #{index}")


_REQUEST_ID_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)


def is_valid_request_id(value: str) -> bool:
    """Regex-free oracle: 8 to 64 characters, each in ``A-Z a-z 0-9 . _ -``."""
    return 8 <= len(value) <= 64 and all(ch in _REQUEST_ID_ALPHABET for ch in value)


def assert_request_id_valid(header: str | None, result: str, generated: str) -> None:
    """Assert request-id resolution returned the right one of its two allowed forms.

    ``generated`` is the generator's output. The header is attacker-controlled, so
    messages report lengths and the failed clause only.
    """
    if header is not None and is_valid_request_id(header):
        if result != header:
            raise AssertionError(
                f"valid header (len {len(header)}) not returned unchanged "
                f"(result len {len(result)})"
            )
    else:
        header_len = None if header is None else len(header)
        if result != generated:
            raise AssertionError(
                f"invalid or absent header (len {header_len}) did not yield the "
                f"generated id (result len {len(result)})"
            )
        parsed = uuid.UUID(generated)
        if parsed.version != 4 or str(parsed) != generated:
            raise AssertionError("generated id is not a canonical UUID4 string")
    if not is_valid_request_id(result):
        raise AssertionError(f"result (len {len(result)}) is not a valid request id")


def _member_name(member: object) -> str:
    if isinstance(member, ErrorCode):
        return member.name
    return f"<{type(member).__name__}>"


def assert_mapping_total(
    mapping: Mapping[ErrorCode, int], codes: Iterable[ErrorCode]
) -> None:
    """Assert `mapping` covers exactly `codes` and every status is valid.

    A valid status is an int (not a bool) in 400..599 that `http.HTTPStatus`
    defines. Messages name the code and the status number only.
    """
    expected = set(codes)
    actual = set(mapping)
    missing = sorted(_member_name(c) for c in expected - actual)
    extra = sorted(_member_name(c) for c in actual - expected)
    if missing or extra:
        raise AssertionError(f"key set differs: missing {missing}, extra {extra}")
    for code, status in mapping.items():
        name = _member_name(code)
        if isinstance(status, bool) or not isinstance(status, int):
            raise AssertionError(f"{name}: status is not an int")
        if not 400 <= status <= 599:
            raise AssertionError(f"{name}: status {status} outside 400..599")
        try:
            HTTPStatus(status)
        except ValueError:
            raise AssertionError(
                f"{name}: status {status} is not a defined HTTP status"
            ) from None


def assert_same_state(a: Snapshot, b: Snapshot) -> None:
    """Assert two ``FakeMigrationStore.snapshot()`` values are identical.

    Compares the executed-SQL history and the recorded entries (name, checksum,
    applied_at). Messages report positions and file names only, never SQL text.
    """
    history_a, recorded_a = a
    history_b, recorded_b = b
    if len(history_a) != len(history_b):
        raise AssertionError(
            f"history length differs: {len(history_a)} vs {len(history_b)}"
        )
    for index, (sql_a, sql_b) in enumerate(zip(history_a, history_b, strict=True)):
        if sql_a != sql_b:
            raise AssertionError(f"history differs first at index {index}")
    by_name_a = {name: (checksum, at) for name, checksum, at in recorded_a}
    by_name_b = {name: (checksum, at) for name, checksum, at in recorded_b}
    added = sorted(set(by_name_b) - set(by_name_a))
    removed = sorted(set(by_name_a) - set(by_name_b))
    changed = sorted(
        name
        for name in set(by_name_a) & set(by_name_b)
        if by_name_a[name] != by_name_b[name]
    )
    if added or removed or changed:
        raise AssertionError(
            f"recorded differs: added {added}, removed {removed}, changed {changed}"
        )
    if recorded_a != recorded_b:
        raise AssertionError("recorded entries differ in order")


# --- evidence-ingestion: chunking, chunk ids, line endings, store snapshots ---------

_ORACLE_MAX_WINDOW = 200


def assert_lossless_cover(text: str, chunks: Sequence[Chunk], overlap: int) -> None:
    """Assert ``chunks`` cover ``text`` with no loss and exact overlaps.

    Checks that each chunk's text equals its slice, the first start is 0, the last
    end is ``len(text)``, starts strictly increase, each next chunk starts at
    ``previous end - overlap``, the pieces reassemble to ``text`` and the empty text
    gives no chunks. Messages carry indices and numbers only, never text.
    """
    if text == "":
        if chunks:
            raise AssertionError(f"empty text gave {len(chunks)} chunks")
        return
    if not chunks:
        raise AssertionError(f"non-empty text (len {len(text)}) gave no chunks")
    previous_start = -1
    previous_end = -1
    for index, chunk in enumerate(chunks):
        start = chunk.locator.start_offset
        end = chunk.locator.end_offset
        if not 0 <= start < end <= len(text):
            raise AssertionError(f"chunk {index}: bad offsets {start}..{end}")
        if chunk.text != text[start:end]:
            raise AssertionError(f"chunk {index}: text differs from its slice")
        if index == 0:
            if start != 0:
                raise AssertionError(f"first chunk starts at {start}, not 0")
        else:
            if start <= previous_start:
                raise AssertionError(f"chunk {index}: start does not increase")
            if start != previous_end - overlap:
                raise AssertionError(
                    f"chunk {index}: start {start} != previous end {previous_end} "
                    f"- overlap {overlap}"
                )
        previous_start, previous_end = start, end
    if previous_end != len(text):
        raise AssertionError(f"last end {previous_end} != text length {len(text)}")
    rebuilt = chunks[0].text + "".join(c.text[overlap:] for c in chunks[1:])
    if rebuilt != text:
        raise AssertionError("chunks do not reassemble to the text")


def expected_chunk_end(text: str, start: int, size: int, overlap: int) -> int:
    """Independent oracle for the end of a NON-final chunk that starts at ``start``.

    Scans with explicit loops from ``hi`` downward; it does not use ``str.rfind``.
    ``lo = start + size - window``, ``hi = start + size``,
    ``window = min(200, size - overlap - 1)``. The result is the offset just after
    the last paragraph break ``"\\n\\n"`` lying entirely inside ``[lo, hi]``, else
    just after the last ``"\\n"`` at an index in ``[lo, hi)``, else just after the
    last whitespace character at an index in ``[lo, hi)``, else ``hi``.
    """
    window = min(_ORACLE_MAX_WINDOW, size - overlap - 1)
    lo = start + size - window
    hi = start + size
    k = hi - 2
    while k >= lo:
        if text[k] == "\n" and text[k + 1] == "\n":
            return k + 2
        k -= 1
    k = hi - 1
    while k >= lo:
        if text[k] == "\n":
            return k + 1
        k -= 1
    k = hi - 1
    while k >= lo:
        if text[k].isspace():
            return k + 1
        k -= 1
    return hi


def assert_boundary_rule(
    text: str, chunks: Sequence[Chunk], size: int, overlap: int
) -> None:
    """Assert every chunk end follows the paragraph, line, whitespace, hard-cut rule.

    Replays the chunk sequence with ``expected_chunk_end``. The final chunk is the
    first one for which ``len(text) - start <= size`` and it ends at ``len(text)``.
    """
    n = len(text)
    if n == 0:
        if chunks:
            raise AssertionError(f"empty text gave {len(chunks)} chunks")
        return
    start = 0
    for index, chunk in enumerate(chunks):
        if chunk.locator.start_offset != start:
            raise AssertionError(
                f"chunk {index}: start {chunk.locator.start_offset} != expected {start}"
            )
        end = chunk.locator.end_offset
        if n - start <= size:
            if end != n:
                raise AssertionError(f"chunk {index}: final end {end} != {n}")
            if index != len(chunks) - 1:
                raise AssertionError(f"chunk {index}: chunks continue after the end")
            return
        expected = expected_chunk_end(text, start, size, overlap)
        if end != expected:
            raise AssertionError(f"chunk {index}: end {end} != expected {expected}")
        start = end - overlap
    raise AssertionError("chunks stop before a final chunk")


def assert_chunk_ids(item_id: str, chunks: Sequence[Chunk]) -> None:
    """Assert ids are ``<item_id>#<position:04d>`` with positions 0..n-1 in order."""
    for index, chunk in enumerate(chunks):
        if chunk.position != index:
            raise AssertionError(f"chunk {index}: position is {chunk.position}")
        if chunk.chunk_id != f"{item_id}#{index:04d}":
            raise AssertionError(f"chunk {index}: malformed chunk id")
        if chunk.item_id != item_id:
            raise AssertionError(f"chunk {index}: wrong item id")


def assert_chunk_metadata(item: EvidenceItem, chunks: Sequence[Chunk]) -> None:
    """Assert each chunk carries the item's kind, author, timestamp and source."""
    for index, chunk in enumerate(chunks):
        loc = chunk.locator
        same = (
            chunk.kind == item.kind
            and chunk.author == item.author
            and chunk.timestamp == item.timestamp
            and loc.item_id == item.id
            and loc.url == item.url
            and loc.path == item.path
        )
        if not same:
            raise AssertionError(f"chunk {index}: metadata differs from its item")


def to_crlf(text: str) -> str:
    """Rewrite every LF as CRLF. ``text`` must not already contain CR."""
    if "\r" in text:
        raise ValueError("text already contains CR")
    return text.replace("\n", "\r\n")


def to_lone_cr(text: str) -> str:
    """Rewrite every LF as a lone CR. ``text`` must not already contain CR."""
    if "\r" in text:
        raise ValueError("text already contains CR")
    return text.replace("\n", "\r")


_BLANK_INGESTED_AT = datetime(1970, 1, 1, tzinfo=UTC)


def snapshot_without_ingested_at(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    """Return ``snapshot`` with every stored item's ``ingested_at`` blanked.

    Lets two stores be compared on content alone.
    """
    items, chunks = snapshot
    blanked = tuple(
        (
            ws,
            bundle,
            item_id,
            dataclasses.replace(stored, ingested_at=_BLANK_INGESTED_AT),
        )
        for ws, bundle, item_id, stored in items
    )
    return (blanked, chunks)
