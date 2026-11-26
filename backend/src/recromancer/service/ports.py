"""Ports the service layer depends on. Adapters satisfy them structurally."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from recromancer.domain.evidence import ItemWrite, StoredChunk, StoredItem


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


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per text, in order. `[]` for `[]`."""
        ...


class EvidenceStore(Protocol):
    async def signatures(self, bundle_id: str) -> dict[str, str]:
        """Map item_id to its stored ingest signature."""
        ...

    async def replace_item(self, write: ItemWrite) -> None:
        """Replace one item and its chunks atomically."""
        ...

    async def get_item(self, bundle_id: str, item_id: str) -> StoredItem | None: ...

    async def get_chunks(
        self, bundle_id: str, item_id: str
    ) -> tuple[StoredChunk, ...]: ...
