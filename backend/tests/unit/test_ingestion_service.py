"""IngestionService tests (IS, EX-REINGEST) over in-memory fakes. No network."""

import ast
import dataclasses
import functools
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from recromancer import observability as obs
from recromancer.domain.errors import BundleError, ErrorCode
from recromancer.ingest.bundle import read_bundle
from recromancer.ingest.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_item,
)
from recromancer.service import ingestion
from recromancer.service.ingestion import (
    IngestError,
    IngestionService,
    IngestReport,
    ingest_signature,
)
from tests.fakes import FakeClock, FakeEmbedder, FakeEvidenceStore

pytestmark = pytest.mark.anyio

Files = dict[str, bytes]
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class FakeFetch:
    def __init__(self, files: Files) -> None:
        self.files = files

    def __call__(self, path: str, max_bytes: int) -> bytes:
        try:
            return self.files[path][: max_bytes + 1]
        except KeyError:
            raise FileNotFoundError(path) from None


def make_files(
    items: dict[str, tuple[str, str]], bundle_id: str = "bundle-one"
) -> Files:
    """Commit items by id -> (title, body), in dict order."""
    manifest = {
        "bundle_id": bundle_id,
        "schema_version": 1,
        "subject": {"handle": "ghost", "display_name": "Gus Host"},
        "fictional": True,
        "files": [{"path": f"items/{i}.json", "kind": "commit"} for i in items],
    }
    files: Files = {"manifest.json": json.dumps(manifest).encode()}
    for n, (item_id, (title, body)) in enumerate(items.items()):
        files[f"items/{item_id}.json"] = json.dumps(
            {
                "id": item_id,
                "kind": "commit",
                "author": "ghost",
                "timestamp": f"2026-01-01T00:00:{n:02d}Z",
                "title": title,
                "body": body,
                "url": f"https://example.invalid/{item_id}",
                "references": [],
            }
        ).encode()
    return files


def fetch_for(
    items: dict[str, tuple[str, str]], bundle_id: str = "bundle-one"
) -> FakeFetch:
    return FakeFetch(make_files(items, bundle_id))


def make_service(
    store: FakeEvidenceStore,
    embedder: FakeEmbedder | None = None,
    clock: FakeClock | None = None,
) -> IngestionService:
    return IngestionService(
        read=read_bundle,
        chunk=functools.partial(
            chunk_item, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP
        ),
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        embedder=embedder or FakeEmbedder(),
        store=store,
        clock=clock or FakeClock(),
    )


THREE = {
    "item-a": ("Title A", "Body A"),
    "item-b": ("Title B", "Body B"),
    "item-c": ("Title C", "Body C"),
}


def test_ingest_signature_format() -> None:
    assert ingest_signature("abc", 1000, 150, "m") == "v1|abc|1000|150|m"


def test_ingest_error_shape() -> None:
    err = IngestError("item-x", IngestReport(1, 2, 3, 4))
    assert (
        str(err)
        == "ingest failed at item item-x: items=1 chunks=2 unchanged=3 absent=4"
    )
    assert err.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert err.item_id == "item-x"
    assert err.report == IngestReport(1, 2, 3, 4)


async def test_first_ingest_writes_every_item() -> None:
    store = FakeEvidenceStore()
    embedder = FakeEmbedder()
    report = await make_service(store, embedder).ingest(fetch_for(THREE))
    assert report == IngestReport(items=3, chunks=3, unchanged=0, absent=0)
    assert embedder.calls == 3
    for item_id in THREE:
        stored = await store.get_item("bundle-one", item_id)
        assert stored is not None
        assert stored.subject_handle == "ghost"
        chunks = await store.get_chunks("bundle-one", item_id)
        assert [c.chunk.chunk_id for c in chunks] == [f"{item_id}#0000"]
        assert chunks[0].embedding_model == "fake-embed-v1"


async def test_stored_ingested_at_equals_fake_clock() -> None:
    store = FakeEvidenceStore()
    clock = FakeClock(datetime(2031, 5, 6, 7, 8, 9, tzinfo=UTC))
    await make_service(store, clock=clock).ingest(fetch_for(THREE))
    for item_id in THREE:
        stored = await store.get_item("bundle-one", item_id)
        assert stored is not None
        assert stored.ingested_at == clock.now()


