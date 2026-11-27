"""Postgres `EvidenceStore`: atomic per-item replace and reads, scoped by workspace.

Sync psycopg through `anyio.to_thread` (see `db.postgres`). The store never sets
`search_path`; the pool decides it (tests pass `options=-c search_path=...`). Driver
errors are reduced to class name and SQLSTATE so no conninfo or text can leak.
"""

import math
from collections.abc import Callable, Sequence
from datetime import UTC
from typing import Any, TypeVar

import anyio.to_thread
from psycopg_pool import ConnectionPool

from recromancer.db.postgres import _DRIVER_ERRORS, _safe_message
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import (
    DEFAULT_WORKSPACE_ID,
    Chunk,
    EvidenceItem,
    ItemKind,
    ItemWrite,
    SourceLocator,
    StoredChunk,
    StoredItem,
)

__all__ = ["EMBEDDING_DIMENSIONS", "PostgresEvidenceStore"]

EMBEDDING_DIMENSIONS = 384

_T = TypeVar("_T")

_SIGNATURES_SQL = (
    "SELECT item_id, ingest_signature FROM evidence_items "
    "WHERE workspace_id = %s AND bundle_id = %s"
)
_UPSERT_ITEM_SQL = (
    "INSERT INTO evidence_items (workspace_id, bundle_id, item_id, kind, author, "
    "subject_handle, occurred_at, title, body, url, path, references_ids, "
    "content_hash, ingest_signature, ingested_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (workspace_id, bundle_id, item_id) DO UPDATE SET "
    "kind = EXCLUDED.kind, author = EXCLUDED.author, "
    "subject_handle = EXCLUDED.subject_handle, occurred_at = EXCLUDED.occurred_at, "
    "title = EXCLUDED.title, body = EXCLUDED.body, url = EXCLUDED.url, "
    "path = EXCLUDED.path, references_ids = EXCLUDED.references_ids, "
    "content_hash = EXCLUDED.content_hash, "
    "ingest_signature = EXCLUDED.ingest_signature, "
    "ingested_at = EXCLUDED.ingested_at"
)
_DELETE_CHUNKS_SQL = (
    "DELETE FROM evidence_chunks "
    "WHERE workspace_id = %s AND bundle_id = %s AND item_id = %s"
)
_INSERT_CHUNK_SQL = (
    "INSERT INTO evidence_chunks (workspace_id, bundle_id, item_id, chunk_id, "
    "position, start_offset, end_offset, text, embedding, embedding_model) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)"
)
_GET_ITEM_SQL = (
    "SELECT subject_handle, kind, author, occurred_at, title, body, url, path, "
    "references_ids, content_hash, ingest_signature, ingested_at "
    "FROM evidence_items WHERE workspace_id = %s AND bundle_id = %s AND item_id = %s"
)
_GET_CHUNKS_SQL = (
    "SELECT c.chunk_id, c.position, c.start_offset, c.end_offset, c.text, "
    "c.embedding::text, c.embedding_model, i.kind, i.author, i.occurred_at, "
    "i.url, i.path "
    "FROM evidence_chunks c JOIN evidence_items i "
    "ON i.workspace_id = c.workspace_id AND i.bundle_id = c.bundle_id "
    "AND i.item_id = c.item_id "
    "WHERE c.workspace_id = %s AND c.bundle_id = %s AND c.item_id = %s "
    "ORDER BY c.position"
)


