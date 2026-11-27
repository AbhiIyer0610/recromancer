"""Composition-root helpers for ingestion (BS): no database, no network."""

import functools
import json
from typing import Any

import pytest
from psycopg_pool import PoolTimeout

from recromancer import bootstrap
from recromancer.config import Settings
from recromancer.db.evidence_store import EMBEDDING_DIMENSIONS, PostgresEvidenceStore
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.ingest.bundle import read_bundle
from recromancer.ingest.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_item,
)
from tests.fakes import FakeEmbedder, FakeEvidenceStore
from tests.fakes_api import FakeClock
from tests.property.strategies import fetch_from

pytestmark = pytest.mark.anyio

URL = "postgresql://svc:" + "pw" + "x" * 10 + "@db.internal:6543/app"


def make_settings() -> Settings:
    return Settings.from_env({"RECROMANCER_DATABASE_URL": URL})


def small_bundle() -> dict[str, bytes]:
    manifest = {
        "bundle_id": "bundle-one",
        "schema_version": 1,
        "subject": {"handle": "ghost", "display_name": "Gus Host"},
        "fictional": True,
        "files": [{"path": "items/a.json", "kind": "commit"}],
    }
    item = {
        "id": "item-a",
        "kind": "commit",
        "author": "ghost",
        "timestamp": "2026-01-01T00:00:00Z",
        "title": "Title A",
        "body": "Body A",
        "url": "https://example.invalid/item-a",
        "references": [],
    }
    return {
        "manifest.json": json.dumps(manifest).encode(),
        "items/a.json": json.dumps(item).encode(),
    }


def test_dimension_mismatch_fails_before_any_write() -> None:
    store = FakeEvidenceStore()
    with pytest.raises(RecromancerError) as caught:
        bootstrap.build_ingestion_service(
            make_settings(),
            store=store,
            embedder=FakeEmbedder(dimensions=8),
            clock=FakeClock(),
        )
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert "8" in caught.value.message
    assert str(EMBEDDING_DIMENSIONS) == "384"
    assert "384" in caught.value.message
    assert store.snapshot() == ((), ())


async def test_matching_embedder_builds_working_service() -> None:
    store = FakeEvidenceStore()
    service = bootstrap.build_ingestion_service(
        make_settings(), store=store, embedder=FakeEmbedder(), clock=FakeClock()
    )
    report = await service.ingest(fetch_from(small_bundle()))
    assert report.items == 1
    assert report.chunks == 1
    assert len(store.snapshot()[0]) == 1


def test_service_is_wired_with_real_reader_and_chunker() -> None:
    service = bootstrap.build_ingestion_service(
        make_settings(),
        store=FakeEvidenceStore(),
        embedder=FakeEmbedder(),
        clock=FakeClock(),
    )
    assert service._read is read_bundle
    chunk = service._chunk
    assert isinstance(chunk, functools.partial)
    assert chunk.func is chunk_item
    assert chunk.args == ()
    assert chunk.keywords == {
        "size": DEFAULT_CHUNK_SIZE,
        "overlap": DEFAULT_CHUNK_OVERLAP,
    }
    assert service._chunk_size == DEFAULT_CHUNK_SIZE
    assert service._chunk_overlap == DEFAULT_CHUNK_OVERLAP


class FakePool:
    def __init__(self, *, open_error: Exception | None = None) -> None:
        self.open_error = open_error
        self.open_args: dict[str, Any] | None = None
        self.closed = 0

    def open(self, **kwargs: Any) -> None:
        self.open_args = kwargs
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        self.closed += 1


def test_evidence_store_maps_pool_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(open_error=PoolTimeout("host=secret-host"))
    monkeypatch.setattr(bootstrap, "build_pool", lambda url: pool)
    with pytest.raises(RecromancerError) as caught:
        with bootstrap.evidence_store(make_settings()):
            pytest.fail("body must not run")
    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.message == "database unavailable"
    assert caught.value.__cause__ is None
    assert pool.closed == 1


def test_evidence_store_yields_postgres_store_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()
    monkeypatch.setattr(bootstrap, "build_pool", lambda url: pool)
    with bootstrap.evidence_store(make_settings()) as store:
        assert isinstance(store, PostgresEvidenceStore)
        assert pool.closed == 0
    assert pool.open_args == {"wait": True, "timeout": 10}
    assert pool.closed == 1


def test_evidence_store_closes_pool_when_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()
    monkeypatch.setattr(bootstrap, "build_pool", lambda url: pool)
    with pytest.raises(ValueError, match="boom"):
        with bootstrap.evidence_store(make_settings()):
            raise ValueError("boom")
    assert pool.closed == 1
