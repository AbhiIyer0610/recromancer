"""Generator-validity tests for the evidence-ingestion strategies (R1.11, R8.9)."""

from __future__ import annotations

from hypothesis import given

from recromancer.ingest.bundle import read_bundle
from tests.property.strategies import GeneratedBundle, bundles, long_lf_texts


# Feature: evidence-ingestion, generator validity
@given(bundles())
def test_generated_bundles_are_valid_and_keep_manifest_order(
    generated: GeneratedBundle,
) -> None:
    bundle = read_bundle(generated.fetch)
    assert tuple(item.id for item in bundle.items) == generated.item_ids
    assert bundle.bundle_id == generated.bundle_id
    assert bundle.subject.handle == generated.subject_handle


# Risk 12: no suppress_health_check is used here; the default profile must pass.
@given(long_lf_texts())
def test_long_lf_texts_generate_without_health_check_errors(text: str) -> None:
    assert 5000 <= len(text) <= 20000
    assert "\r" not in text
