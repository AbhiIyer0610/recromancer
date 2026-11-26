"""Property tests for ``IngestionService`` over in-memory fakes (evidence-ingestion)."""

from __future__ import annotations

import functools
import json

import anyio
from hypothesis import example, given
from hypothesis import strategies as st

from recromancer.ingest.bundle import read_bundle
from recromancer.ingest.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_item,
)
from recromancer.service.ingestion import IngestionService, IngestReport
from tests.fakes import FakeClock, FakeEmbedder, FakeEvidenceStore
from tests.property.strategies import GeneratedBundle, bundles, chunk_params

# Small sizes make many-chunk items common; the defaults (one chunk) appear too.
_PARAMS = st.one_of(
    chunk_params(), st.just((DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP))
)

_EMPTY = GeneratedBundle(
    files={
        "manifest.json": json.dumps(
            {
                "bundle_id": "empty",
                "schema_version": 1,
                "subject": {"handle": "ghost", "display_name": "Gus Host"},
                "fictional": True,
                "files": [],
            }
        ).encode()
    },
    item_ids=(),
    bundle_id="empty",
    subject_handle="ghost",
)


# Feature: evidence-ingestion, Property 4: Ingesting a bundle twice changes nothing
# Validates: Requirements 6.3, 6.4, 8.3
# Mutation_Check (task 1.15): see the "Notes (task 1.15)" block in tasks.md. Appending
# the clock time to the Ingest_Signature in IngestionService.ingest() makes run 2
# re-embed every item, so the report and embedder-counter assertions fail.
@given(bundle=bundles(), params=_PARAMS)
@example(bundle=_EMPTY, params=(DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP))
def test_ingesting_twice_changes_nothing(
    bundle: GeneratedBundle, params: tuple[int, int]
) -> None:
    size, overlap = params
    embedder = FakeEmbedder()
    store = FakeEvidenceStore()
    clock = FakeClock()
    service = IngestionService(
        read=read_bundle,
        chunk=functools.partial(chunk_item, size=size, overlap=overlap),
        chunk_size=size,
        chunk_overlap=overlap,
        embedder=embedder,
        store=store,
        clock=clock,
    )

    async def run() -> IngestReport:
        return await service.ingest(bundle.fetch)

    anyio.run(run)
    snapshot = store.snapshot()
    calls, texts = embedder.calls, embedder.texts_embedded

    clock.advance(hours=1)
    report = anyio.run(run)

    assert store.snapshot() == snapshot
    assert report.chunks == 0
    assert report.unchanged == report.items == len(bundle.item_ids)
    assert report.absent == 0
    assert (embedder.calls, embedder.texts_embedded) == (calls, texts)
