"""LG: the Ledgerline sample bundle (R2.1 to R2.10).

The golden file ``tests/unit/data/ledgerline_hashes.json`` maps item id to
Content_Hash and is the cross-OS check that the fixture hashes the same
everywhere (decision 17). It is generated once from the final fixture. Never
regenerate it just to make a test pass; regenerate it only after a deliberate
fixture change. From ``backend`` with the venv active (PowerShell):

    python -c "import json, pathlib
    from recromancer.ingest.bundle import read_bundle
    from recromancer.ingest_cli import ledgerline_dir, make_fetch
    b = read_bundle(make_fetch(ledgerline_dir()))
    d = {i.id: i.content_hash for i in b.items}
    s = json.dumps(d, sort_keys=True, indent=2) + chr(10)
    pathlib.Path('tests/unit/data/ledgerline_hashes.json').write_bytes(s.encode())"
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from recromancer.domain.evidence import Bundle, EvidenceItem
from recromancer.ingest.bundle import read_bundle
from recromancer.ingest_cli import ledgerline_dir, make_fetch

_HERE = Path(__file__).resolve()
_GOLDEN = _HERE.parent / "data" / "ledgerline_hashes.json"
_GITATTRIBUTES = _HERE.parents[3] / ".gitattributes"
_LF_LINE = "backend/src/recromancer/ingest/fixtures/** text eol=lf"
_FIXTURE_ROOT = Path(str(ledgerline_dir()))

_SILENCE_WORDS = (
    "kubernetes",
    "k8s",
    "helm",
    "hire",
    "hiring",
    "recruit",
    "interview",
    "candidate",
    "headcount",
)
_URL_HOST = re.compile(r"https?://([^/\s)]+)")


def _read() -> Bundle:
    return read_bundle(make_fetch(ledgerline_dir()))


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    return _read()


@pytest.fixture(scope="module")
def by_id(bundle: Bundle) -> dict[str, EvidenceItem]:
    return {item.id: item for item in bundle.items}


def silence_matches(text: str) -> list[str]:
    """Return the R2.7 words that occur in ``text`` as case-insensitive substrings."""
    lowered = text.lower()
    return [word for word in _SILENCE_WORDS if word in lowered]


def _fixture_files() -> list[Path]:
    return sorted(p for p in _FIXTURE_ROOT.rglob("*") if p.is_file())


# --- shape (R2.1 to R2.3) ---


def test_bundle_shape(bundle: Bundle) -> None:
    assert 30 <= len(bundle.items) <= 32
    assert bundle.bundle_id == "ledgerline"
    assert bundle.subject.handle == "priya-raman"
    assert bundle.fictional is True
    assert len({item.id for item in bundle.items}) == len(bundle.items)
    for kind in ("commit", "pull_request", "review", "issue", "doc"):
        assert sum(item.kind == kind for item in bundle.items) >= 2


def test_readme_present_and_labelled_fictional() -> None:
    readme = (_FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "fictional" in readme.lower()


def test_authors_and_timeline(bundle: Bundle) -> None:
    others = {i.author for i in bundle.items if i.author != bundle.subject.handle}
    assert len(others) == 3
    assert all(
        i.kind == "review" for i in bundle.items if i.author != bundle.subject.handle
    )
    lo = datetime(2025, 1, 1, tzinfo=UTC)
    hi = datetime(2026, 10, 16, 23, 59, 59, tzinfo=UTC)
    stamps = [i.timestamp for i in bundle.items]
    assert all(lo <= s <= hi for s in stamps)
    assert stamps == sorted(stamps)


def test_sizes_and_non_ascii(bundle: Bundle) -> None:
    assert sum(p.stat().st_size for p in _fixture_files()) < 150 * 1024
    assert any(not (i.title + i.body).isascii() for i in bundle.items)
    by = {i.id: i for i in bundle.items}
    for item_id in (
        "pr-0142-remove-redis-cache",
        "issue-0233-duplicate-settlement-webhook",
        "doc-rfc-multi-currency-ledger",
        "doc-deployment-runbook",
    ):
        assert 2500 <= len(by[item_id].body) <= 6000, item_id


# --- links and names (R2.4, R2.8) ---


def test_urls_are_https_example_invalid(bundle: Bundle) -> None:
    from urllib.parse import urlsplit

    for item in bundle.items:
        if item.url is None:
            continue
        parts = urlsplit(item.url)
        assert parts.scheme == "https", item.id
        assert parts.hostname == "example.invalid", item.id


def test_no_foreign_url_hosts_in_text(bundle: Bundle) -> None:
    for item in bundle.items:
        for text in (item.title, item.body):
            for host in _URL_HOST.findall(text):
                assert host == "example.invalid", item.id


# --- planted facts (R2.5, R2.6) ---


def _has(item: EvidenceItem, *needles: str) -> None:
    for needle in needles:
        assert needle in item.body, f"{item.id}: {needle!r}"


def test_fact_a_remove_redis_cache(by_id: dict[str, EvidenceItem]) -> None:
    pr = by_id["pr-0142-remove-redis-cache"]
    assert pr.kind == "pull_request" and pr.author == "priya-raman"
    _has(
        pr,
        "raced with the nightly settlement import",
        "stale balances in three reconciliation runs",
    )
    _has(pr, "38%", "12 ms", "One fewer stateful dependency")
    ask = by_id["review-pr-0142-tomas-month-end-load"]
    assert ask.kind == "review" and ask.author != "priya-raman"
    assert pr.id in ask.references
    _has(ask, "month-end")
    reply = by_id["review-pr-0142-priya-measured-load"]
    assert reply.kind == "review" and reply.author == "priya-raman"
    assert pr.id in reply.references
    _has(reply, "3x peak load", "under 80 ms")


def test_fact_b_idempotency_incident(by_id: dict[str, EvidenceItem]) -> None:
    issue = by_id["issue-0233-duplicate-settlement-webhook"]
    assert issue.kind == "issue" and issue.author == "priya-raman"
    _has(issue, "retried a settlement webhook", "posted the same settlement twice")
    _has(issue, "41 transactions", "Idempotency-Key", "unique constraint", "24 hours")
    _has(issue, "```sql")
    referencing = [
        i for i in by_id.values() if i.kind == "commit" and issue.id in i.references
    ]
    assert {i.id for i in referencing} >= {
        "commit-9c21d7b-require-idempotency-key",
        "commit-e17a6c4-idempotency-unique-constraint",
    }


def test_fact_c_multi_currency_objection(by_id: dict[str, EvidenceItem]) -> None:
    doc = by_id["doc-rfc-multi-currency-ledger"]
    assert doc.kind == "doc"
    review = by_id["review-rfc-multi-currency-priya-objection"]
    assert review.kind == "review" and review.author == "priya-raman"
    assert doc.id in review.references
    _has(review, "read time", "floats", "reproducible", "rate id", "original amount")


def test_decoys(by_id: dict[str, EvidenceItem]) -> None:
    lru = by_id["commit-3d9e5a7-currency-metadata-lru"]
    assert "redis" not in (lru.title + lru.body).lower()
    assert "idempotent" in by_id["pr-0117-webhook-signature-check"].body
    runbook = by_id["doc-deployment-runbook"].body.lower()
    for needle in ("docker compose", "two virtual machines", "systemd timers"):
        assert needle in runbook


# --- silence scan (R2.7) ---


def test_silence_scan_helper_is_not_vacuous() -> None:
    assert silence_matches("Release candidate") == ["candidate"]
    assert silence_matches("Wiltshire") == ["hire"]
    assert silence_matches("nothing to see") == []


def test_no_silence_words_in_any_title_or_body(bundle: Bundle) -> None:
    hits = {
        i.id: found
        for i in bundle.items
        if (found := silence_matches(i.title) + silence_matches(i.body))
    }
    assert hits == {}


# --- determinism and bytes (R2.9, R2.10) ---


def test_twice_read_equal(bundle: Bundle) -> None:
    again = _read()
    assert again.items == bundle.items
    assert [i.content_hash for i in again.items] == [
        i.content_hash for i in bundle.items
    ]


def test_hashes_match_golden_file(bundle: Bundle) -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert golden == {i.id: i.content_hash for i in bundle.items}


def test_fixture_files_have_no_bom_and_no_cr() -> None:
    files = _fixture_files()
    assert len(files) >= 32
    for path in files:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r" not in data, path.name


def test_gitattributes_has_lf_line() -> None:
    lines = _GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
    assert _LF_LINE in (line.strip() for line in lines)
