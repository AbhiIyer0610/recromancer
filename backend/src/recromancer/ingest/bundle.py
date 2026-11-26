"""Bundle_Reader: validates an Evidence_Bundle and returns Evidence_Items.

Pure: every byte comes through the injected ``fetch`` callable, so this module
does no I/O. Error messages have the form ``<file>: field <field>: <reason>``.
Only field names that came from a file are echoed (through ``ascii(name)[:40]``);
no value, title, body or Markdown text is ever included.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Final, NoReturn, cast
from urllib.parse import urlsplit

from recromancer.domain.errors import BundleError
from recromancer.domain.evidence import (
    Bundle,
    EvidenceItem,
    FetchBytes,
    ItemKind,
    Subject,
)

MANIFEST_NAME: Final = "manifest.json"
MANIFEST_MAX_BYTES: Final = 262_144
ITEM_MAX_BYTES: Final = 1_048_576
MAX_FILES: Final = 5000
TITLE_MAX: Final = 300
BODY_MAX: Final = 200_000
REFERENCES_MAX: Final = 100

ITEM_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
AUTHOR_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
TIMESTAMP_PATTERN: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
_DRIVE_PATTERN: Final = re.compile(r"[A-Za-z]:")

_KINDS: Final[tuple[ItemKind, ...]] = (
    "commit",
    "pull_request",
    "review",
    "issue",
    "doc",
)
_BOM: Final = b"\xef\xbb\xbf"

_MANIFEST_FIELDS: Final = (
    "bundle_id",
    "schema_version",
    "subject",
    "fictional",
    "files",
)
_SUBJECT_FIELDS: Final = ("handle", "display_name")
_ENTRY_FIELDS: Final = ("path", "kind")
_DOC_ENTRY_FIELDS: Final = ("path", "kind", "id", "author", "timestamp", "title")
_ITEM_REQUIRED: Final = (
    "id",
    "kind",
    "author",
    "timestamp",
    "title",
    "body",
    "references",
)
_ITEM_ALLOWED: Final = (*_ITEM_REQUIRED, "url", "path")


def _fname(path: str) -> str:
    """File name for messages: escaped and bounded, never raw."""
    return ascii(path)[1:-1][:120]


def _fail(file: str, field: str, reason: str) -> NoReturn:
    raise BundleError(f"{file}: field {field}: {reason}")


def _key(name: str) -> str:
    return ascii(name)[:40]


def _decode(raw: bytes, max_bytes: int, file: str) -> str:
    if len(raw) > max_bytes:
        _fail(file, "file", "exceeds the size limit")
    if raw.startswith(_BOM):
        _fail(file, "file", "starts with a byte order mark")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail(file, "file", "is not valid UTF-8")


def _fetch(fetch: FetchBytes, path: str, max_bytes: int, file: str) -> bytes:
    try:
        return fetch(path, max_bytes)
    except BundleError:
        raise
    except OSError:
        _fail(file, "file", "is missing or unreadable")


def _parse_json(text: str, file: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for k, v in items:
            if k in out:
                _fail(file, "file", "contains a duplicate key")
            out[k] = v
        return out

    def constant(_name: str) -> NoReturn:
        _fail(file, "file", "contains a non-finite number")

    try:
        return cast(
            object,
            json.loads(text, object_pairs_hook=pairs, parse_constant=constant),
        )
    except (ValueError, RecursionError):
        _fail(file, "file", "is not valid JSON")


def _object(
    value: object,
    file: str,
    field: str,
    allowed: tuple[str, ...],
    required: tuple[str, ...],
) -> dict[str, object]:
    """Object checks in order: type, unknown field, missing field."""
    if not isinstance(value, dict):
        _fail(file, field, "must be an object")
    obj = cast(dict[str, object], value)
    prefix = f"{field}." if field else ""
    for name in obj:
        if name not in allowed:
            _fail(file, f"{prefix}{_key(name)}", "unknown field")
    for name in required:
        if name not in obj:
            _fail(file, f"{prefix}{name}", "missing field")
    return obj


def _normalise(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _text(
    value: object,
    file: str,
    field: str,
    max_len: int,
    *,
    non_empty: bool = False,
) -> str:
    """Normalise line endings, then check type, length, NUL and encodability."""
    if not isinstance(value, str):
        _fail(file, field, "must be a string")
    s = _normalise(value)
    if non_empty and not s:
        _fail(file, field, "must not be empty")
    if len(s) > max_len:
        _fail(file, field, "exceeds the length limit")
    _require_storable(s, file, field)
    return s


def _require_storable(s: str, file: str, field: str) -> None:
    if "\x00" in s:
        _fail(file, field, "contains a NUL character")
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        _fail(file, field, "contains an unpaired surrogate")


def _item_id(value: object, file: str, field: str) -> str:
    if not isinstance(value, str) or ITEM_ID_PATTERN.fullmatch(value) is None:
        _fail(file, field, "is not a valid item id")
    return value


def _author(value: object, file: str, field: str) -> str:
    if not isinstance(value, str) or AUTHOR_PATTERN.fullmatch(value) is None:
        _fail(file, field, "is not a valid author handle")
    return value


def _timestamp(value: object, file: str, field: str) -> datetime:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.fullmatch(value) is None:
        _fail(file, field, "is not a UTC timestamp of the form YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail(file, field, "is not a valid date and time")


def _kind(value: object, file: str, field: str) -> ItemKind:
    for kind in _KINDS:
        if isinstance(value, str) and value == kind:
            return kind
    _fail(file, field, "is not a valid item kind")


def _safe_path(value: object, file: str, field: str) -> str:
    """The bundle-relative path rule (R1.12), also applied to an item's `path`."""
    if not isinstance(value, str):
        _fail(file, field, "must be a string")
    if not value:
        _fail(file, field, "must not be empty")
    if "\\" in value:
        _fail(file, field, "contains a backslash")
    if "\x00" in value:
        _fail(file, field, "contains a NUL character")
    if value.startswith("/") or _DRIVE_PATTERN.match(value) is not None:
        _fail(file, field, "must not be absolute")
    for segment in value.split("/"):
        if segment == "":
            _fail(file, field, "contains an empty segment")
        if segment in (".", ".."):
            _fail(file, field, "contains a relative segment")
    _require_storable(value, file, field)
    return value


