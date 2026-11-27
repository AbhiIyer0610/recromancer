"""Ingest command line: ``python -m recromancer.ingest_cli <bundle-dir>``.

``make_fetch`` is the disk reader behind the pure Bundle_Reader. It lives here,
not in ``ingest``, because it does disk I/O (contract C2).

``main`` order of work: argument count, ``load_dotenv``, ``Settings.from_env``,
logging, bundle directory check, embedder factory, store, service, ingest. On
success it prints ``items=<n> chunks=<n> unchanged=<n> absent=<n>`` on stdout and
nothing on stderr. Any failure prints exactly one redacted line on stderr, nothing
on stdout, and returns 1.

Logging: ``configure_logging`` routes library logs to the same stderr, and the
ingestion service logs ``ingest_failed`` at ERROR. To keep the one-line failure
contract (and an empty stderr on success), the CLI configures the library log level
at CRITICAL. The CLI's own result line is the user-facing report; the service's
structured events are for the long-running API process, not this command.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, MutableMapping, Sequence
from contextlib import AbstractContextManager
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TextIO

from recromancer.bootstrap import SystemClock, build_ingestion_service, evidence_store
from recromancer.config import ConfigError, Settings, load_dotenv
from recromancer.domain.errors import BundleError, RecromancerError
from recromancer.domain.evidence import FetchBytes
from recromancer.observability import configure_logging, redact_text
from recromancer.service.ports import Clock, Embedder, EvidenceStore

USAGE = "usage: python -m recromancer.ingest_cli <bundle-dir>"
_NO_EMBEDDER = "no embedder is configured"
_LIBRARY_LOG_LEVEL = "CRITICAL"


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


def _one_line(text: str, secrets: Sequence[str]) -> str:
    return " ".join(redact_text(text, secrets).split())


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    store_factory: Callable[[Settings], AbstractContextManager[EvidenceStore]]
    | None = None,
    embedder_factory: Callable[[Settings], Embedder] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    clock: Clock | None = None,
    dotenv_start: Path | None = None,
) -> int:
    """Ingest one bundle directory. Returns the process exit code (0 or 1)."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    args = list(sys.argv[1:] if argv is None else argv)
    secrets: tuple[str, ...] = ()

    def fail(message: str) -> int:
        err.write(_one_line(message, secrets) + "\n")
        err.flush()
        return 1

    if len(args) != 1:
        return fail(USAGE)

    try:
        load_dotenv(environ=environ, start=dotenv_start)
        settings = Settings.from_env(environ)
        secrets = settings.redaction_values()
        use_clock = SystemClock() if clock is None else clock
        configure_logging(
            level=_LIBRARY_LOG_LEVEL,
            clock=use_clock,
            secret_values=secrets,
            stream=err,
        )
        root = Path(args[0]).resolve()
        if not root.is_dir():
            return fail("bundle directory not found")
        if embedder_factory is None:
            return fail(_NO_EMBEDDER)
        factory = evidence_store if store_factory is None else store_factory
        with factory(settings) as store:
            embedder = embedder_factory(settings)
            service = build_ingestion_service(
                settings, store=store, embedder=embedder, clock=use_clock
            )
            report = asyncio.run(service.ingest(make_fetch(root)))
    except ConfigError as exc:
        return fail(f"configuration error: {exc.message}")
    except RecromancerError as exc:
        return fail(exc.message)
    except Exception as exc:
        return fail(f"unexpected error: {type(exc).__name__}")

    out.write(
        f"items={report.items} chunks={report.chunks} "
        f"unchanged={report.unchanged} absent={report.absent}\n"
    )
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
