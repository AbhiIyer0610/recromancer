"""Example tests for request-id resolution (R5.3, R5.4)."""

from __future__ import annotations

import pytest

from recromancer.api.request_id import resolve_request_id

GENERATED = "generated-id-0001"


def _gen() -> str:
    return GENERATED


def test_valid_id_is_echoed() -> None:
    assert resolve_request_id("abc.DEF_123-xyz", _gen) == "abc.DEF_123-xyz"


@pytest.mark.parametrize(
    ("length", "accepted"), [(7, False), (8, True), (64, True), (65, False)]
)
def test_boundary_lengths(length: int, accepted: bool) -> None:
    value = "a" * length
    assert resolve_request_id(value, _gen) == (value if accepted else GENERATED)


def test_trailing_newline_is_rejected() -> None:
    assert resolve_request_id("abcdefgh\n", _gen) == GENERATED


@pytest.mark.parametrize(
    "value", ["abcdefgh\u00e9", "\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668"]
)
def test_non_ascii_is_rejected(value: str) -> None:
    assert resolve_request_id(value, _gen) == GENERATED


@pytest.mark.parametrize("value", [None, ""])
def test_absent_or_empty_generates(value: str | None) -> None:
    assert resolve_request_id(value, _gen) == GENERATED
