"""Ingestion service: read a bundle, chunk, embed and store it item by item (A4).

Imports only ``domain``, ``service.ports``, the standard library and ``structlog``.
Nothing here logs titles, bodies or chunk text; only ids, counts and lengths.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    Bundle,
    Chunk,
    EmbeddedChunk,
    EvidenceItem,
    FetchBytes,
    ItemWrite,
)
from recromancer.service.ports import Clock, Embedder, EvidenceStore

_log = structlog.get_logger()


@dataclass(frozen=True)
class IngestReport:
    items: int
    chunks: int
    unchanged: int
    absent: int


class IngestError(RecromancerError):
    """Ingestion stopped at one item. Earlier items stay committed."""

    def __init__(self, item_id: str, report: IngestReport) -> None:
        super().__init__(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"ingest failed at item {item_id}: items={report.items} "
            f"chunks={report.chunks} unchanged={report.unchanged} "
            f"absent={report.absent}",
        )
        self.item_id = item_id
        self.report = report


def ingest_signature(
    content_hash: str, chunk_size: int, chunk_overlap: int, model_id: str
) -> str:
    return f"v1|{content_hash}|{chunk_size}|{chunk_overlap}|{model_id}"


class IngestionService:
    def __init__(
        self,
        *,
        read: Callable[[FetchBytes], Bundle],
        chunk: Callable[[EvidenceItem], tuple[Chunk, ...]],
        chunk_size: int,
        chunk_overlap: int,
        embedder: Embedder,
        store: EvidenceStore,
        clock: Clock,
    ) -> None:
        self._read = read
        self._chunk = chunk
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._embedder = embedder
        self._store = store
        self._clock = clock

    async def ingest(self, fetch: FetchBytes) -> IngestReport:
        bundle = self._read(fetch)
        stored = await self._store.signatures(bundle.bundle_id)
        absent = len(stored.keys() - {item.id for item in bundle.items})
        items = chunks_written = unchanged = 0
        for item in bundle.items:
            signature = ingest_signature(
                item.content_hash,
                self._chunk_size,
                self._chunk_overlap,
                self._embedder.model_id,
            )
            if stored.get(item.id) == signature:
                unchanged += 1
                items += 1
                _log.debug("ingest_item_unchanged", item_id=item.id)
                continue
            try:
                count, text_len = await self._write_item(bundle, item, signature)
            except Exception as exc:
                report = IngestReport(items, chunks_written, unchanged, absent)
                _log.error(
                    "ingest_failed",
                    bundle_id=bundle.bundle_id,
                    item_id=item.id,
                    error_type=type(exc).__name__,
                    items=report.items,
                    chunks=report.chunks,
                    unchanged=report.unchanged,
                    absent=report.absent,
                )
                raise IngestError(item.id, report) from None
            items += 1
            chunks_written += count
            _log.debug(
                "ingest_item_written",
                item_id=item.id,
                chunks=count,
                text_len=text_len,
            )
        report = IngestReport(items, chunks_written, unchanged, absent)
        _log.info(
            "ingest_completed",
            bundle_id=bundle.bundle_id,
            items=report.items,
            chunks=report.chunks,
            unchanged=report.unchanged,
            absent=report.absent,
        )
        return report

    async def _write_item(
        self, bundle: Bundle, item: EvidenceItem, signature: str
    ) -> tuple[int, int]:
        chunks = self._chunk(item)
        vectors: list[list[float]] = []
        if chunks:
            vectors = await self._embedder.embed([c.text for c in chunks])
        dimensions = self._embedder.dimensions
        if len(vectors) != len(chunks) or any(len(v) != dimensions for v in vectors):
            raise ValueError("embedder returned unexpected vectors")
        model_id = self._embedder.model_id
        embedded = tuple(
            EmbeddedChunk(chunk=c, vector=tuple(v), model_id=model_id)
            for c, v in zip(chunks, vectors, strict=True)
        )
        await self._store.replace_item(
            ItemWrite(
                bundle_id=bundle.bundle_id,
                subject_handle=bundle.subject.handle,
                item=item,
                signature=signature,
                ingested_at=self._clock.now(),
                chunks=embedded,
            )
        )
        return len(chunks), sum(len(c.text) for c in chunks)
