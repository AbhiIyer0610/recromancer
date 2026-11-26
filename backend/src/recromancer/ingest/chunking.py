"""Pure chunker: splits an evidence item's source text into overlapping chunks (A3)."""

from recromancer.domain.errors import ChunkingError
from recromancer.domain.evidence import Chunk, EvidenceItem, SourceLocator

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_ITEM = 10_000
_MAX_WINDOW = 200


def source_text(item: EvidenceItem) -> str:
    """Return the title, or title + blank line + body when the body is not empty."""
    if item.body == "":
        return item.title
    return item.title + "\n\n" + item.body


def _find_end(text: str, lo: int, hi: int) -> int:
    i = text.rfind("\n\n", lo, hi)
    if i != -1:
        return i + 2
    j = text.rfind("\n", lo, hi)
    if j != -1:
        return j + 1
    for k in range(hi - 1, lo - 1, -1):
        if text[k].isspace():
            return k + 1
    return hi


def chunk_item(
    item: EvidenceItem,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[Chunk, ...]:
    """Split ``source_text(item)`` into chunks of at most ``size`` code points."""
    if size < 1:
        raise ChunkingError("chunk size must be at least 1")
    if overlap < 0:
        raise ChunkingError("chunk overlap must not be negative")
    if overlap >= size:
        raise ChunkingError("chunk overlap must be smaller than chunk size")
    text = source_text(item)
    n = len(text)
    if n == 0:
        return ()
    window = min(_MAX_WINDOW, size - overlap - 1)
    start = 0
    chunks: list[Chunk] = []
    while True:
        final = n - start <= size
        if final:
            end = n
        else:
            end = _find_end(text, start + size - window, start + size)
        position = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{item.id}#{position:04d}",
                position=position,
                item_id=item.id,
                kind=item.kind,
                author=item.author,
                timestamp=item.timestamp,
                text=text[start:end],
                locator=SourceLocator(
                    item_id=item.id,
                    start_offset=start,
                    end_offset=end,
                    url=item.url,
                    path=item.path,
                ),
            )
        )
        if len(chunks) > MAX_CHUNKS_PER_ITEM:
            raise ChunkingError("item produces more than 10000 chunks")
        if final:
            break
        start = end - overlap
    return tuple(chunks)
