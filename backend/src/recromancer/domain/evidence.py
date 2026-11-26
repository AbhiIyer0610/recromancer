"""Evidence domain types shared by ingestion, storage and retrieval. Stdlib only."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ItemKind = Literal["commit", "pull_request", "review", "issue", "doc"]
DEFAULT_WORKSPACE_ID = "default"

# (bundle-relative path, max_bytes) -> at most max_bytes + 1 bytes
FetchBytes = Callable[[str, int], bytes]


@dataclass(frozen=True)
class Subject:
    handle: str
    display_name: str


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: ItemKind
    author: str
    timestamp: datetime  # aware, UTC, whole seconds
    title: str
    body: str
    url: str | None  # exactly one of url and path is not None
    path: str | None
    references: tuple[str, ...]
    content_hash: str  # set only by read_bundle


@dataclass(frozen=True)
class Bundle:
    bundle_id: str
    subject: Subject
    fictional: bool
    items: tuple[EvidenceItem, ...]


@dataclass(frozen=True)
class SourceLocator:
    item_id: str
    start_offset: int
    end_offset: int
    url: str | None
    path: str | None


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    position: int
    item_id: str
    kind: ItemKind
    author: str
    timestamp: datetime
    text: str
    locator: SourceLocator


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    vector: tuple[float, ...]
    model_id: str


@dataclass(frozen=True)
class ItemWrite:
    """The whole input of the atomic per-item replace."""

    bundle_id: str
    subject_handle: str
    item: EvidenceItem
    signature: str
    ingested_at: datetime
    chunks: tuple[EmbeddedChunk, ...]


@dataclass(frozen=True)
class StoredItem:
    bundle_id: str
    subject_handle: str
    item: EvidenceItem
    signature: str
    ingested_at: datetime


@dataclass(frozen=True)
class StoredChunk:
    bundle_id: str
    chunk: Chunk
    vector: tuple[float, ...]
    embedding_model: str
