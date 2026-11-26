"""Unit tests for BundleError and ChunkingError."""

import pytest

from recromancer.domain.errors import (
    BundleError,
    ChunkingError,
    ErrorCode,
    RecromancerError,
)

CLASSES = [BundleError, ChunkingError]


@pytest.mark.parametrize("cls", CLASSES)
def test_code_is_validation_error(cls: type[BundleError] | type[ChunkingError]) -> None:
    error = cls("bad")
    assert isinstance(error, RecromancerError)
    assert error.code is ErrorCode.VALIDATION_ERROR
    assert error.message == "bad"


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("length", [500, 501, 5000])
def test_message_is_truncated_to_500_without_raising(
    cls: type[BundleError] | type[ChunkingError], length: int
) -> None:
    error = cls("x" * length)
    assert error.message == "x" * min(length, 500)
    assert str(error) == error.message
