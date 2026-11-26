"""Ingest command line entry point (Bundle_Loader part only).

``make_fetch`` is the disk reader behind the pure Bundle_Reader. It lives here,
not in ``ingest``, because it does disk I/O (contract C2).
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from recromancer.domain.errors import BundleError
from recromancer.domain.evidence import FetchBytes


def ledgerline_dir() -> Traversable:
    """Return the packaged Ledgerline sample bundle directory."""
    return resources.files("recromancer.ingest") / "fixtures" / "ledgerline"


def _unreadable(path: str) -> BundleError:
    return BundleError(f"cannot read bundle file {path}")


def _unsafe(path: str) -> BundleError:
    return BundleError(f"unsafe bundle path {path}")


def make_fetch(root: Traversable) -> FetchBytes:
    """Build a ``FetchBytes`` that reads bundle-relative files under ``root``.

    Unsafe paths are rejected before any read. OS error text never reaches the
    error message.
    """
    real_root = root.resolve() if isinstance(root, Path) else None

    def fetch(path: str, max_bytes: int) -> bytes:
        if not path or path.startswith("/") or "\\" in path:
            raise _unsafe(path)
        segments = path.split("/")
        if any(segment in ("", "..") for segment in segments):
            raise _unsafe(path)
        if Path(path).is_absolute() or Path(path).drive:
            raise _unsafe(path)
        try:
            node = root.joinpath(*segments)
            if real_root is not None and isinstance(node, Path):
                resolved = node.resolve()
                if resolved != real_root and not resolved.is_relative_to(real_root):
                    raise _unsafe(path)
            with node.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        except BundleError:
            raise
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            raise _unreadable(path) from None
        return bytes(data)

    return fetch
