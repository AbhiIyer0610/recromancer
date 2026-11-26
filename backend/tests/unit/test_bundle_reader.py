"""Bundle_Reader tests over hand-written in-memory bundles (R1, decision 11)."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from recromancer.domain.errors import BundleError
from recromancer.ingest import bundle as bundle_module
from recromancer.ingest.bundle import read_bundle

BODY_SENTINEL = "zqxbodysentinel"
MARKDOWN_SENTINEL = "zqxmarkdownsentinel"

Files = dict[str, bytes]
Mutation = Callable[[Files], None]


class FakeFetch:
    """Serves in-memory files and records every path the reader asks for."""

    def __init__(self, files: Files) -> None:
        self.files = files
        self.calls: list[str] = []

    def __call__(self, path: str, max_bytes: int) -> bytes:
        self.calls.append(path)
        try:
            return self.files[path][: max_bytes + 1]
        except KeyError:
            raise FileNotFoundError(path) from None


def _dump(obj: object) -> bytes:
    return json.dumps(obj).encode()


def valid_files() -> Files:
    manifest = {
        "bundle_id": "demo-bundle",
        "schema_version": 1,
        "subject": {"handle": "ghost", "display_name": "Gus Host"},
        "fictional": True,
        "files": [
            {"path": "items/c1.json", "kind": "commit"},
            {"path": "items/r1.json", "kind": "review"},
            {
                "path": "docs/d1.md",
                "kind": "doc",
                "id": "doc-one",
                "author": "ghost",
                "timestamp": "2026-01-02T03:04:05Z",
                "title": "Design",
            },
        ],
    }
    commit = {
        "id": "commit-one",
        "kind": "commit",
        "author": "ghost",
        "timestamp": "2026-01-01T00:00:00Z",
        "title": "Add thing",
        "body": f"{BODY_SENTINEL} one\r\ntwo",
        "url": "https://example.invalid/c/1",
        "references": [],
    }
    review = {
        "id": "review-one",
        "kind": "review",
        "author": "reviewer",
        "timestamp": "2026-01-03T00:00:00Z",
        "title": "Looks fine",
        "body": f"{BODY_SENTINEL} review",
        "url": "https://example.invalid/r/1",
        "references": ["commit-one"],
    }
    return {
        "manifest.json": _dump(manifest),
        "items/c1.json": _dump(commit),
        "items/r1.json": _dump(review),
        "docs/d1.md": f"# Design\r\n{MARKDOWN_SENTINEL}\rend".encode(),
    }


def edit(name: str, change: Callable[[dict[str, Any]], None]) -> Mutation:
    def apply(files: Files) -> None:
        doc = json.loads(files[name])
        change(doc)
        files[name] = _dump(doc)

    return apply


def put(name: str, data: bytes) -> Mutation:
    def apply(files: Files) -> None:
        files[name] = data

    return apply


def drop(name: str) -> Mutation:
    def apply(files: Files) -> None:
        del files[name]

    return apply


def both(*mutations: Mutation) -> Mutation:
    def apply(files: Files) -> None:
        for m in mutations:
            m(files)

    return apply


def set_key(name: str, key: str, value: object) -> Mutation:
    return edit(name, lambda d: d.__setitem__(key, value))


def del_key(name: str, key: str) -> Mutation:
    return edit(name, lambda d: d.pop(key))


def _error(files: Files) -> tuple[str, FakeFetch]:
    fetch = FakeFetch(files)
    with pytest.raises(BundleError) as err:
        read_bundle(fetch)
    return str(err.value), fetch


def _padded(doc: dict[str, Any], size: int) -> bytes:
    raw = _dump(doc)
    return raw + b" " * (size - len(raw))


# --- valid bundle, doc path, order ---------------------------------------


def test_valid_bundle_items_in_manifest_order_with_normalised_text() -> None:
    bundle = read_bundle(FakeFetch(valid_files()))
    assert bundle.bundle_id == "demo-bundle"
    assert bundle.subject.display_name == "Gus Host"
    assert bundle.fictional is True
    assert [i.id for i in bundle.items] == ["commit-one", "review-one", "doc-one"]
    assert bundle.items[0].body == f"{BODY_SENTINEL} one\ntwo"
    assert bundle.items[1].references == ("commit-one",)


def test_doc_item_takes_metadata_from_manifest_and_body_from_markdown() -> None:
    doc = read_bundle(FakeFetch(valid_files())).items[2]
    assert doc.kind == "doc"
    assert doc.path == "docs/d1.md"
    assert doc.url is None
    assert doc.references == ()
    assert (doc.author, doc.title) == ("ghost", "Design")
    assert doc.timestamp.isoformat() == "2026-01-02T03:04:05+00:00"
    assert doc.body == f"# Design\n{MARKDOWN_SENTINEL}\nend"


def test_items_follow_manifest_order_when_files_are_reversed() -> None:
    def reverse(doc: dict[str, Any]) -> None:
        doc["files"].reverse()

    files = valid_files()
    edit("manifest.json", reverse)(files)
    bundle = read_bundle(FakeFetch(files))
    assert [i.id for i in bundle.items] == ["doc-one", "review-one", "commit-one"]


# --- limits at the limit and at limit + 1 (R1.8) -------------------------


def test_manifest_size_limit() -> None:
    files = valid_files()
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = []
    limit = bundle_module.MANIFEST_MAX_BYTES
    files["manifest.json"] = _padded(manifest, limit)
    assert read_bundle(FakeFetch(files)).items == ()
    files["manifest.json"] = _padded(manifest, limit + 1)
    message, _ = _error(files)
    assert message == "manifest.json: field file: exceeds the size limit"


def test_item_file_size_limit() -> None:
    files = valid_files()
    item = json.loads(files["items/c1.json"])
    limit = bundle_module.ITEM_MAX_BYTES
    files["items/c1.json"] = _padded(item, limit)
    assert len(read_bundle(FakeFetch(files)).items) == 3
    files["items/c1.json"] = _padded(item, limit + 1)
    message, _ = _error(files)
    assert message == "items/c1.json: field file: exceeds the size limit"


def test_markdown_size_limit() -> None:
    files = valid_files()
    limit = bundle_module.ITEM_MAX_BYTES
    files["docs/d1.md"] = b"x" * bundle_module.BODY_MAX
    assert len(read_bundle(FakeFetch(files)).items[2].body) == bundle_module.BODY_MAX
    files["docs/d1.md"] = b"x" * (limit + 1)
    message, _ = _error(files)
    assert message == "docs/d1.md: field file: exceeds the size limit"


def _many_files(count: int) -> Files:
    entries = [{"path": f"i/{n}.json", "kind": "commit"} for n in range(count)]
    manifest = {
        "bundle_id": "big-bundle",
        "schema_version": 1,
        "subject": {"handle": "ghost", "display_name": "G"},
        "fictional": True,
        "files": entries,
    }
    files: Files = {
        "manifest.json": json.dumps(manifest, separators=(",", ":")).encode()
    }
    for n in range(count):
        files[f"i/{n}.json"] = _dump(
            {
                "id": f"item-{n}",
                "kind": "commit",
                "author": "ghost",
                "timestamp": "2026-01-01T00:00:00Z",
                "title": "t",
                "body": "",
                "url": "https://example.invalid/x",
                "references": [],
            }
        )
    return files


def test_file_count_limit() -> None:
    limit = bundle_module.MAX_FILES
    assert len(read_bundle(FakeFetch(_many_files(limit))).items) == limit
    message, fetch = _error(_many_files(limit + 1))
    assert message == "manifest.json: field files: exceeds the file count limit"
    assert fetch.calls == ["manifest.json"]


@pytest.mark.parametrize(
    ("field", "limit", "reason"),
    [
        ("title", bundle_module.TITLE_MAX, "exceeds the length limit"),
        ("body", bundle_module.BODY_MAX, "exceeds the length limit"),
    ],
)
def test_text_length_limits(field: str, limit: int, reason: str) -> None:
    files = valid_files()
    set_key("items/c1.json", field, "a" * limit)(files)
    read_bundle(FakeFetch(files))
    set_key("items/c1.json", field, "a" * (limit + 1))(files)
    message, _ = _error(files)
    assert message == f"items/c1.json: field {field}: {reason}"


def test_references_limit() -> None:
    limit = bundle_module.REFERENCES_MAX
    files = valid_files()
    set_key("items/c1.json", "references", ["commit-one"] * limit)(files)
    read_bundle(FakeFetch(files))
    set_key("items/c1.json", "references", ["commit-one"] * (limit + 1))(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field references: exceeds the entry limit"


# --- first violation wins (R1.13) ----------------------------------------


def test_manifest_violation_is_reported_before_item_violations() -> None:
    files = valid_files()
    both(
        set_key("manifest.json", "extra", 1),
        put("items/c1.json", b"{not json"),
    )(files)
    message, fetch = _error(files)
    assert message.startswith("manifest.json: field 'extra':")
    assert fetch.calls == ["manifest.json"]


def test_manifest_path_rule_is_checked_before_any_item_file_is_fetched() -> None:
    def bad_second_path(doc: dict[str, Any]) -> None:
        doc["files"][1]["path"] = "../escape.json"

    files = valid_files()
    both(
        edit("manifest.json", bad_second_path),
        put("items/c1.json", b"{not json"),
    )(files)
    message, fetch = _error(files)
    assert message.startswith("manifest.json: field files[1].path:")
    assert fetch.calls == ["manifest.json"]


def test_first_item_file_in_manifest_order_is_reported() -> None:
    files = valid_files()
    both(put("items/c1.json", b"{not json"), put("items/r1.json", b"\xff"))(files)
    message, fetch = _error(files)
    assert message.startswith("items/c1.json: field file:")
    assert fetch.calls == ["manifest.json", "items/c1.json"]

    def swap(doc: dict[str, Any]) -> None:
        doc["files"][0], doc["files"][1] = doc["files"][1], doc["files"][0]

    edit("manifest.json", swap)(files)
    message, _ = _error(files)
    assert message.startswith("items/r1.json: field file:")


def test_item_file_violation_is_reported_before_cross_item_violation() -> None:
    files = valid_files()
    both(
        set_key("items/c1.json", "references", ["nope-id"]),
        put("docs/d1.md", b"\xef\xbb\xbfbom"),
    )(files)
    message, _ = _error(files)
    assert message.startswith("docs/d1.md: field file:")


def test_unknown_field_is_reported_before_missing_field() -> None:
    files = valid_files()
    both(set_key("items/c1.json", "extra", 1), del_key("items/c1.json", "title"))(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field 'extra': unknown field"


# --- decision 11 rules, one example each ---------------------------------


def test_d11_duplicate_json_keys_rejected() -> None:
    files = valid_files()
    put("items/c1.json", b'{"id":"aaa","id":"bbb"}')(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field file: contains a duplicate key"


def test_d11_nan_rejected() -> None:
    files = valid_files()
    files["items/c1.json"] = files["items/c1.json"].replace(
        b'"references": []', b'"references": [], "x": NaN'
    )
    message, _ = _error(files)
    assert message == "items/c1.json: field file: contains a non-finite number"


def test_d11_duplicate_manifest_path_rejected() -> None:
    def duplicate(doc: dict[str, Any]) -> None:
        doc["files"][1]["path"] = "items/c1.json"

    files = valid_files()
    edit("manifest.json", duplicate)(files)
    message, fetch = _error(files)
    assert message == "manifest.json: field files[1].path: is listed more than once"
    assert fetch.calls == ["manifest.json"]


def test_d11_nul_in_text_rejected() -> None:
    files = valid_files()
    set_key("items/c1.json", "body", "a\x00b")(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field body: contains a NUL character"


def test_d11_lone_surrogate_rejected() -> None:
    files = valid_files()
    files["items/c1.json"] = files["items/c1.json"].replace(
        b'"Add thing"', b'"Add \\ud800 thing"'
    )
    message, _ = _error(files)
    assert message == "items/c1.json: field title: contains an unpaired surrogate"


def test_d11_subject_handle_must_match_author_pattern() -> None:
    def bad_handle(doc: dict[str, Any]) -> None:
        doc["subject"]["handle"] = "-bad handle"

    files = valid_files()
    edit("manifest.json", bad_handle)(files)
    message, _ = _error(files)
    assert (
        message == "manifest.json: field subject.handle: is not a valid author handle"
    )


def test_d11_item_path_follows_manifest_path_rule() -> None:
    files = valid_files()
    both(del_key("items/c1.json", "url"), set_key("items/c1.json", "path", "../x"))(
        files
    )
    message, _ = _error(files)
    assert message == "items/c1.json: field path: contains a relative segment"


@pytest.mark.parametrize("url", ["https://", "https:///only/path"])
def test_d11_url_needs_hostname(url: str) -> None:
    files = valid_files()
    set_key("items/c1.json", "url", url)(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field url: must have a host name"


def test_d11_empty_files_array_is_allowed() -> None:
    files = valid_files()
    set_key("manifest.json", "files", [])(files)
    assert read_bundle(FakeFetch(files)).items == ()


def test_d11_length_is_measured_after_line_ending_normalisation() -> None:
    files = valid_files()
    set_key("items/c1.json", "body", "\r\n" * bundle_module.BODY_MAX)(files)
    body = read_bundle(FakeFetch(files)).items[0].body
    assert body == "\n" * bundle_module.BODY_MAX
    set_key("items/c1.json", "body", "\n" * (bundle_module.BODY_MAX + 1))(files)
    message, _ = _error(files)
    assert message == "items/c1.json: field body: exceeds the length limit"


def test_d11_unlisted_file_is_never_fetched() -> None:
    files = valid_files()
    files["items/unlisted.json"] = b"{not json"
    fetch = FakeFetch(files)
    read_bundle(fetch)
    assert sorted(fetch.calls) == sorted(
        ["manifest.json", "items/c1.json", "items/r1.json", "docs/d1.md"]
    )


# --- EX-VIOLATIONS --------------------------------------------------------

C1 = "items/c1.json"
R1 = "items/r1.json"
D1 = "docs/d1.md"
MAN = "manifest.json"


def _long_title(doc: dict[str, Any]) -> None:
    doc["title"] = "t" * (bundle_module.TITLE_MAX + 1)


def _bad_path(doc: dict[str, Any]) -> None:
    doc["files"][0]["path"] = "a/../b.json"


VIOLATIONS: list[tuple[str, Mutation, str, str]] = [
    ("unknown manifest field", set_key(MAN, "extra", 1), MAN, "'extra'"),
    ("unknown item field", set_key(C1, "extra", 1), C1, "'extra'"),
    ("wrong kind", set_key(C1, "kind", "bogus"), C1, "kind"),
    ("kind differs from manifest", set_key(C1, "kind", "issue"), C1, "kind"),
    ("bad item id", set_key(C1, "id", "X"), C1, "id"),
    ("bad author", set_key(C1, "author", "-bad"), C1, "author"),
    (
        "bad timestamp",
        set_key(C1, "timestamp", "2026-13-01T00:00:00Z"),
        C1,
        "timestamp",
    ),
    ("url and path both", set_key(C1, "path", "a/b.txt"), C1, "url"),
    ("url and path neither", del_key(C1, "url"), C1, "url"),
    ("non-https url", set_key(C1, "url", "http://example.invalid/x"), C1, "url"),
    ("duplicate id", set_key(R1, "id", "commit-one"), R1, "id"),
    ("dangling reference", set_key(C1, "references", ["nope-id"]), C1, "references"),
    (
        "non-subject author on non-review",
        set_key(C1, "author", "stranger"),
        C1,
        "author",
    ),
    ("review without subject reference", set_key(R1, "references", []), R1, "author"),
    ("missing file", drop(C1), C1, "file"),
    ("byte order mark", put(C1, b"\xef\xbb\xbf{}"), C1, "file"),
    ("invalid utf-8", put(C1, b"\xff\xfe\xfd"), C1, "file"),
    ("invalid json", put(C1, b"{not json"), C1, "file"),
    ("bad markdown utf-8", put(D1, b"\xc3\x28"), D1, "file"),
    ("unsafe path", edit(MAN, _bad_path), MAN, "files[0].path"),
    ("title too long", edit(C1, _long_title), C1, "title"),
    (
        "too many references",
        set_key(C1, "references", ["commit-one"] * (bundle_module.REFERENCES_MAX + 1)),
        C1,
        "references",
    ),
]


@pytest.mark.parametrize(
    ("mutation", "file", "field"),
    [pytest.param(m, f, fld, id=name) for name, m, f, fld in VIOLATIONS],
)
def test_ex_violations(mutation: Mutation, file: str, field: str) -> None:
    files = valid_files()
    mutation(files)
    message, _ = _error(files)
    assert message.startswith(f"{file}: field {field}:")
    assert BODY_SENTINEL not in message
    assert MARKDOWN_SENTINEL not in message


def test_error_messages_never_contain_body_text() -> None:
    # Break the files that hold the sentinels, so the reader has the text in hand.
    broken: list[Mutation] = [
        set_key(C1, "author", "stranger"),
        set_key(R1, "url", "http://example.invalid/x"),
        set_key(R1, "references", ["nope-id"]),
        put(D1, f"{MARKDOWN_SENTINEL}\x00".encode()),
        put(D1, MARKDOWN_SENTINEL.encode() + b"\xff"),
        put(C1, f'{{"body":"{BODY_SENTINEL}",'.encode()),
        put(C1, f'{{"body":"{BODY_SENTINEL}","body":"x"}}'.encode()),
        put(C1, f'{{"body":"{BODY_SENTINEL}\\u0000"}}'.encode()),
        set_key(C1, "body", BODY_SENTINEL + "a" * bundle_module.BODY_MAX),
    ]
    for mutation in broken:
        files = valid_files()
        mutation(files)
        message, _ = _error(files)
        assert BODY_SENTINEL not in message
        assert MARKDOWN_SENTINEL not in message
