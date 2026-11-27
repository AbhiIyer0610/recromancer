"""Unit tests for the ingest CLI (`recromancer.ingest_cli.main`), driven by fakes."""

from __future__ import annotations

import importlib
import io
import json
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest

from recromancer.config import Settings
from recromancer.domain.errors import ErrorCode, RecromancerError
from recromancer.domain.evidence import ItemWrite
from recromancer.ingest_cli import USAGE, ledgerline_dir, main
from recromancer.service.ports import EvidenceStore
from tests.fakes import FakeClock, FakeEmbedder, FakeEvidenceStore

# Built at runtime so no secret-shaped literal sits in the source.
DB_PASSWORD = "Pw" + "x9yz" + "AB9q"
DB_URL = f"postgresql://app:{DB_PASSWORD}@db.example.test:5432/app"
BODY_SENTINEL = "zqxbodysentinel"
ENV: Mapping[str, str] = {"RECROMANCER_DATABASE_URL": DB_URL}
BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _restore_logging(log_stream: io.StringIO) -> None:
    """`main` reconfigures global logging; `log_stream` restores it afterwards."""


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


def _write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(data, indent=2).encode("utf-8") + b"\n")


def _commit(
    item_id: str, body: str, timestamp: str = "2026-01-02T03:04:05Z"
) -> dict[str, object]:
    return {
        "id": item_id,
        "kind": "commit",
        "author": "ada-test",
        "timestamp": timestamp,
        "title": f"title of {item_id}",
        "body": body,
        "references": [],
        "url": f"https://example.invalid/t/{item_id}",
    }


def _bundle(tmp_path: Path, bodies: Mapping[str, str], name: str = "bundle") -> Path:
    root = tmp_path / name
    files = []
    for item_id, body in bodies.items():
        _write(root / "commits" / f"{item_id}.json", _commit(item_id, body))
        files.append({"path": f"commits/{item_id}.json", "kind": "commit"})
    _write(
        root / "manifest.json",
        {
            "bundle_id": "tiny",
            "schema_version": 1,
            "subject": {"handle": "ada-test", "display_name": "Ada Test"},
            "fictional": True,
            "files": files,
        },
    )
    return root


def _invalid_bundle(tmp_path: Path) -> Path:
    root = _bundle(tmp_path, {"item-1": BODY_SENTINEL}, name="invalid")
    data = _commit("item-1", BODY_SENTINEL)
    data["timestamp"] = "not-a-timestamp"
    _write(root / "commits" / "item-1.json", data)
    return root


def _store(instance: EvidenceStore) -> object:
    @contextmanager
    def factory(_settings: Settings) -> Iterator[EvidenceStore]:
        yield instance

    return factory