async def test_second_run_skips_unchanged_items() -> None:
    store = FakeEvidenceStore()
    embedder = FakeEmbedder()
    service = make_service(store, embedder)
    await service.ingest(fetch_for(THREE))
    calls = embedder.calls
    report = await service.ingest(fetch_for(THREE))
    assert report == IngestReport(items=3, chunks=0, unchanged=3, absent=0)
    assert embedder.calls == calls


async def test_changed_item_is_replaced_and_others_are_skipped() -> None:
    store = FakeEvidenceStore()
    embedder = FakeEmbedder()
    clock = FakeClock()
    service = make_service(store, embedder, clock)
    await service.ingest(fetch_for(THREE))
    first_at = clock.now()
    clock.advance(hours=1)
    edited = {**THREE, "item-b": ("Title B", "Body B changed")}
    report = await service.ingest(fetch_for(edited))
    assert report == IngestReport(items=3, chunks=1, unchanged=2, absent=0)
    b = await store.get_item("bundle-one", "item-b")
    a = await store.get_item("bundle-one", "item-a")
    assert b is not None and a is not None
    assert b.item.body == "Body B changed"
    assert b.ingested_at == first_at + timedelta(hours=1)
    assert a.ingested_at == first_at


async def test_embedder_failure_stops_and_keeps_earlier_items() -> None:
    store = FakeEvidenceStore()
    service = make_service(store, FakeEmbedder(fail_on_call=2))
    with pytest.raises(IngestError) as err:
        await service.ingest(fetch_for(THREE))
    assert str(err.value) == (
        "ingest failed at item item-b: items=1 chunks=1 unchanged=0 absent=0"
    )
    assert err.value.item_id == "item-b"
    assert err.value.report == IngestReport(1, 1, 0, 0)
    assert err.value.__cause__ is None
    assert await store.get_item("bundle-one", "item-a") is not None
    assert await store.get_item("bundle-one", "item-b") is None
    assert await store.get_item("bundle-one", "item-c") is None


async def test_store_failure_stops_and_keeps_earlier_items() -> None:
    store = FakeEvidenceStore(fail_on_item_ids=frozenset({"item-c"}))
    with pytest.raises(IngestError) as err:
        await make_service(store).ingest(fetch_for(THREE))
    assert str(err.value) == (
        "ingest failed at item item-c: items=2 chunks=2 unchanged=0 absent=0"
    )
    assert await store.get_item("bundle-one", "item-b") is not None
    assert await store.get_item("bundle-one", "item-c") is None
    assert await store.get_chunks("bundle-one", "item-c") == ()


async def test_failed_item_keeps_its_old_state() -> None:
    store = FakeEvidenceStore()
    await make_service(store).ingest(fetch_for(THREE))
    before = await store.get_item("bundle-one", "item-b")
    before_chunks = await store.get_chunks("bundle-one", "item-b")
    edited = {**THREE, "item-b": ("Title B", "Body B changed")}
    service = make_service(store, FakeEmbedder(fail_on_call=1))
    with pytest.raises(IngestError) as err:
        await service.ingest(fetch_for(edited))
    assert err.value.item_id == "item-b"
    assert err.value.report == IngestReport(1, 0, 1, 0)
    assert await store.get_item("bundle-one", "item-b") == before
    assert await store.get_chunks("bundle-one", "item-b") == before_chunks


async def test_wrong_vector_count_is_an_ingest_error() -> None:
    class ShortEmbedder(FakeEmbedder):
        async def embed(self, texts):  # type: ignore[no-untyped-def]
            return (await super().embed(texts))[:-1]

    store = FakeEvidenceStore()
    with pytest.raises(IngestError):
        await make_service(store, ShortEmbedder()).ingest(fetch_for(THREE))
    assert store.snapshot() == ((), ())


async def test_absent_items_are_kept_and_counted() -> None:
    store = FakeEvidenceStore()
    service = make_service(store)
    await service.ingest(fetch_for(THREE))
    smaller = {k: v for k, v in THREE.items() if k != "item-c"}
    report = await service.ingest(fetch_for(smaller))
    assert report == IngestReport(items=2, chunks=0, unchanged=2, absent=1)
    assert await store.get_item("bundle-one", "item-c") is not None