def _url(value: object, file: str, field: str) -> str:
    if not isinstance(value, str):
        _fail(file, field, "must be a string")
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        _fail(file, field, "is not a valid URL")
    if parts.scheme != "https":
        _fail(file, field, "must use the https scheme")
    if not host:
        _fail(file, field, "must have a host name")
    _require_storable(value, file, field)
    return value


class _Entry:
    __slots__ = ("author", "id", "kind", "path", "timestamp", "title")

    def __init__(
        self,
        path: str,
        kind: ItemKind,
        id: str | None,
        author: str | None,
        timestamp: datetime | None,
        title: str | None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.id = id
        self.author = author
        self.timestamp = timestamp
        self.title = title


def _check_entry(value: object, index: int) -> _Entry:
    field = f"files[{index}]"
    file = MANIFEST_NAME
    if not isinstance(value, dict):
        _fail(file, field, "must be an object")
    raw = cast(dict[str, object], value)
    is_doc = raw.get("kind") == "doc"
    allowed = _DOC_ENTRY_FIELDS if is_doc else _ENTRY_FIELDS
    obj = _object(raw, file, field, allowed, allowed)
    path = _safe_path(obj["path"], file, f"{field}.path")
    kind = _kind(obj["kind"], file, f"{field}.kind")
    if kind != "doc":
        return _Entry(path, kind, None, None, None, None)
    return _Entry(
        path,
        kind,
        _item_id(obj["id"], file, f"{field}.id"),
        _author(obj["author"], file, f"{field}.author"),
        _timestamp(obj["timestamp"], file, f"{field}.timestamp"),
        _text(obj["title"], file, f"{field}.title", TITLE_MAX, non_empty=True),
    )


def _check_manifest(doc: object) -> tuple[str, Subject, bool, tuple[_Entry, ...]]:
    file = MANIFEST_NAME
    obj = _object(doc, file, "", _MANIFEST_FIELDS, _MANIFEST_FIELDS)
    bundle_id = _item_id(obj["bundle_id"], file, "bundle_id")
    version = obj["schema_version"]
    if type(version) is not int or version != 1:
        _fail(file, "schema_version", "must be the integer 1")
    sub = _object(obj["subject"], file, "subject", _SUBJECT_FIELDS, _SUBJECT_FIELDS)
    handle = _author(sub["handle"], file, "subject.handle")
    if not isinstance(sub["display_name"], str):
        _fail(file, "subject.display_name", "must be a string")
    display_name = _normalise(sub["display_name"])
    _require_storable(display_name, file, "subject.display_name")
    fictional = obj["fictional"]
    if type(fictional) is not bool:
        _fail(file, "fictional", "must be a boolean")
    files = obj["files"]
    if not isinstance(files, list):
        _fail(file, "files", "must be an array")
    entries_raw = cast(list[object], files)
    if len(entries_raw) > MAX_FILES:
        _fail(file, "files", "exceeds the file count limit")
    entries = tuple(_check_entry(v, i) for i, v in enumerate(entries_raw))
    seen_paths: set[str] = set()
    for i, entry in enumerate(entries):
        if entry.path in seen_paths:
            _fail(file, f"files[{i}].path", "is listed more than once")
        seen_paths.add(entry.path)
    return bundle_id, Subject(handle, display_name), fictional, entries


def _build_item(
    *,
    id: str,
    kind: ItemKind,
    author: str,
    timestamp: datetime,
    title: str,
    body: str,
    url: str | None,
    path: str | None,
    references: tuple[str, ...],
) -> EvidenceItem:
    draft = EvidenceItem(
        id=id,
        kind=kind,
        author=author,
        timestamp=timestamp,
        title=title,
        body=body,
        url=url,
        path=path,
        references=references,
        content_hash="",
    )
    return EvidenceItem(
        id=id,
        kind=kind,
        author=author,
        timestamp=timestamp,
        title=title,
        body=body,
        url=url,
        path=path,
        references=references,
        content_hash=content_hash(draft),
    )


def _read_json_item(entry: _Entry, raw: bytes) -> EvidenceItem:
    file = _fname(entry.path)
    doc = _parse_json(_decode(raw, ITEM_MAX_BYTES, file), file)
    obj = _object(doc, file, "", _ITEM_ALLOWED, _ITEM_REQUIRED)
    item_id = _item_id(obj["id"], file, "id")
    kind = _kind(obj["kind"], file, "kind")
    if kind != entry.kind:
        _fail(file, "kind", "does not match the manifest entry")
    author = _author(obj["author"], file, "author")
    timestamp = _timestamp(obj["timestamp"], file, "timestamp")
    title = _text(obj["title"], file, "title", TITLE_MAX, non_empty=True)
    body = _text(obj["body"], file, "body", BODY_MAX)
    if ("url" in obj) == ("path" in obj):
        _fail(file, "url", "exactly one of url and path is required")
    url = _url(obj["url"], file, "url") if "url" in obj else None
    path = _safe_path(obj["path"], file, "path") if "path" in obj else None
    refs_value = obj["references"]
    if not isinstance(refs_value, list):
        _fail(file, "references", "must be an array")
    refs_raw = cast(list[object], refs_value)
    if len(refs_raw) > REFERENCES_MAX:
        _fail(file, "references", "exceeds the entry limit")
    references = tuple(_item_id(r, file, "references") for r in refs_raw)
    return _build_item(
        id=item_id,
        kind=kind,
        author=author,
        timestamp=timestamp,
        title=title,
        body=body,
        url=url,
        path=path,
        references=references,
    )


def _read_doc(entry: _Entry, raw: bytes) -> EvidenceItem:
    file = _fname(entry.path)
    text = _decode(raw, ITEM_MAX_BYTES, file)
    body = _text(text, file, "body", BODY_MAX)
    assert entry.id is not None
    assert entry.author is not None
    assert entry.timestamp is not None
    assert entry.title is not None
    return _build_item(
        id=entry.id,
        kind="doc",
        author=entry.author,
        timestamp=entry.timestamp,
        title=entry.title,
        body=body,
        url=None,
        path=entry.path,
        references=(),
    )


def read_bundle(fetch: FetchBytes) -> Bundle:
    """Validate the bundle behind ``fetch`` (algorithm A1); the first violation wins."""
    raw = _fetch(fetch, MANIFEST_NAME, MANIFEST_MAX_BYTES, MANIFEST_NAME)
    text = _decode(raw, MANIFEST_MAX_BYTES, MANIFEST_NAME)
    bundle_id, subject, fictional, entries = _check_manifest(
        _parse_json(text, MANIFEST_NAME)
    )

    items: list[EvidenceItem] = []
    by_id: dict[str, EvidenceItem] = {}
    for entry in entries:
        file = _fname(entry.path)
        data = _fetch(fetch, entry.path, ITEM_MAX_BYTES, file)
        item = (
            _read_doc(entry, data)
            if entry.kind == "doc"
            else _read_json_item(entry, data)
        )
        if item.id in by_id:
            _fail(file, "id", "is not unique within the bundle")
        by_id[item.id] = item
        items.append(item)

    for entry, item in zip(entries, items, strict=True):
        file = _fname(entry.path)
        for ref in item.references:
            if ref not in by_id:
                _fail(file, "references", "names an item that is not in the bundle")
        if item.author != subject.handle and (
            item.kind != "review"
            or not any(by_id[ref].author == subject.handle for ref in item.references)
        ):
            _fail(
                file,
                "author",
                "is not the subject and not a review of the subject's work",
            )

    return Bundle(
        bundle_id=bundle_id,
        subject=subject,
        fictional=fictional,
        items=tuple(items),
    )


def _stamp(ts: datetime) -> str:
    date = f"{ts.year:04d}-{ts.month:02d}-{ts.day:02d}"
    return f"{date}T{ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}Z"


def content_hash(item: EvidenceItem) -> str:
    """SHA-256 of the canonical JSON form (algorithm A2).

    The bundle id and the subject are not hashed.
    """
    payload: dict[str, object] = {
        "id": item.id,
        "kind": item.kind,
        "author": item.author,
        "timestamp": _stamp(item.timestamp),
        "title": _normalise(item.title),
        "body": _normalise(item.body),
        "url": item.url,
        "path": item.path,
        "references": list(item.references),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