def _run(
    args: list[str],
    tmp_path: Path,
    *,
    environ: dict[str, str] | None = None,
    store: EvidenceStore | None = None,
    store_factory: object = None,
    embedder: Callable[[Settings], FakeEmbedder] | None | str = "default",
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    factory = store_factory
    if factory is None:
        factory = _store(FakeEvidenceStore() if store is None else store)
    embedder_factory = None
    if embedder == "default":
        embedder_factory = lambda _s: FakeEmbedder()  # noqa: E731
    elif not isinstance(embedder, str):
        embedder_factory = embedder
    code = main(
        args,
        environ=dict(ENV) if environ is None else environ,
        store_factory=factory,  # type: ignore[arg-type]
        embedder_factory=embedder_factory,
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        dotenv_start=tmp_path / "no-repo-here",
    )
    return Result(code, out.getvalue(), err.getvalue())


def test_ledgerline_success_line_and_empty_stderr(tmp_path: Path) -> None:
    root = Path(str(ledgerline_dir()))
    result = _run([str(root)], tmp_path)
    assert result.code == 0
    assert result.err == ""
    lines = result.out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("items=31 chunks=")
    assert lines[0].endswith(" unchanged=0 absent=0")


def test_second_run_writes_no_chunks(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha", "item-2": "beta"})
    shared = FakeEvidenceStore()
    first = _run([str(root)], tmp_path, store=shared)
    second = _run([str(root)], tmp_path, store=shared)
    assert first.out == "items=2 chunks=2 unchanged=0 absent=0\n"
    assert second.out == "items=2 chunks=0 unchanged=2 absent=0\n"
    assert second.code == 0
    assert second.err == ""


def test_relative_path_resolves_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bundle(tmp_path, {"item-1": "alpha"})
    monkeypatch.chdir(tmp_path)
    result = _run(["bundle"], tmp_path)
    assert result.code == 0
    assert result.out == "items=1 chunks=1 unchanged=0 absent=0\n"


def test_absolute_path_is_accepted(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha"})
    result = _run([str(root.resolve())], tmp_path)
    assert result.code == 0
    assert result.out == "items=1 chunks=1 unchanged=0 absent=0\n"


@pytest.mark.skipif(sys.platform != "win32", reason="backslash separators are Windows")
def test_backslash_paths_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bundle(tmp_path, {"item-1": "alpha"}, name="sub" + "/" + "bundle")
    monkeypatch.chdir(tmp_path)
    result = _run(["sub\\bundle"], tmp_path)
    assert result.code == 0
    assert result.out == "items=1 chunks=1 unchanged=0 absent=0\n"


@pytest.mark.parametrize("args", [[], ["a", "b"]])
def test_wrong_argument_count_prints_usage_and_calls_no_factory(
    tmp_path: Path, args: list[str]
) -> None:
    calls: list[str] = []

    def store_factory(_s: Settings) -> object:
        calls.append("store")
        raise AssertionError("must not be called")

    def embedder_factory(_s: Settings) -> FakeEmbedder:
        calls.append("embedder")
        raise AssertionError("must not be called")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        args,
        environ=dict(ENV),
        store_factory=store_factory,  # type: ignore[arg-type]
        embedder_factory=embedder_factory,
        stdout=out,
        stderr=err,
        clock=FakeClock(),
        dotenv_start=tmp_path,
    )
    assert code == 1
    assert out.getvalue() == ""
    assert err.getvalue() == USAGE + "\n"
    assert USAGE == "usage: python -m recromancer.ingest_cli <bundle-dir>"
    assert calls == []


def test_no_embedder_configured_fails_closed(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha"})
    result = _run([str(root)], tmp_path, embedder=None)
    assert result.code == 1
    assert result.out == ""
    assert result.err == "no embedder is configured\n"


def test_unexpected_exception_is_reported_by_class_name_only(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha"})

    def boom(_s: Settings) -> FakeEmbedder:
        raise RuntimeError(f"password={DB_PASSWORD}")

    result = _run([str(root)], tmp_path, embedder=boom)
    assert result.code == 1
    assert result.out == ""
    assert result.err == "unexpected error: RuntimeError\n"


def test_environment_is_unchanged_without_dotenv(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha"})
    environ = dict(ENV)
    before = dict(environ)
    result = _run([str(root)], tmp_path, environ=environ)
    assert result.code == 0
    assert environ == before


def test_importable_and_runs_as_module() -> None:
    module = importlib.import_module("recromancer.ingest_cli")
    assert callable(module.main)
    completed = subprocess.run(
        [sys.executable, "-m", "recromancer.ingest_cli"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == USAGE


# ---- failure contract ----------------------------------------------------------


def _leaky() -> str:
    return f"connect failed: password={DB_PASSWORD} url={DB_URL} {quote(DB_PASSWORD)}"


def _failing_store_factory(_settings: Settings) -> object:
    raise RecromancerError(ErrorCode.DEPENDENCY_UNAVAILABLE, _leaky())


class _LeakyWriteStore(FakeEvidenceStore):
    async def replace_item(self, write: ItemWrite) -> None:
        raise RecromancerError(ErrorCode.DEPENDENCY_UNAVAILABLE, _leaky())


def _failing_embedder(_settings: Settings) -> FakeEmbedder:
    return FakeEmbedder(fail_on_call=1)


def _case_invalid_bundle(tmp_path: Path) -> Result:
    return _run([str(_invalid_bundle(tmp_path))], tmp_path)


def _case_store_failure(tmp_path: Path) -> Result:
    root = _bundle(tmp_path, {"item-1": BODY_SENTINEL})
    return _run([str(root)], tmp_path, store_factory=_failing_store_factory)


def _case_store_write_failure(tmp_path: Path) -> Result:
    root = _bundle(tmp_path, {"item-1": BODY_SENTINEL})
    return _run([str(root)], tmp_path, store=_LeakyWriteStore())


def _case_embedder_failure(tmp_path: Path) -> Result:
    root = _bundle(tmp_path, {"item-1": BODY_SENTINEL})
    return _run([str(root)], tmp_path, embedder=_failing_embedder)


def _case_missing_directory(tmp_path: Path) -> Result:
    return _run([str(tmp_path / "nope")], tmp_path)


def _case_file_not_directory(tmp_path: Path) -> Result:
    target = tmp_path / "afile"
    target.write_bytes(b"x")
    return _run([str(target)], tmp_path)


def _case_invalid_settings(tmp_path: Path) -> Result:
    root = _bundle(tmp_path, {"item-1": BODY_SENTINEL})
    return _run([str(root)], tmp_path, environ={"RECROMANCER_UNKNOWN": "x"})


FAILURE_CASES = {
    "invalid-bundle": _case_invalid_bundle,
    "store-failure": _case_store_failure,
    "store-write-failure": _case_store_write_failure,
    "embedder-failure": _case_embedder_failure,
    "missing-directory": _case_missing_directory,
    "file-not-directory": _case_file_not_directory,
    "invalid-settings": _case_invalid_settings,
}


@pytest.mark.parametrize("case", list(FAILURE_CASES))
def test_failure_prints_one_redacted_line_without_body(
    tmp_path: Path, case: str
) -> None:
    result = FAILURE_CASES[case](tmp_path)
    assert result.code == 1
    assert result.out == ""
    assert len(result.err.splitlines()) == 1
    assert result.err.endswith("\n")
    assert "Traceback" not in result.err
    for secret in (BODY_SENTINEL, DB_PASSWORD, quote(DB_PASSWORD), DB_URL):
        assert secret not in result.err


def test_store_failure_message_is_redacted_not_dropped(tmp_path: Path) -> None:
    result = _case_store_failure(tmp_path)
    assert result.err.startswith("connect failed: password=***")


@pytest.mark.parametrize("case", ["embedder-failure", "store-write-failure"])
def test_ingest_failure_stderr_is_one_line_even_with_logging_configured(
    tmp_path: Path, case: str
) -> None:
    """The service logs `ingest_failed` at ERROR; that must not add a stderr line."""
    result = FAILURE_CASES[case](tmp_path)
    assert result.code == 1
    assert result.out == ""
    lines = result.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("ingest failed at item item-1: ")
    assert "ingest_failed" not in result.err
    assert not lines[0].startswith("{")


def test_success_leaves_stderr_empty_and_stdout_to_one_line(tmp_path: Path) -> None:
    root = _bundle(tmp_path, {"item-1": "alpha", "item-2": "beta"})
    result = _run([str(root)], tmp_path)
    assert result.code == 0
    assert result.err == ""
    assert len(result.out.splitlines()) == 1
    assert "ingest_completed" not in result.out