async def test_other_bundles_are_untouched() -> None:
    store = FakeEvidenceStore()
    service = make_service(store)
    await service.ingest(fetch_for(THREE, "bundle-one"))
    other = {"item-z": ("Title Z", "Body Z")}
    report = await service.ingest(fetch_for(other, "bundle-two"))
    assert report == IngestReport(items=1, chunks=1, unchanged=0, absent=0)
    assert set(await store.signatures("bundle-one")) == set(THREE)
    assert set(await store.signatures("bundle-two")) == {"item-z"}


async def test_invalid_bundle_raises_bundle_error_and_writes_nothing() -> None:
    files = make_files(THREE)
    del files["items/item-b.json"]
    store = FakeEvidenceStore()
    embedder = FakeEmbedder()
    with pytest.raises(BundleError):
        await make_service(store, embedder).ingest(FakeFetch(files))
    assert store.snapshot() == ((), ())
    assert embedder.calls == 0


def test_module_imports_no_random_or_uuid() -> None:
    tree = ast.parse(Path(ingestion.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"random", "uuid"}


# ---- EX-REINGEST -------------------------------------------------------------


def _normalised(store: FakeEvidenceStore) -> tuple[object, object]:
    items, chunks = store.snapshot()
    return (
        tuple(
            (*row[:3], dataclasses.replace(row[3], ingested_at=EPOCH)) for row in items
        ),
        chunks,
    )


LONG_BODY = " ".join(f"word{n}" for n in range(1200))  # about 7800 characters
SHORT_BODY = "short body"


@pytest.mark.parametrize(
    ("before", "after"),
    [(SHORT_BODY, LONG_BODY), (LONG_BODY, SHORT_BODY)],
    ids=["grow", "shrink"],
)
async def test_reingest_edited_item_equals_fresh_store(before: str, after: str) -> None:
    first = {**THREE, "item-b": ("Title B", before)}
    second = {**THREE, "item-b": ("Title B", after)}
    store = FakeEvidenceStore()
    embedder = FakeEmbedder()
    clock = FakeClock()
    service = make_service(store, embedder, clock)
    await service.ingest(fetch_for(first))
    old_count = len(await store.get_chunks("bundle-one", "item-b"))
    texts_before = embedder.texts_embedded
    calls_before = embedder.calls
    clock.advance(minutes=5)

    report = await service.ingest(fetch_for(second))

    fresh = FakeEvidenceStore()
    await make_service(fresh).ingest(fetch_for(second))
    new_count = len(await fresh.get_chunks("bundle-one", "item-b"))
    assert (new_count > old_count) if after is LONG_BODY else (new_count < old_count)
    assert _normalised(store) == _normalised(fresh)
    stored_ids = [
        c.chunk.chunk_id for c in await store.get_chunks("bundle-one", "item-b")
    ]
    assert stored_ids == [f"item-b#{n:04d}" for n in range(new_count)]
    assert report == IngestReport(items=3, chunks=new_count, unchanged=2, absent=0)
    assert embedder.calls == calls_before + 1
    assert embedder.texts_embedded == texts_before + new_count


# ---- R6.9 ---------------------------------------------------------------------


async def test_logs_contain_no_title_body_or_chunk_text(
    log_stream: io.StringIO,
) -> None:
    obs.configure_logging(level="DEBUG", clock=FakeClock(), stream=log_stream)
    items = {
        f"item-{n}": (f"zqxtitle{n}sentinel", f"zqxbody{n}sentinel text")
        for n in range(3)
    }
    await make_service(FakeEvidenceStore()).ingest(fetch_for(items))
    success_output = log_stream.getvalue()
    assert "ingest_completed" in success_output
    assert "ingest_item_written" in success_output

    log_stream.truncate(0)
    log_stream.seek(0)
    service = make_service(FakeEvidenceStore(), FakeEmbedder(fail_on_call=2))
    with pytest.raises(IngestError):
        await service.ingest(fetch_for(items))
    failure_output = log_stream.getvalue()
    assert "ingest_failed" in failure_output

    for output in (success_output, failure_output):
        assert "zqxtitle" not in output
        assert "zqxbody" not in output
        assert "sentinel" not in output
