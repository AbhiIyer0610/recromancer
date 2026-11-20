"""Ports the service layer depends on. Adapters satisfy them structurally."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol


class ReadinessProbe(Protocol):
    async def is_ready(self) -> bool:
        """Return True when the database answers."""
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time, timezone-aware, in UTC."""
        ...

    def monotonic(self) -> float: ...


IdGenerator = Callable[[], str]


class DbDiagnostics(Protocol):
    """Used by the doctor script only."""

    def ping(self) -> None:
        """Raise on failure."""
        ...

    def has_extension(self, name: str) -> bool: ...
