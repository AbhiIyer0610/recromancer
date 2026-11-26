"""R8.8: ingest Ledgerline into the in-memory store and find the planted facts."""

from __future__ import annotations

import functools

import pytest

from recromancer.ingest.bundle import read_bundle
from recromancer.ingest.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_item,
)
from recromancer.ingest_cli import ledgerline_dir, make_fetch
from recromancer.service.ingestion import IngestionService
from tests.fakes import FakeEmbedder, FakeEvidenceStore
from tests.fakes_api import FakeClock

pytestmark = pytest.mark.anyio

# R2.4, R2.5, R2.6: planted fact item ids, with a keyword each must contain
# somewhere in its stored chunks (the Redis keyword is the measured 38% figure).
_FACT_A = (
    "pr-0142-remove-redis-cache",
    "review-pr-0142-tomas-month-end-load",
    "review-pr-0142-priya-measured-load",
)
_FACT_B = (
    "issue-0233-duplicate-settlement-webhook",
    "commit-9c21d7b-require-idempotency-key",
    "commit-e17a6c4-idempotency-unique-constraint",
)
_FACT_C = (
    "doc-rfc-multi-currency-ledger",
    "review-rfc-multi-currency-priya-objection",
)
_PLANTED = _FACT_A + _FACT_B + _FACT_C
_BUNDLE_ITEMS = 31


async def test_ledgerline_planted_facts_are_stored_with_chunks() -> None:
    fetch = make_fetch(ledgerline_dir())
    bundle = read_bundle(fetch)
    embedder = FakeEmbedder()
    store = FakeEvidenceStore()
    service = IngestionService(
        read=read_bundle,
        chunk=functools.partial(
            chunk_item, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP
        ),
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        embedder=embedder,
        store=store,
        clock=FakeClock(),
    )

    report = await service.ingest(fetch)

    assert len(bundle.items) == _BUNDLE_ITEMS
    assert report.items == len(bundle.items)
    assert report.unchanged == 0
    assert report.absent == 0

    total_chunks = 0
    for item in bundle.items:
        stored_chunks = await store.get_chunks(bundle.bundle_id, item.id)
        total_chunks += len(stored_chunks)
        for stored in stored_chunks:
            assert stored.embedding_model == embedder.model_id
            assert len(stored.vector) == 384
    assert report.chunks == total_chunks
    assert report.chunks > report.items

    texts: dict[str, str] = {}
    for item_id in _PLANTED:
        assert await store.get_item(bundle.bundle_id, item_id) is not None
        chunks = await store.get_chunks(bundle.bundle_id, item_id)
        assert chunks, item_id
        texts[item_id] = " ".join(c.chunk.text for c in chunks)

    assert "38%" in texts["pr-0142-remove-redis-cache"]
