"""Small deterministic fakes shared by the logging and API unit tests."""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio

# Loggers that belong to the HTTP test client, not to the application under test.
CLIENT_LOGGER_PREFIXES = ("httpx", "httpcore")


class FakeClock:
    """Clock whose time moves only when the test says so.

    ``now()`` returns ``current``. ``monotonic()`` returns ``mono`` and then adds
    ``step`` (negative steps model a clock that goes backwards).
    """

    def __init__(self, start: datetime | None = None, *, step: float = 0.0) -> None:
        self.current = start or datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
        self.mono = 10.0
        self.step = step

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)

    def monotonic(self) -> float:
        value = self.mono
        self.mono += self.step
        return value


def counter_ids(prefix: str = "gen-id") -> Callable[[], str]:
    """Return an id generator producing ``<prefix>-0001``, ``<prefix>-0002``, ..."""
    counter = itertools.count(1)
    return lambda: f"{prefix}-{next(counter):04d}"


class FakeProbe:
    """Readiness probe with a fixed mode: ready, not_ready, raise or hang."""

    def __init__(self, mode: str = "ready", message: str = "probe failed") -> None:
        self.mode = mode
        self.message = message
        self.calls = 0

    async def is_ready(self) -> bool:
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError(self.message)
        if self.mode == "hang":
            await anyio.sleep_forever()
        return self.mode == "ready"


def log_records(stream: io.StringIO) -> list[dict[str, Any]]:
    """Parse the JSON log lines of the application, dropping the test client's.

    ``TestClient`` logs each request, with the full URL and query string, through
    the ``httpx2`` logger. Those lines belong to the client, so every assertion
    about the application's logging looks at all other loggers only.
    """
    records: list[dict[str, Any]] = [
        json.loads(line) for line in stream.getvalue().splitlines()
    ]
    return [
        record
        for record in records
        if not str(record.get("logger", "")).startswith(CLIENT_LOGGER_PREFIXES)
    ]


def records_for(stream: io.StringIO, logger: str) -> list[dict[str, Any]]:
    """Return the application log records written by one logger name."""
    return [r for r in log_records(stream) if r.get("logger") == logger]


def app_output(stream: io.StringIO) -> str:
    """Return the application's log lines as one text, for absence checks."""
    return "\n".join(json.dumps(r) for r in log_records(stream))
