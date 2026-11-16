"""Unit tests for the log-key rule in ``recromancer.domain.secrets``."""

import pytest

from recromancer.domain.secrets import is_sensitive_key, name_segments

REDACTED = [
    "apikey",
    "apiKey",
    "API_KEY",
    "api_key",
    "accesskey",
    "privatekey",
    "signingkey",
    "authkey",
    "accesstoken",
    "db_password",
    "admin_token",
    "authorization",
    "session_cookie",
]

NOT_REDACTED = ["monkey", "keyboard", "input_tokens", "output_tokens", "max_tokens"]


@pytest.mark.parametrize("key", REDACTED)
def test_secret_keys_are_redacted(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", NOT_REDACTED)
def test_ordinary_keys_are_not_redacted(key: str) -> None:
    assert not is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "api-key",
        "API-KEY",
        "db-password",
        "Admin-Token",
        "session-cookie",
        "Authorization",
    ],
)
def test_hyphen_and_upper_case_secret_variants_are_redacted(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "input-tokens",
        "INPUT_TOKENS",
        "Max-Tokens",
        "output-tokens",
        "MONKEY",
        "Keyboard",
    ],
)
def test_hyphen_and_upper_case_ordinary_variants_are_not_redacted(key: str) -> None:
    assert not is_sensitive_key(key)


@pytest.mark.parametrize("key", REDACTED + NOT_REDACTED)
def test_hyphen_and_upper_case_forms_match_snake_case(key: str) -> None:
    expected = is_sensitive_key(key)
    assert is_sensitive_key(key.replace("_", "-")) is expected
    assert is_sensitive_key(key.upper()) is expected


def test_counter_allowlist_wins_over_substring_marker() -> None:
    assert not is_sensitive_key("secret_tokens")


def test_tokenizer_is_redacted_by_substring_rule() -> None:
    assert is_sensitive_key("tokenizer")


def test_empty_string_is_not_redacted() -> None:
    assert not is_sensitive_key("")


def test_name_segments_drops_empty_segments() -> None:
    assert name_segments("__api--key_") == ("api", "key")
    assert name_segments("") == ()
    assert name_segments("-_") == ()
