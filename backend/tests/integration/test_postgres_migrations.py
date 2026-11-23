"""Migration runner and CLI against a real Postgres, inside throwaway schemas."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pytest

from recromancer.config import Settings
from recromancer.db.migrate import main
from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import Migration, MigrationError, StoreError, discover, run
from tests.pg_support import (
    connect,
    recorded_rows,
    relation_exists,
    table_names,
    throwaway_schema,
    try_advisory_lock,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
INITIAL = "0001_create_vector_extension.sql"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _warn(_: str) -> None:
    return None


def _write(directory: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8", newline="\n")


def _packaged() -> tuple[Migration, ...]:
    return discover(resources.files("recromancer.db") / "migrations")


def test_initial_migration_applies_and_is_idempotent(
    postgres_url: str, pg_schema: str
) -> None:
    store = PostgresMigrationStore(postgres_url, schema=pg_schema)
    first = run(store, _packaged(), _Clock(), _warn)
    assert INITIAL in first.applied
    with connect(postgres_url) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    assert row is not None
    second = run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        _packaged(),
        _Clock(),
        _warn,
    )
    assert second.applied == ()
    assert INITIAL in second.skipped


def test_lock_is_held_while_runner_applies(postgres_url: str, pg_schema: str) -> None:
    seen: list[bool] = []

    class Probing(PostgresMigrationStore):
        def apply(self, migration: Migration, applied_at: datetime) -> None:
            seen.append(try_advisory_lock(postgres_url))
            super().apply(migration, applied_at)

    migrations = (Migration("0001_t.sql", "CREATE TABLE t1 (id int);", "x"),)
    run(Probing(postgres_url, schema=pg_schema), migrations, _Clock(), _warn)
    assert seen == [False]
    assert try_advisory_lock(postgres_url) is True


def test_failing_migration_rolls_back_its_own_statements(
    postgres_url: str, pg_schema: str, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        {
            "0001_ok.sql": "CREATE TABLE ok_t (id int);",
            "0002_bad.sql": "CREATE TABLE bad_t (id int); SELECT 1/0;",
        },
    )
    store = PostgresMigrationStore(postgres_url, schema=pg_schema)
    with pytest.raises(MigrationError):
        run(store, discover(tmp_path), _Clock(), _warn)
    names = [name for name, _ in recorded_rows(postgres_url, pg_schema)]
    assert names == ["0001_ok.sql"]
    assert relation_exists(postgres_url, pg_schema, "ok_t")
    assert not relation_exists(postgres_url, pg_schema, "bad_t")


def test_applying_twice_changes_nothing(
    postgres_url: str, pg_schema: str, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        {
            "0001_a.sql": "CREATE TABLE a (id int);",
            "0002_b.sql": "CREATE TABLE b (id int);",
        },
    )
    migrations = discover(tmp_path)
    run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        migrations,
        _Clock(),
        _warn,
    )
    rows = recorded_rows(postgres_url, pg_schema)
    tables = table_names(postgres_url, pg_schema)
    report = run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        migrations,
        _Clock(),
        _warn,
    )
    assert report.applied == ()
    assert recorded_rows(postgres_url, pg_schema) == rows
    assert table_names(postgres_url, pg_schema) == tables


def test_prefix_then_rest_equals_all_at_once(
    postgres_url: str, pg_schema: str, tmp_path: Path
) -> None:
    files = {
        "0001_a.sql": "CREATE TABLE a (id int);",
        "0002_b.sql": "CREATE TABLE b (id int);",
        "0003_c.sql": "CREATE TABLE c (id int);",
    }
    _write(tmp_path, files)
    migrations = discover(tmp_path)
    run(
        PostgresMigrationStore(postgres_url, schema=pg_schema),
        migrations,
        _Clock(),
        _warn,
    )
    with throwaway_schema(postgres_url) as other:
        run(
            PostgresMigrationStore(postgres_url, schema=other),
            migrations[:1],
            _Clock(),
            _warn,
        )
        run(
            PostgresMigrationStore(postgres_url, schema=other),
            migrations,
            _Clock(),
            _warn,
        )
        assert recorded_rows(postgres_url, other) == recorded_rows(
            postgres_url, pg_schema
        )
        assert table_names(postgres_url, other) - {"_schema_migrations"} == table_names(
            postgres_url, pg_schema
        ) - {"_schema_migrations"}


def test_cli_applies_then_reports_nothing_pending(
    postgres_url: str, pg_schema: str, tmp_path: Path
) -> None:
    migrations_dir = tmp_path / "m"
    migrations_dir.mkdir()
    _write(migrations_dir, {"0001_a.sql": "CREATE TABLE a (id int);"})

    def factory(settings: Settings) -> PostgresMigrationStore:
        return PostgresMigrationStore(settings.database_url, schema=pg_schema)

    def invoke() -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            environ={"RECROMANCER_DATABASE_URL": postgres_url},
            store_factory=factory,
            stdout=out,
            stderr=err,
            migrations_dir=migrations_dir,
            dotenv_start=tmp_path,
        )
        return code, out.getvalue(), err.getvalue()

    code, out, _ = invoke()
    assert code == 0
    assert "applied 0001_a.sql" in out
    code, out, _ = invoke()
    assert code == 0
    assert "no migrations pending" in out


def test_cli_wrong_password_is_redacted(postgres_url: str, tmp_path: Path) -> None:
    password = "wrong-" + uuid.uuid4().hex
    bad_url = f"postgresql://recromancer:{password}@127.0.0.1:5432/recromancer"
    out, err = io.StringIO(), io.StringIO()
    code = main(
        environ={"RECROMANCER_DATABASE_URL": bad_url},
        stdout=out,
        stderr=err,
        migrations_dir=tmp_path,
        dotenv_start=tmp_path,
    )
    text = err.getvalue()
    assert code == 1
    assert "database error" in text
    assert password not in text
    assert "password=" not in text


def test_failed_record_insert_rolls_back_migration_effects(
    postgres_url: str, pg_schema: str
) -> None:
    store = PostgresMigrationStore(postgres_url, schema=pg_schema)
    first = Migration("0001_dup.sql", "CREATE TABLE first_t (id int);", "a" * 64)
    clash = Migration("0001_dup.sql", "CREATE TABLE clash_t (id int);", "b" * 64)
    with store.locked():
        store.ensure_table()
        store.apply(first, NOW)
        with pytest.raises(StoreError):
            store.apply(clash, NOW)
    assert relation_exists(postgres_url, pg_schema, "first_t")
    assert not relation_exists(postgres_url, pg_schema, "clash_t")