def _vector_literal(vector: Sequence[float]) -> str:
    """Render `[a,b,...]` using `repr(float)`, which round-trips exactly."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _parse_vector(text: str) -> tuple[float, ...]:
    inner = text.strip()[1:-1]
    return tuple(float(part) for part in inner.split(",")) if inner else ()


def _validate_vectors(write: ItemWrite) -> None:
    for embedded in write.chunks:
        chunk_id = embedded.chunk.chunk_id
        length = len(embedded.vector)
        if length != EMBEDDING_DIMENSIONS:
            raise RecromancerError(
                ErrorCode.VALIDATION_ERROR,
                f"chunk {chunk_id}: vector has {length} dimensions, "
                f"expected {EMBEDDING_DIMENSIONS}",
            )
        if not all(math.isfinite(v) for v in embedded.vector):
            raise RecromancerError(
                ErrorCode.VALIDATION_ERROR,
                f"chunk {chunk_id}: vector has a non-finite value",
            )


class PostgresEvidenceStore:
    """`EvidenceStore` over a sync pool; every call runs in a worker thread."""

    def __init__(
        self, pool: ConnectionPool, *, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> None:
        self._pool = pool
        self._workspace_id = workspace_id

    async def _run(self, op: str, func: Callable[[], _T]) -> _T:
        def guarded() -> _T:
            try:
                return func()
            except _DRIVER_ERRORS as exc:
                raise RecromancerError(
                    ErrorCode.DEPENDENCY_UNAVAILABLE, _safe_message(op, exc)
                ) from None

        return await anyio.to_thread.run_sync(guarded)

    async def signatures(self, bundle_id: str) -> dict[str, str]:
        def work() -> dict[str, str]:
            with self._pool.connection() as conn:
                rows = conn.execute(
                    _SIGNATURES_SQL, (self._workspace_id, bundle_id)
                ).fetchall()
            return {row[0]: row[1] for row in rows}

        return await self._run("signatures", work)

    async def replace_item(self, write: ItemWrite) -> None:
        _validate_vectors(write)

        def work() -> None:
            ws, bundle, item = self._workspace_id, write.bundle_id, write.item
            chunk_rows = [
                (
                    ws,
                    bundle,
                    item.id,
                    e.chunk.chunk_id,
                    e.chunk.position,
                    e.chunk.locator.start_offset,
                    e.chunk.locator.end_offset,
                    e.chunk.text,
                    _vector_literal(e.vector),
                    e.model_id,
                )
                for e in write.chunks
            ]
            with self._pool.connection() as conn, conn.transaction():
                conn.execute(
                    _UPSERT_ITEM_SQL,
                    (
                        ws,
                        bundle,
                        item.id,
                        item.kind,
                        item.author,
                        write.subject_handle,
                        item.timestamp,
                        item.title,
                        item.body,
                        item.url,
                        item.path,
                        list(item.references),
                        item.content_hash,
                        write.signature,
                        write.ingested_at,
                    ),
                )
                conn.execute(_DELETE_CHUNKS_SQL, (ws, bundle, item.id))
                if chunk_rows:
                    with conn.cursor() as cur:
                        cur.executemany(_INSERT_CHUNK_SQL, chunk_rows)

        await self._run("replace_item", work)

    async def get_item(self, bundle_id: str, item_id: str) -> StoredItem | None:
        def work() -> StoredItem | None:
            with self._pool.connection() as conn:
                row = conn.execute(
                    _GET_ITEM_SQL, (self._workspace_id, bundle_id, item_id)
                ).fetchone()
            if row is None:
                return None
            kind: ItemKind = row[1]
            item = EvidenceItem(
                id=item_id,
                kind=kind,
                author=row[2],
                timestamp=row[3].astimezone(UTC),
                title=row[4],
                body=row[5],
                url=row[6],
                path=row[7],
                references=tuple(row[8]),
                content_hash=row[9],
            )
            return StoredItem(
                bundle_id=bundle_id,
                subject_handle=row[0],
                item=item,
                signature=row[10],
                ingested_at=row[11].astimezone(UTC),
            )

        return await self._run("get_item", work)

    async def get_chunks(self, bundle_id: str, item_id: str) -> tuple[StoredChunk, ...]:
        def work() -> tuple[StoredChunk, ...]:
            with self._pool.connection() as conn:
                rows: list[Any] = conn.execute(
                    _GET_CHUNKS_SQL, (self._workspace_id, bundle_id, item_id)
                ).fetchall()
            stored: list[StoredChunk] = []
            for row in rows:
                kind: ItemKind = row[7]
                chunk = Chunk(
                    chunk_id=row[0],
                    position=row[1],
                    item_id=item_id,
                    kind=kind,
                    author=row[8],
                    timestamp=row[9].astimezone(UTC),
                    text=row[4],
                    locator=SourceLocator(
                        item_id=item_id,
                        start_offset=row[2],
                        end_offset=row[3],
                        url=row[10],
                        path=row[11],
                    ),
                )
                stored.append(
                    StoredChunk(
                        bundle_id=bundle_id,
                        chunk=chunk,
                        vector=_parse_vector(row[5]),
                        embedding_model=row[6],
                    )
                )
            return tuple(stored)

        return await self._run("get_chunks", work)
