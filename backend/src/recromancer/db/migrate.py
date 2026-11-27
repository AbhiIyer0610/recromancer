"""Migration CLI: ``python -m recromancer.db.migrate``.

Flow: ``load_dotenv`` -> ``Settings.from_env`` -> build the store -> ``discover`` ->
``run``. Prints one ``applied <file>`` line per applied file, or ``no migrations
pending``, and exits 0. Any failure prints one redacted line on stderr and exits 1:
``ConfigError``, ``MigrationError``, any ``StoreError`` (``run`` only wraps those raised
by ``apply``; lock, connect, ``ensure_table`` and ``applied`` failures arrive here
unwrapped) and, as a last resort, any other exception, reported by class name only.

Every stderr line goes through ``redact_text`` seeded with ``settings.secret_values()``.
A ``ConfigError`` raised before settings exist is printed with no secrets registered;
that is safe because ``ConfigError`` text never contains configuration values.

This is the only ``db`` module that may import ``config`` and ``observability``.
"""

import sys
from collections.abc import Callable, Iterable, MutableMapping, Sequence
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Protocol, TextIO

from recromancer.config import ConfigError, Settings, load_dotenv
from recromancer.db.postgres import PostgresMigrationStore
from recromancer.db.runner import (
    MigrationError,
    MigrationStore,
    StoreError,
    discover,
    run,
)
from recromancer.observability import redact_text

__all__ = ["main"]

_NOTHING_PENDING = "no migrations pending"


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _default_store(settings: Settings) -> MigrationStore:
    return PostgresMigrationStore(settings.database_url)


def _default_migrations_dir() -> Traversable:
    return resources.files("recromancer.db") / "migrations"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    store_factory: Callable[[Settings], MigrationStore] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    clock: _Clock | None = None,
    migrations_dir: Traversable | None = None,
    dotenv_start: Path | None = None,
) -> int:
    """Apply pending migrations. Returns the process exit code (0 or 1).

    ``argv`` is accepted for the standard entry-point shape and is not used.
    ``dotenv_start`` is where the ``.env.example`` search starts (default: the cwd).
    """
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    secrets: tuple[str, ...] = ()

    def emit_error(message: str) -> int:
        err.write(_one_line(redact_text(message, secrets)) + "\n")
        err.flush()
        return 1

    def warn(message: str) -> None:
        err.write(_one_line(redact_text(message, secrets)) + "\n")

    try:
        load_dotenv(environ=environ, start=dotenv_start)
        settings = Settings.from_env(environ)
        secrets = settings.redaction_values()
        factory = _default_store if store_factory is None else store_factory
        store = factory(settings)
        migrations = discover(
            _default_migrations_dir() if migrations_dir is None else migrations_dir
        )
        report = run(
            store, migrations, _SystemClock() if clock is None else clock, warn
        )
    except ConfigError as exc:
        return emit_error(f"configuration error: {exc.message}")
    except MigrationError as exc:
        return emit_error(f"migration failed: {exc.message}")
    except StoreError as exc:
        return emit_error(f"database error: {exc}")
    except Exception as exc:
        return emit_error(f"unexpected error: {type(exc).__name__}")

    _print_lines(out, report.applied)
    if not report.applied:
        out.write(_NOTHING_PENDING + "\n")
    out.flush()
    return 0


def _print_lines(out: TextIO, applied: Iterable[str]) -> None:
    for name in applied:
        out.write(f"applied {name}\n")


if __name__ == "__main__":
    raise SystemExit(main())
