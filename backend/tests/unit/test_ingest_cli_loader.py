"""BL: the Bundle_Loader ``make_fetch`` and ``ledgerline_dir`` (R1.12, R2.11, R7.7)."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import IO, Any

import pytest

from recromancer.domain.errors import BundleError
from recromancer.ingest_cli import ledgerline_dir, make_fetch


def test_ledgerline_dir_shape() -> None:
    node = ledgerline_dir()
    assert node.name == "ledgerline"
    parent = Path(str(node)).parent
    assert parent.name == "fixtures"
    assert parent.parent.name == "ingest"


def test_reads_file_under_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.json").write_bytes(b"hello")
    assert make_fetch(tmp_path)("sub/a.json", 100) == b"hello"


def test_size_cap_reads_at_most_max_plus_one(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * 1000)
    assert make_fetch(tmp_path)("big.bin", 10) == b"x" * 11
    assert make_fetch(tmp_path)("big.bin", 1000) == b"x" * 1000
    assert make_fetch(tmp_path)("big.bin", 0) == b"x"


class _SpyRoot:
    """A Traversable-like root that records every joinpath and open."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def joinpath(self, *parts: str) -> Any:
        self.calls.append("joinpath")
        raise AssertionError("no read may happen for an unsafe path")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "\\evil.json",
        "a\\b.json",
        "a//b.json",
        "a/",
        "../x.json",
        "a/../x.json",
        "a/..",
        "C:/x.json",
        "C:x.json",
    ],
)
def test_unsafe_paths_are_rejected_before_any_read(path: str) -> None:
    spy = _SpyRoot()
    fetch = make_fetch(spy)  # type: ignore[arg-type]
    with pytest.raises(BundleError):
        fetch(path, 10)
    assert spy.calls == []


def test_backslash_path_under_tmp_path_touches_nothing(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_bytes(b"ok")
    with pytest.raises(BundleError, match="unsafe bundle path"):
        make_fetch(tmp_path)("..\\ok.txt", 10)
    with pytest.raises(BundleError, match="unsafe bundle path"):
        make_fetch(tmp_path)("sub\\ok.txt", 10)


def test_missing_file_maps_without_os_text(tmp_path: Path) -> None:
    with pytest.raises(BundleError) as info:
        make_fetch(tmp_path)("nope.json", 10)
    assert str(info.value) == "cannot read bundle file nope.json"


def test_directory_maps_without_os_text(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    with pytest.raises(BundleError) as info:
        make_fetch(tmp_path)("d", 10)
    assert str(info.value) == "cannot read bundle file d"


class _FailingFile:
    def __init__(self, exc: OSError) -> None:
        self._exc = exc

    def open(self, mode: str = "r") -> IO[bytes]:
        raise self._exc

    def joinpath(self, *parts: str) -> _FailingFile:
        return self


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(13, "SECRET-OS-TEXT"),
        IsADirectoryError(21, "SECRET-OS-TEXT"),
        FileNotFoundError(2, "SECRET-OS-TEXT"),
        OSError(5, "SECRET-OS-TEXT"),
    ],
)
def test_os_errors_map_to_bundle_error_without_os_text(exc: OSError) -> None:
    fetch = make_fetch(_FailingFile(exc))  # type: ignore[arg-type]
    with pytest.raises(BundleError) as info:
        fetch("a/b.json", 10)
    assert str(info.value) == "cannot read bundle file a/b.json"
    assert "SECRET-OS-TEXT" not in str(info.value)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {type(exc).__name__}")
    with pytest.raises(BundleError, match="unsafe bundle path"):
        make_fetch(root)("link/secret.txt", 100)


class _MemFile:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def open(self, mode: str = "r") -> IO[bytes]:
        import io

        assert mode == "rb"
        return io.BytesIO(self._data)


class _MemDir:
    """A Traversable that is not a ``pathlib.Path``."""

    def __init__(self, files: dict[tuple[str, ...], bytes]) -> None:
        self._files = files

    def joinpath(self, *parts: str) -> Any:
        return _MemFile(self._files[tuple(parts)])

    def iterdir(self) -> Iterator[Traversable]:  # pragma: no cover
        raise NotImplementedError


def test_non_path_traversable_is_read_without_resolve() -> None:
    root = _MemDir({("commits", "c1.json"): b"0123456789"})
    fetch = make_fetch(root)  # type: ignore[arg-type]
    assert fetch("commits/c1.json", 4) == b"01234"
