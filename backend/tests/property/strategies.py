"""Shared Hypothesis strategies for property tests.

``migration_sets()`` never produces SQL that starts with ``FAIL_MARKER`` (the fake
store's failure injection), so Properties 4 and 5 stay deterministic.
``migration_sets_with_failures()`` always contains at least one such file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from hypothesis import strategies as st

from recromancer.db.runner import Migration
from recromancer.domain.evidence import EvidenceItem, FetchBytes, ItemKind
from recromancer.ingest.bundle import content_hash
from tests.fakes import FAIL_MARKER
from tests.property.invariants import to_crlf

__all__ = [
    "GeneratedBundle",
    "bundles",
    "chunk_params",
    "chunk_texts",
    "evidence_items",
    "fetch_from",
    "long_lf_texts",
    "migration_sets",
    "migration_sets_with_failures",
]

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


# --- evidence-ingestion ---------------------------------------------------------------

_MIXERS = ["\n", "\n\n", " ", "\t", "\u00e9", "\U0001f680"]
_SEPARATORS = ["\n", "\n\n", " ", "\n \n", "\n\n\n"]
_LONG_ALPHABET = string.ascii_letters + " \u00e9\U0001f680"
_ID_FIRST = string.ascii_lowercase + string.digits
_ID_REST = string.ascii_lowercase + string.digits + "._-"
_HANDLE_REST = string.ascii_letters + string.digits + "-"
_SEGMENT = string.ascii_lowercase + string.digits + "_"
_KINDS: tuple[ItemKind, ...] = ("commit", "pull_request", "review", "issue", "doc")
_MAX_ITEMS = 6
_TITLE_MAX = 300

# Any character except surrogates (Cs), NUL, CR (reserved for the line-ending
# tests) and U+FEFF (a leading one would encode to a BOM in a Markdown file).
_TEXT_CHARS = st.characters(
    exclude_categories=["Cs"], exclude_characters="\x00\r\ufeff"
)


@st.composite
def chunk_texts(draw: st.DrawFn, *, min_size: int = 0, max_pieces: int = 12) -> str:
    """Text mixing arbitrary characters with breaks, whitespace and multi-byte runs.

    Never contains a surrogate, NUL, CR or U+FEFF. ``min_size`` is in characters.
    """
    pieces = draw(
        st.lists(
            st.one_of(
                st.text(alphabet=_TEXT_CHARS, max_size=12),
                st.text(alphabet=string.ascii_letters, min_size=1, max_size=12),
                st.sampled_from(_MIXERS),
            ),
            max_size=max_pieces,
        )
    )
    text = "".join(pieces)
    if len(text) < min_size:
        text += draw(st.text(alphabet=string.ascii_letters, min_size=min_size))
    return text


@st.composite
def long_lf_texts(draw: st.DrawFn) -> str:
    """LF-only text of 5000 to 20000 characters, built by repeating a few pieces.

    A handful of small draws (not one huge text draw) keeps generation fast and
    clear of Hypothesis health checks. Contains no CR.
    """
    target = draw(st.integers(5000, 20000))
    pieces = draw(
        st.lists(
            st.text(alphabet=_LONG_ALPHABET, min_size=20, max_size=300),
            min_size=1,
            max_size=6,
        )
    )
    separators = draw(st.lists(st.sampled_from(_SEPARATORS), min_size=1, max_size=6))
    parts: list[str] = []
    length = 0
    index = 0
    while length < target:
        piece = pieces[index % len(pieces)] + separators[index % len(separators)]
        parts.append(piece)
        length += len(piece)
        index += 1
    return "".join(parts)[:target]


@st.composite
def chunk_params(draw: st.DrawFn) -> tuple[int, int]:
    """A valid ``(size, overlap)``: size 1..400 or 1000, overlap in [0, size - 1]."""
    size = draw(st.one_of(st.integers(1, 400), st.just(1000)))
    overlap = draw(st.integers(0, size - 1))
    return size, overlap


def _timestamps() -> st.SearchStrategy[datetime]:
    return st.datetimes(
        min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31)
    ).map(lambda d: d.replace(microsecond=0, tzinfo=UTC))


def _stamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _item_ids() -> st.SearchStrategy[str]:
    return st.builds(
        lambda first, rest: first + rest,
        st.sampled_from(_ID_FIRST),
        st.text(alphabet=_ID_REST, min_size=2, max_size=10),
    )


def _handles() -> st.SearchStrategy[str]:
    return st.builds(
        lambda first, rest: first + rest,
        st.sampled_from(string.ascii_letters + string.digits),
        st.text(alphabet=_HANDLE_REST, max_size=12),
    )


def _titles() -> st.SearchStrategy[str]:
    return chunk_texts(min_size=1).map(lambda t: t[:_TITLE_MAX])


def _rel_path() -> st.SearchStrategy[str]:
    segment = st.text(alphabet=_SEGMENT, min_size=1, max_size=8)
    return st.lists(segment, min_size=1, max_size=3).map("/".join)


def _source() -> st.SearchStrategy[tuple[str | None, str | None]]:
    url = _rel_path().map(lambda p: ("https://example.invalid/" + p, None))
    path = _rel_path().map(lambda p: (None, p))
    return st.one_of(url, path)


@st.composite
def evidence_items(draw: st.DrawFn) -> EvidenceItem:
    """A valid ``EvidenceItem`` built directly, for chunking and hashing tests."""
    url, path = draw(_source())
    draft = EvidenceItem(
        id=draw(_item_ids()),
        kind=draw(st.sampled_from(_KINDS)),
        author=draw(_handles()),
        timestamp=draw(_timestamps()),
        title=draw(_titles()),
        body=draw(chunk_texts()),
        url=url,
        path=path,
        references=tuple(draw(st.lists(_item_ids(), max_size=3))),
        content_hash="",
    )
    return dataclasses.replace(draft, content_hash=content_hash(draft))


def fetch_from(files: dict[str, bytes]) -> FetchBytes:
    """A fake ``fetch`` over an in-memory mapping.

    Mirrors the real loader: returns at most ``max_bytes + 1`` bytes and raises
    ``FileNotFoundError`` for a path that is not in ``files``.
    """

    def fetch(path: str, max_bytes: int) -> bytes:
        try:
            return files[path][: max_bytes + 1]
        except KeyError:
            raise FileNotFoundError(path) from None

    return fetch


@dataclass(frozen=True)
class GeneratedBundle:
    """A generated bundle: its files by path and its item ids in manifest order."""

    files: dict[str, bytes]
    item_ids: tuple[str, ...]
    bundle_id: str
    subject_handle: str

    @property
    def fetch(self) -> Callable[[str, int], bytes]:
        return fetch_from(self.files)


def _dump(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


@st.composite
def bundles(draw: st.DrawFn) -> GeneratedBundle:
    """A valid bundle with 0 to 6 items, serialised to files.

    Subject-authored items of any kind come first; other authors appear only on
    ``review`` items that reference a subject-authored item. Ids are unique, every
    reference resolves, timestamps are whole-second UTC, titles are 1..300
    characters, and about half the texts are written with CRLF line endings.
    """
    handle = draw(_handles())
    bundle_id = draw(_item_ids())
    subject_count = draw(st.integers(0, 4))
    review_count = draw(st.integers(0, min(2, _MAX_ITEMS - subject_count)))
    if subject_count == 0:
        review_count = 0
    total = subject_count + review_count
    ids = draw(st.lists(_item_ids(), min_size=total, max_size=total, unique=True))
    subject_ids = ids[:subject_count]

    files: dict[str, bytes] = {}
    entries: list[dict[str, object]] = []
    for index, item_id in enumerate(ids):
        is_review = index >= subject_count
        kind: ItemKind = "review" if is_review else draw(st.sampled_from(_KINDS))
        author = handle
        if is_review:
            author = draw(_handles().filter(lambda h: h != handle))
        timestamp = _stamp(draw(_timestamps()))
        title = draw(_titles())
        body = draw(chunk_texts())
        crlf = draw(st.booleans())
        if crlf:
            title, body = to_crlf(title), to_crlf(body)
        if kind == "doc":
            path = f"docs/{index:02d}.md"
            entries.append(
                {
                    "path": path,
                    "kind": "doc",
                    "id": item_id,
                    "author": author,
                    "timestamp": timestamp,
                    "title": title,
                }
            )
            files[path] = body.encode("utf-8")
            continue
        path = f"items/{index:02d}.json"
        entries.append({"path": path, "kind": kind})
        refs: list[str] = []
        if is_review:
            refs.append(draw(st.sampled_from(subject_ids)))
        if ids:
            refs.extend(draw(st.lists(st.sampled_from(ids), max_size=3)))
        url, source_path = draw(_source())
        item: dict[str, object] = {
            "id": item_id,
            "kind": kind,
            "author": author,
            "timestamp": timestamp,
            "title": title,
            "body": body,
            "references": list(dict.fromkeys(refs)),
        }
        if url is not None:
            item["url"] = url
        else:
            item["path"] = source_path
        files[path] = _dump(item)

    manifest: dict[str, object] = {
        "bundle_id": bundle_id,
        "schema_version": 1,
        "subject": {"handle": handle, "display_name": draw(_titles())[:60]},
        "fictional": draw(st.booleans()),
        "files": entries,
    }
    files["manifest.json"] = _dump(manifest)
    return GeneratedBundle(
        files=files,
        item_ids=tuple(ids),
        bundle_id=bundle_id,
        subject_handle=handle,
    )
