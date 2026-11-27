"""The ingest CLI against real Postgres, with the Fake_Embedder.

The CLI uses the default store factory (``bootstrap.evidence_store``, the real pool and
``PostgresEvidenceStore``). The pool builder is not changed: the throwaway schema is
selected through the ``options`` query parameter of the database URL.
"""

from __future__ import annotations

import io
from importlib.resources import files
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import discover, run
from recromancer.ingest.bundle import read_bundle
from recromancer.ingest_cli import ledgerline_dir, main, make_fetch
from tests.fakes import FakeEmbedder
from tests.fakes_api import FakeClock


@pytest.fixture(autouse=True)
def _restore_logging(log_stream: io.StringIO) -> None:
    """`main` reconfigures global logging; `log_stream` restores it afterwards."""


def _schema_url(url: str, schema: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-c%20search_path%3D{schema}%2Cpublic"


def _count(url: str, schema: str, table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(table)
    )
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def _run_cli(environ: dict[str, str], tmp_path: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [str(Path(str(ledgerline_dir())))],
        environ=environ,
        embedder_factory=lambda _settings: FakeEmbedder(),
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        dotenv_start=tmp_path,
    )
    return code, out.getvalue(), err.getvalue()


def test_cli_ingests_ledgerline_into_postgres_and_reruns_unchanged(
    postgres_url: str, pg_schema: str, tmp_path: Path
) -> None:
    run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        discover(files("recromancer.db") / "migrations"),
        FakeClock(),
        lambda _m: None,
    )
    environ = {"RECROMANCER_DATABASE_URL": _schema_url(postgres_url, pg_schema)}
    n = len(read_bundle(make_fetch(ledgerline_dir())).items)

    code, out, err = _run_cli(environ, tmp_path)
    m = _count(postgres_url, pg_schema, "evidence_chunks")
    assert code == 0
    assert out == f"items={n} chunks={m} unchanged=0 absent=0\n"
    assert err == ""
    assert m > 0
    assert _count(postgres_url, pg_schema, "evidence_items") == n

    code, out, err = _run_cli(environ, tmp_path)
    assert code == 0
    assert out == f"items={n} chunks=0 unchanged={n} absent=0\n"
    assert err == ""
    assert _count(postgres_url, pg_schema, "evidence_chunks") == m

    with psycopg.connect(postgres_url, autocommit=True) as conn:
        row = conn.execute("SELECT to_regclass('public.evidence_items')").fetchone()
    assert row is not None
    assert row[0] is None
