"""Unit tests for the domain error taxonomy."""

import pytest

from recromancer.domain.errors import ErrorCode, RecromancerError

EXPECTED = {
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "CONFIG_INVALID",
    "MIGRATION_FAILED",
    "DEPENDENCY_UNAVAILABLE",
    "INTERNAL_ERROR",
}


def test_members_present_and_values_equal_names() -> None:
    assert {c.name for c in ErrorCode} == EXPECTED
    assert all(c.value == c.name for c in ErrorCode)


def test_message_of_500_characters_is_accepted() -> None:
    message = "a" * 500
    error = RecromancerError(ErrorCode.INTERNAL_ERROR, message)
    assert error.message == message


def test_message_of_501_characters_is_rejected_without_leaking_text() -> None:
    message = "SECRETTEXT" + "b" * 491
    assert len(message) == 501
    with pytest.raises(ValueError) as info:
        RecromancerError(ErrorCode.INTERNAL_ERROR, message)
    text = str(info.value)
    assert "501" in text
    assert "SECRETTEXT" not in text
    assert "bbbb" not in text


def test_code_and_message_attributes_are_preserved() -> None:
    error = RecromancerError(ErrorCode.NOT_FOUND, "missing")
    assert error.code is ErrorCode.NOT_FOUND
    assert error.message == "missing"
    assert str(error) == "missing"
