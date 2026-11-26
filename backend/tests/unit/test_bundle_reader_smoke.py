"""Smoke tests for the Bundle_Reader; the full suite is test_bundle_reader.py."""

import dataclasses
import json

import pytest

from recromancer.domain.errors import BundleError
from recromancer.domain.evidence import FetchBytes
from recromancer.ingest.bundle import content_hash, read_bundle


def _files(*, bad_author: bool = False) -> dict[str, bytes]:
    manifest = {
        "bundle_id": "demo-bundle",
        "schema_version": 1,
        "subject": {"handle": "ghost", "display_name": "Gus Host"},
        "fictional": True,
        "files": [
            {"path": "items/c1.json", "kind": "commit"},
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
    commit: dict[str, object] = {
        "id": "commit-one",
        "kind": "commit",
        "author": "stranger" if bad_author else "ghost",
        "timestamp": "2026-01-01T00:00:00Z",
        "title": "Add thing",
        "body": "line one\r\nline two",
        "url": "https://example.invalid/c/1",
        "references": [],
    }
    return {
        "manifest.json": json.dumps(manifest).encode(),
        "items/c1.json": json.dumps(commit).encode(),
        "docs/d1.md": b"# Design\r\ntext",
    }


def _fetch(files: dict[str, bytes]) -> FetchBytes:
    def fetch(path: str, max_bytes: int) -> bytes:
        try:
            return files[path][: max_bytes + 1]
        except KeyError:
            raise FileNotFoundError(path) from None

    return fetch


def test_valid_bundle_reads_in_manifest_order() -> None:
    bundle = read_bundle(_fetch(_files()))
    assert [i.id for i in bundle.items] == ["commit-one", "doc-one"]
    assert bundle.items[0].body == "line one\nline two"
    assert bundle.items[1].path == "docs/d1.md"
    assert bundle.subject.handle == "ghost"


def test_invalid_bundle_raises_without_leaking_values() -> None:
    with pytest.raises(BundleError) as err:
        read_bundle(_fetch(_files(bad_author=True)))
    assert str(err.value) == (
        "items/c1.json: field author: "
        "is not the subject and not a review of the subject's work"
    )
    assert "stranger" not in str(err.value)


def test_content_hash_is_hex_and_stable_under_crlf() -> None:
    item = read_bundle(_fetch(_files())).items[0]
    crlf = dataclasses.replace(item, body="line one\r\nline two")
    digest = content_hash(item)
    assert len(digest) == 64
    int(digest, 16)
    assert content_hash(crlf) == digest == item.content_hash
