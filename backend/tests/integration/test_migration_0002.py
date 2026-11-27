"""Migration 0002 (evidence tables) against a real Postgres, plus byte checks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from typing import Any

import psycopg
import pytest
from psycopg import errors

from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import discover, run
from tests.fakes_api import FakeClock
from tests.pg_support import recorded_rows

FIRST = "0001_create_vector_extension.sql"
SECOND = "0002_create_evidence_tables.sql"
OCCURRED = "2026-01-02T03:04:05+00:00"

ITEM_SQL = (
    "INSERT INTO evidence_items (bundle_id, item_id, kind, author, subject_handle, "
    "occurred_at, title, body, url, path, content_hash, ingest_signature, "
    "ingested_at) VALUES (%s, %s, %s, 'a', 'h', %s, 't', 'b', %s, %s, 'c', 's', %s)"
)
CHUNK_SQL = (
    "INSERT INTO evidence_chunks (bundle_id, item_id, chunk_id, position, "
    "start_offset, end_offset, text, embedding, embedding_model) "
    "VALUES (%s, %s, %s, 0, 0, 1, 'x', %s::vector, 'm')"
)


def _vector(dim: int) -> str:
    return "[" + ",".join("0.5" for _ in range(dim)) + "]"


def _migration_bytes(name: str) -> bytes:
    return (files("recromancer.db") / "migrations" / name).read_bytes()


@pytest.fixture
def migrated(postgres_url: str, pg_schema: str) -> Iterator[tuple[str, str]]:
    run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        discover(files("recromancer.db") / "migrations"),
        FakeClock(),
        lambda _m: None,
    )
    yield postgres_url, pg_schema


@contextmanager
def _conn(url: str, schema: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(
        url, autocommit=True, options=f"-c search_path={schema},public"
    ) as conn:
        yield conn


def _add_item(
    conn: psycopg.Connection[Any],
    item_id: str,
    kind: str = "commit",
    url: str | None = "https://example.test/x",
    path: str | None = None,
) -> None:
    conn.execute(ITEM_SQL, ("b1", item_id, kind, OCCURRED, url, path, OCCURRED))


def test_both_migrations_are_recorded_and_objects_exist(
    migrated: tuple[str, str],
) -> None:
    url, schema = migrated
    names = [name for name, _ in recorded_rows(url, schema)]
    assert names == [FIRST, SECOND]
    with _conn(url, schema) as conn:
        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s",
                (schema,),
            ).fetchall()
        }
        indexes = {
            str(r[0]): str(r[1])
            for r in conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s",
                (schema,),
            ).fetchall()
        }
    assert {"evidence_items", "evidence_chunks"} <= tables
    hnsw = indexes["evidence_chunks_embedding_hnsw"]
    assert "USING hnsw" in hnsw
    assert "vector_cosine_ops" in hnsw
    assert "evidence_chunks_item_idx" in indexes


def test_unknown_kind_is_rejected(migrated: tuple[str, str]) -> None:
    with _conn(*migrated) as conn:
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _add_item(conn, "i1", kind="tweet")


@pytest.mark.parametrize(
    ("url", "path"), [("https://example.test/x", "a/b.md"), (None, None)]
)
def test_url_xor_path_is_enforced(
    migrated: tuple[str, str], url: str | None, path: str | None
) -> None:
    with _conn(*migrated) as conn:
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _add_item(conn, "i1", url=url, path=path)


def test_wrong_vector_dimension_is_rejected(migrated: tuple[str, str]) -> None:
    with _conn(*migrated) as conn:
        _add_item(conn, "i1")
        with pytest.raises(errors.DataException), conn.transaction():
            conn.execute(CHUNK_SQL, ("b1", "i1", "c1", _vector(3)))


def test_chunk_for_missing_item_is_rejected(migrated: tuple[str, str]) -> None:
    with _conn(*migrated) as conn:
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            conn.execute(CHUNK_SQL, ("b1", "missing", "c1", _vector(384)))


def test_deleting_an_item_cascades_to_its_chunks(migrated: tuple[str, str]) -> None:
    with _conn(*migrated) as conn:
        _add_item(conn, "i1")
        _add_item(conn, "i2")
        conn.execute(CHUNK_SQL, ("b1", "i1", "c1", _vector(384)))
        conn.execute(CHUNK_SQL, ("b1", "i2", "c2", _vector(384)))
        conn.execute("DELETE FROM evidence_items WHERE item_id = %s", ("i1",))
        rows = conn.execute("SELECT chunk_id FROM evidence_chunks").fetchall()
    assert [r[0] for r in rows] == ["c2"]


def test_0002_bytes_have_no_bom_no_cr_and_end_with_newline() -> None:
    data = _migration_bytes(SECOND)
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data
    assert data.endswith(b"\n")


def test_0001_bytes_are_unchanged() -> None:
    assert _migration_bytes(FIRST) == b"CREATE EXTENSION IF NOT EXISTS vector;\n"
