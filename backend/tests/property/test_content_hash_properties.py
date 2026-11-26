"""Property tests for ``content_hash`` through ``read_bundle`` (evidence-ingestion)."""

from __future__ import annotations

import dataclasses
import json
import string
from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from recromancer.ingest.bundle import read_bundle
from tests.property.invariants import to_crlf, to_lone_cr
from tests.property.strategies import fetch_from

_ISSUE = "item-issue"
_COMMIT = "item-commit"
_DOC = "item-doc"
_FIELDS = ("title", "body", "author", "timestamp", "references")
_STYLES = ("lf", "crlf", "cr", "raw")

# Boundary texts: empty, ending in a lone CR, "\r\r\n", CRLF only, mixed endings.
_BOUNDARY = ["", "\r", "a\r", "\r\r\n", "a\r\r\nb", "\r\n", "a\r\n\r\nb", "\n\r"]
_ALPHABET = "ab \n\r\u00e9"


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _texts(*, min_norm: int = 0) -> st.SearchStrategy[str]:
    generated = st.text(alphabet=_ALPHABET, max_size=30)
    return st.one_of(generated, generated, st.sampled_from(_BOUNDARY)).filter(
        lambda t: len(_normalise(t)) >= min_norm
    )


def _handles() -> st.SearchStrategy[str]:
    return st.text(
        alphabet=string.ascii_letters + string.digits, min_size=1, max_size=8
    )


def _timestamps() -> st.SearchStrategy[datetime]:
    return st.datetimes(
        min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31)
    ).map(lambda d: d.replace(microsecond=0, tzinfo=UTC))


@dataclasses.dataclass(frozen=True)
class _Content:
    title: str
    body: str
    author: str
    timestamp: datetime
    references: tuple[str, ...]


def _contents() -> st.SearchStrategy[_Content]:
    return st.builds(
        _Content,
        title=_texts(min_norm=1),
        body=_texts(),
        author=_handles(),
        timestamp=_timestamps(),
        references=st.sampled_from([(), (_ISSUE,)]),
    )


@st.composite
def _changes(draw: st.DrawFn) -> tuple[_Content, str, _Content]:
    """A base content, the field to change, and content with that field changed.

    The new value always differs from the original after normalisation.
    """
    base = draw(_contents())
    field = draw(st.sampled_from(_FIELDS))
    if field == "title":
        title = draw(
            _texts(min_norm=1).filter(lambda v: _normalise(v) != _normalise(base.title))
        )
        return base, field, dataclasses.replace(base, title=title)
    if field == "body":
        body = draw(_texts().filter(lambda v: _normalise(v) != _normalise(base.body)))
        return base, field, dataclasses.replace(base, body=body)
    if field == "author":
        author = draw(_handles().filter(lambda v: v != base.author))
        return base, field, dataclasses.replace(base, author=author)
    if field == "timestamp":
        ts = draw(_timestamps().filter(lambda v: v != base.timestamp))
        return base, field, dataclasses.replace(base, timestamp=ts)
    refs = draw(st.sampled_from([(), (_ISSUE,)]).filter(lambda v: v != base.references))
    return base, field, dataclasses.replace(base, references=refs)


def _styled(text: str, style: str) -> str:
    plain = _normalise(text)
    if style == "lf":
        return plain
    if style == "crlf":
        return to_crlf(plain)
    if style == "cr":
        return to_lone_cr(plain)
    return text


def _stamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hashes(content: _Content, style: str) -> tuple[tuple[object, ...], dict[str, str]]:
    """Read a bundle with a commit, an issue and a doc; return the items and hashes.

    The subject is the content's author, so every item stays subject-authored.
    """
    title = _styled(content.title, style)
    body = _styled(content.body, style)
    stamp = _stamp(content.timestamp)

    def json_item(item_id: str, kind: str, refs: tuple[str, ...]) -> bytes:
        item = {
            "id": item_id,
            "kind": kind,
            "author": content.author,
            "timestamp": stamp,
            "title": title,
            "body": body,
            "url": f"https://example.invalid/{item_id}",
            "references": list(refs),
        }
        return json.dumps(item).encode("utf-8")

    files = {
        "items/commit.json": json_item(_COMMIT, "commit", content.references),
        "items/issue.json": json_item(_ISSUE, "issue", ()),
        "docs/doc.md": body.encode("utf-8"),
    }
    manifest = {
        "bundle_id": "bundle-x",
        "schema_version": 1,
        "subject": {"handle": content.author, "display_name": "Subject"},
        "fictional": True,
        "files": [
            {"path": "items/commit.json", "kind": "commit"},
            {"path": "items/issue.json", "kind": "issue"},
            {
                "path": "docs/doc.md",
                "kind": "doc",
                "id": _DOC,
                "author": content.author,
                "timestamp": stamp,
                "title": title,
            },
        ],
    }
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    bundle = read_bundle(fetch_from(files))
    return bundle.items, {item.id: item.content_hash for item in bundle.items}


# Feature: evidence-ingestion, Property 3: Content_Hash ignores line-ending style and reacts to every content field  # noqa: E501
# **Validates: Requirements 1.10, 8.4**
#
# Mutation_Checks (R8.5, R8.6), both run against ingest/bundle.py and reverted:
#  (a) `_normalise` returns its input unchanged: the LF versus CRLF/CR/raw reads
#      differ, so the line-ending equality assertions fail.
#  (b) `content_hash` hashes only `title` and `body`: the sensitivity half fails
#      for `author`, `timestamp` or `references`.
# Assertions, falsifying examples and file hashes: tasks.md, task 1.14 notes.
@given(change=_changes(), style=st.sampled_from(_STYLES[1:]))
def test_content_hash_ignores_line_endings_and_reacts_to_changes(
    change: tuple[_Content, str, _Content], style: str
) -> None:
    base, field, changed = change

    # Line-ending half: LF and the other style read to equal items and hashes.
    items_lf, hashes_lf = _hashes(base, "lf")
    items_other, hashes_other = _hashes(base, style)
    assert items_lf == items_other, "items differ between LF and other line endings"
    assert hashes_lf == hashes_other, "hashes differ between LF and other endings"

    # Sensitivity half: one changed field changes the hash.
    _, after = _hashes(changed, style)
    assert hashes_other[_COMMIT] != after[_COMMIT], f"commit hash ignores {field}"
    if field != "references":
        assert hashes_other[_ISSUE] != after[_ISSUE], f"issue hash ignores {field}"
        assert hashes_other[_DOC] != after[_DOC], f"doc hash ignores {field}"
