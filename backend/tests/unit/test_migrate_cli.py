"""Unit tests for the migrate CLI (`recromancer.db.migrate.main`), driven by fakes."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

import pytest

from recromancer.config import Settings
from recromancer.db import migrate
from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import StoreError, discover
from tests.fakes import FAIL_MARKER, FakeClock, FakeMigrationStore

# Built at runtime so no secret-shaped literal sits in the source. Both are >= 8
# characters, contain characters that JSON escaping and percent-encoding change, and
# are not token-shaped.
DB_PASSWORD_DECODED = "Pw" + '"' + "x!yz" + "AB9"
DB_PASSWORD_RAW = "Pw%22x%21yzAB9"
ADMIN_TOKEN = "adm " + '"' + "q" + '"' + " tok\u00e9n9"
DB_URL = f"postgresql://app:{DB_PASSWORD_RAW}@db.example.test:5432/app"

LEAK_FORMS = (
    DB_PASSWORD_RAW,
    DB_PASSWORD_DECODED,
    json.dumps(DB_PASSWORD_DECODED)[1:-1],
    quote(DB_PASSWORD_DECODED),
    quote(DB_PASSWORD_DECODED, safe=""),
    DB_URL,
    ADMIN_TOKEN,
    json.dumps(ADMIN_TOKEN)[1:-1],
    json.dumps(ADMIN_TOKEN, ensure_ascii=False)[1:-1],
    quote(ADMIN_TOKEN),
    quote(ADMIN_TOKEN, safe=""),
)

ENV: Mapping[str, str] = {
    "RECROMANCER_DATABASE_URL": DB_URL,
    "RECROMANCER_ADMIN_TOKEN": ADMIN_TOKEN,
}


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


def _migrations(tmp_path: Path, files: Mapping[str, str]) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name).write_bytes(text.encode("utf-8"))
    return directory


def _run(
    tmp_path: Path,
    directory: Path | None,
    store: FakeMigrationStore,
    environ: dict[str, str] | None = None,
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    code = migrate.main(
        [],
        environ=dict(ENV) if environ is None else environ,
        store_factory=lambda _settings: store,
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        migrations_dir=directory,
        dotenv_start=tmp_path / "no-repo-here",
    )
    return Result(code, out.getvalue(), err.getvalue())


def _assert_clean(result: Result) -> None:
    assert "Traceback" not in result.err
    for form in LEAK_FORMS:
        assert form not in result.err
        assert form not in result.out


def test_applies_in_order_then_reports_nothing_pending(tmp_path: Path) -> None:
    directory = _migrations(
        tmp_path,
        {"0002_b.sql": "SELECT 2;", "0001_a.sql": "SELECT 1;"},
    )
    store = FakeMigrationStore()
    first = _run(tmp_path, directory, store)
    assert first.code == 0
    assert first.out.splitlines() == ["applied 0001_a.sql", "applied 0002_b.sql"]
    assert first.err == ""
    second = _run(tmp_path, directory, store)
    assert second.code == 0
    assert second.out.splitlines() == ["no migrations pending"]


def test_blank_files_warn_on_stderr_and_count_as_nothing_pending(
    tmp_path: Path,
) -> None:
    directory = _migrations(tmp_path, {"0001_blank.sql": "  \n"})
    result = _run(tmp_path, directory, FakeMigrationStore())
    assert result.code == 0
    assert result.out.splitlines() == ["no migrations pending"]
    assert "0001_blank.sql" in result.err
    assert len(result.err.splitlines()) == 1


def test_packaged_migrations_apply_with_defaults_for_clock(tmp_path: Path) -> None:
    store = FakeMigrationStore()
    out, err = io.StringIO(), io.StringIO()
    code = migrate.main(
        environ=dict(ENV),
        store_factory=lambda _s: store,
        stdout=out,
        stderr=err,
        dotenv_start=tmp_path,
    )
    assert code == 0
    expected = [
        f"applied {m.name}" for m in discover(files("recromancer.db") / "migrations")
    ]
    assert expected[0] == "applied 0001_create_vector_extension.sql"
    assert out.getvalue().splitlines() == expected
    (_, applied_at) = store.recorded["0001_create_vector_extension.sql"]
    assert applied_at.tzinfo is not None
    assert applied_at.utcoffset() is not None
    assert applied_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_default_stdio_is_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    code = migrate.main(
        environ=dict(ENV),
        store_factory=lambda _s: FakeMigrationStore(),
        migrations_dir=directory,
        clock=FakeClock(),
        dotenv_start=tmp_path,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.splitlines() == ["applied 0001_a.sql"]
    assert captured.err == ""


def test_default_helpers_do_not_connect() -> None:
    settings = Settings.from_env(dict(ENV))
    assert isinstance(migrate._default_store(settings), PostgresMigrationStore)
    assert migrate._default_migrations_dir().is_dir()
    assert migrate._SystemClock().now().utcoffset() is not None


def test_config_error_exits_one(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _migrations(tmp_path, {}),
        FakeMigrationStore(),
        environ={"RECROMANCER_UNKNOWN": "x"},
    )
    assert result.code == 1
    assert result.out == ""
    assert "RECROMANCER_UNKNOWN" in result.err
    assert len(result.err.splitlines()) == 1
    assert "Traceback" not in result.err


def test_missing_directory_exits_one(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "nope", FakeMigrationStore())
    assert result.code == 1
    assert "migrations directory not found" in result.err
    assert len(result.err.splitlines()) == 1
    _assert_clean(result)


def test_checksum_mismatch_exits_one(tmp_path: Path) -> None:
    directory = _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    store = FakeMigrationStore()
    assert _run(tmp_path, directory, store).code == 0
    _migrations(tmp_path, {"0001_a.sql": "SELECT 11;"})
    result = _run(tmp_path, directory, store)
    assert result.code == 1
    assert "checksum mismatch: 0001_a.sql" in result.err
    assert result.out == ""
    _assert_clean(result)


def test_out_of_order_exits_one(tmp_path: Path) -> None:
    directory = _migrations(tmp_path, {"0002_b.sql": "SELECT 2;"})
    store = FakeMigrationStore()
    assert _run(tmp_path, directory, store).code == 0
    _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    result = _run(tmp_path, directory, store)
    assert result.code == 1
    assert "out-of-order migration: 0001_a.sql" in result.err
    _assert_clean(result)


def test_apply_failure_exits_one_and_keeps_earlier_output_off_stdout(
    tmp_path: Path,
) -> None:
    directory = _migrations(
        tmp_path,
        {"0001_a.sql": "SELECT 1;", "0002_bad.sql": f"{FAIL_MARKER}\nSELECT 2;"},
    )
    result = _run(tmp_path, directory, FakeMigrationStore())
    assert result.code == 1
    assert "0002_bad.sql" in result.err
    assert "simulated failure" in result.err
    assert result.out == ""
    _assert_clean(result)


def _leaky() -> str:
    return f"connect failed: password={DB_PASSWORD_DECODED} token={ADMIN_TOKEN}"


class LockFails(FakeMigrationStore):
    @contextmanager
    def locked(self) -> Iterator[None]:
        raise StoreError(_leaky())
        yield  # pragma: no cover


class EnsureFails(FakeMigrationStore):
    def ensure_table(self) -> None:
        raise StoreError(_leaky())


class AppliedFails(FakeMigrationStore):
    def applied(self) -> dict[str, str]:
        raise StoreError(_leaky())


@pytest.mark.parametrize("store_class", [LockFails, EnsureFails, AppliedFails])
def test_store_errors_outside_apply_are_redacted_and_exit_one(
    tmp_path: Path, store_class: type[FakeMigrationStore]
) -> None:
    directory = _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    result = _run(tmp_path, directory, store_class())
    assert result.code == 1
    assert result.out == ""
    assert result.err.startswith("database error: connect failed: password=***")
    assert "token=***" in result.err
    assert len(result.err.splitlines()) == 1
    _assert_clean(result)


def test_store_factory_connection_failure_is_redacted(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = migrate.main(
        environ=dict(ENV),
        store_factory=lambda _s: LockFails(),
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        migrations_dir=_migrations(tmp_path, {}),
        dotenv_start=tmp_path,
    )
    assert code == 1
    assert "password=***" in err.getvalue()
    assert DB_PASSWORD_DECODED not in err.getvalue()


def test_apply_error_message_embedding_secrets_is_redacted(tmp_path: Path) -> None:
    class ApplyLeaks(FakeMigrationStore):
        def apply(self, migration: object, applied_at: object) -> None:
            raise StoreError(_leaky())

    directory = _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    result = _run(tmp_path, directory, ApplyLeaks())
    assert result.code == 1
    assert result.err.startswith("migration failed: 0001_a.sql: connect failed")
    _assert_clean(result)


def test_warnings_are_redacted(tmp_path: Path) -> None:
    directory = _migrations(tmp_path, {"0001_" + "a" * 8 + ".sql": ""})
    environ = dict(ENV)
    environ["RECROMANCER_ADMIN_TOKEN"] = "a" * 8
    result = _run(tmp_path, directory, FakeMigrationStore(), environ)
    assert result.code == 0
    assert result.err == "empty migration skipped: 0001_***.sql\n"


def test_unexpected_exception_is_reported_by_class_name_only(tmp_path: Path) -> None:
    def boom(_settings: Settings) -> FakeMigrationStore:
        raise RuntimeError(_leaky())

    out, err = io.StringIO(), io.StringIO()
    code = migrate.main(
        environ=dict(ENV),
        store_factory=boom,
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        migrations_dir=_migrations(tmp_path, {}),
        dotenv_start=tmp_path,
    )
    assert code == 1
    assert err.getvalue() == "unexpected error: RuntimeError\n"
    assert out.getvalue() == ""


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://db.example.test/app",
        "postgresql://app:short@db.example.test/app",
    ],
)
def test_urls_without_a_long_password_still_run(tmp_path: Path, url: str) -> None:
    directory = _migrations(tmp_path, {"0001_a.sql": "SELECT 1;"})
    result = _run(
        tmp_path,
        directory,
        FakeMigrationStore(),
        environ={"RECROMANCER_DATABASE_URL": url},
    )
    assert result.code == 0
    assert result.out == "applied 0001_a.sql\n"


def test_injected_environ_and_dotenv_are_used(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text("", encoding="utf-8")
    (repo / ".env").write_text("RECROMANCER_BOGUS_FROM_DOTENV=1\n", encoding="utf-8")
    environ: dict[str, str] = {}
    out, err = io.StringIO(), io.StringIO()
    code = migrate.main(
        environ=environ,
        store_factory=lambda _s: FakeMigrationStore(),
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        migrations_dir=_migrations(tmp_path, {}),
        dotenv_start=repo,
    )
    assert environ["RECROMANCER_BOGUS_FROM_DOTENV"] == "1"
    assert code == 1
    assert "RECROMANCER_BOGUS_FROM_DOTENV" in err.getvalue()
