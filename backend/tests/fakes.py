"""In-memory fakes for the migration runner tests."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from recromancer.db.runner import Migration, StoreError
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    DEFAULT_WORKSPACE_ID,
    Chunk,
    ItemWrite,
    StoredChunk,
    StoredItem,
)
from tests.fakes_api import FakeClock as FakeClock

# SQL prefix that makes FakeMigrationStore.apply fail (strategies import it).
FAIL_MARKER = "-- fail"

Snapshot = tuple[tuple[str, ...], tuple[tuple[str, str, datetime], ...]]


class FakeMigrationStore:
    """Deterministic ``MigrationStore``.

    ``history`` (the executed SQL, in order) models the schema. ``recorded`` maps
    filename -> (checksum, applied_at). Both change together inside ``apply``.
    """

    def __init__(self) -> None:
        self.history: tuple[str, ...] = ()
        self.recorded: dict[str, tuple[str, datetime]] = {}
        self.events: list[str] = []
        self._held = False
        self._table = False

    @contextmanager
    def locked(self) -> Iterator[None]:
        if self._held:
            raise RuntimeError("migration lock is already held")
        self._held = True
        self.events.append("lock")
        try:
            yield
        finally:
            self._held = False
            self.events.append("unlock")

    def _require_lock(self) -> None:
        if not self._held:
            raise RuntimeError("migration lock is not held")

    def ensure_table(self) -> None:
        self._require_lock()
        self.events.append("ensure")
        self._table = True

    def applied(self) -> dict[str, str]:
        self._require_lock()
        if not self._table:
            raise RuntimeError("migrations table does not exist")
        return {name: checksum for name, (checksum, _) in self.recorded.items()}

    def apply(self, migration: Migration, applied_at: datetime) -> None:
        self._require_lock()
        if not self._table:
            raise RuntimeError("migrations table does not exist")
        if migration.sql.lstrip().startswith(FAIL_MARKER):
            raise StoreError("simulated failure")
        new_history = (*self.history, migration.sql)
        new_recorded = {
            **self.recorded,
            migration.name: (migration.checksum, applied_at),
        }
        self.history = new_history
        self.recorded = new_recorded
        self.events.append(f"apply:{migration.name}")

    def schema_state(self) -> tuple[str, ...]:
        return self.history

    def snapshot(self) -> Snapshot:
        recorded = tuple(
            (name, checksum, at)
            for name, (checksum, at) in sorted(self.recorded.items())
        )
        return (self.history, recorded)


class FakeEmbedder:
    """Deterministic `Embedder`. A vector depends only on the text."""

    def __init__(
        self,
        dimensions: int = 384,
        model_id: str = "fake-embed-v1",
        fail_on_call: int | None = None,
    ) -> None:
        self._dimensions = dimensions
        self._model_id = model_id
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.texts_embedded = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> list[float]:
        data = text.encode("utf-8")
        raw: list[float] = []
        block = 0
        while len(raw) < self._dimensions:
            digest = hashlib.blake2b(
                data, digest_size=64, person=block.to_bytes(4, "big")
            ).digest()
            raw.extend(b / 127.5 - 1.0 for b in digest)
            block += 1
        raw = raw[: self._dimensions]
        norm = math.sqrt(sum(x * x for x in raw))
        if norm == 0.0:
            return [1.0] + [0.0] * (self._dimensions - 1)
        return [x / norm for x in raw]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RecromancerError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "embedder failure injected"
            )
        self.texts_embedded += len(texts)
        return [self._vector(text) for text in texts]


EvidenceSnapshot = tuple[
    tuple[tuple[str, str, str, StoredItem], ...],
    tuple[tuple[str, str, str, str, StoredChunk], ...],
]


class FakeEvidenceStore:
    """In-memory `EvidenceStore` keyed like the Postgres primary keys."""

    def __init__(
        self,
        dimensions: int = 384,
        fail_on_item_ids: frozenset[str] = frozenset(),
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> None:
        self._dimensions = dimensions
        self._fail_on_item_ids = fail_on_item_ids
        self._workspace_id = workspace_id
        # (workspace, bundle, item) -> StoredItem
        self._items: dict[tuple[str, str, str], StoredItem] = {}
        # (workspace, bundle, item, chunk) -> StoredChunk
        self._chunks: dict[tuple[str, str, str, str], StoredChunk] = {}

    async def signatures(self, bundle_id: str) -> dict[str, str]:
        return {
            item_id: stored.signature
            for (ws, bundle, item_id), stored in self._items.items()
            if ws == self._workspace_id and bundle == bundle_id
        }

    async def replace_item(self, write: ItemWrite) -> None:
        ws, bundle, item_id = self._workspace_id, write.bundle_id, write.item.id
        if item_id in self._fail_on_item_ids:
            raise RecromancerError(
                ErrorCode.DEPENDENCY_UNAVAILABLE, "store failure injected"
            )
        for embedded in write.chunks:
            if len(embedded.vector) != self._dimensions:
                raise RecromancerError(
                    ErrorCode.VALIDATION_ERROR,
                    f"vector has {len(embedded.vector)} dimensions, "
                    f"expected {self._dimensions}",
                )
        new_items = dict(self._items)
        new_chunks = {
            key: value
            for key, value in self._chunks.items()
            if key[:3] != (ws, bundle, item_id)
        }
        new_items[(ws, bundle, item_id)] = StoredItem(
            bundle_id=bundle,
            subject_handle=write.subject_handle,
            item=write.item,
            signature=write.signature,
            ingested_at=write.ingested_at,
        )
        for embedded in write.chunks:
            chunk: Chunk = embedded.chunk
            new_chunks[(ws, bundle, item_id, chunk.chunk_id)] = StoredChunk(
                bundle_id=bundle,
                chunk=chunk,
                vector=embedded.vector,
                embedding_model=embedded.model_id,
            )
        self._items, self._chunks = new_items, new_chunks

    async def get_item(self, bundle_id: str, item_id: str) -> StoredItem | None:
        return self._items.get((self._workspace_id, bundle_id, item_id))

    async def get_chunks(self, bundle_id: str, item_id: str) -> tuple[StoredChunk, ...]:
        found = [
            stored
            for key, stored in self._chunks.items()
            if key[:3] == (self._workspace_id, bundle_id, item_id)
        ]
        return tuple(sorted(found, key=lambda s: s.chunk.position))

    def snapshot(self) -> EvidenceSnapshot:
        items = tuple((*key, value) for key, value in sorted(self._items.items()))
        chunks = tuple((*key, value) for key, value in sorted(self._chunks.items()))
        return (items, chunks)
