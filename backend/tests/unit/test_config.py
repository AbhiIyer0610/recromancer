"""Unit tests for ``recromancer.config.Settings`` and ``ConfigError``.

Every case passes an explicit mapping to ``Settings.from_env``; the process
environment is never read or modified.
"""

from collections.abc import Mapping

import pytest
from pydantic import SecretStr, ValidationError

from recromancer.config import (
    ADMIN_TOKEN_PLACEHOLDER,
    DEFAULT_DATABASE_URL,
    ConfigError,
    Settings,
)
from recromancer.domain.errors import ErrorCode, RecromancerError

PROD_URL = "postgresql://app:pw@db.example.com:6543/app"


def _prod(**overrides: str) -> dict[str, str]:
    env = {
        "RECROMANCER_PRODUCTION": "true",
        "RECROMANCER_DEMO_MODE": "false",
        "RECROMANCER_DATABASE_URL": PROD_URL,
        "RECROMANCER_ADMIN_TOKEN": "t" * 32,
    }
    env.update(overrides)
    return env


def _error(env: Mapping[str, str]) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env)
    return info.value


# --- defaults, prefix, case ---------------------------------------------------


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.production is False
    assert s.demo_mode is True
    assert s.database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert s.admin_token is None
    assert s.anthropic_api_key is None
    assert s.log_level == "INFO"


def test_variables_without_prefix_are_ignored() -> None:
    env = {"PATH": "/bin", "OTHER_RECROMANCER_PRODUCTION": "true", "LOG_LEVEL": "bogus"}
    s = Settings.from_env(env)
    assert s.production is False
    assert s.log_level == "INFO"


def test_prefixed_values_are_loaded() -> None:
    s = Settings.from_env(
        {"RECROMANCER_LOG_LEVEL": "DEBUG", "RECROMANCER_DEMO_MODE": "false"}
    )
    assert s.log_level == "DEBUG"
    assert s.demo_mode is False


def test_names_are_case_insensitive() -> None:
    s = Settings.from_env(
        {"recromancer_log_level": "WARNING", "Recromancer_Demo_Mode": "false"}
    )
    assert s.log_level == "WARNING"
    assert s.demo_mode is False


def test_upper_case_key_wins_over_other_case_variant() -> None:
    env = {"recromancer_log_level": "DEBUG", "RECROMANCER_LOG_LEVEL": "ERROR"}
    assert Settings.from_env(env).log_level == "ERROR"


def test_case_variants_without_upper_case_pick_first_sorted() -> None:
    env = {"recromancer_log_level": "DEBUG", "Recromancer_Log_Level": "ERROR"}
    assert Settings.from_env(env).log_level == "ERROR"


# --- unknown and invalid ------------------------------------------------------


def test_unknown_variable_is_named_in_error() -> None:
    err = _error({"recromancer_foo": "x", "RECROMANCER_BAR": "y"})
    assert err.code is ErrorCode.CONFIG_INVALID
    assert isinstance(err, RecromancerError)
    assert err.message == (
        "Invalid configuration. Unknown variables: RECROMANCER_BAR, RECROMANCER_FOO."
    )


def test_invalid_field_names_expected_type_but_not_value() -> None:
    raw = "sekrit-value-xyz"
    err = _error({"RECROMANCER_DEMO_MODE": raw})
    assert err.message == (
        "Invalid configuration. Invalid fields: RECROMANCER_DEMO_MODE (expected bool)."
    )
    assert raw not in err.message


def test_log_level_literal_is_validated() -> None:
    raw = "loud-sekrit"
    err = _error({"RECROMANCER_LOG_LEVEL": raw})
    assert "RECROMANCER_LOG_LEVEL (expected one of DEBUG, INFO, WARNING, ERROR)" in (
        err.message
    )
    assert raw not in err.message


def test_unknown_and_invalid_are_reported_together() -> None:
    err = _error({"RECROMANCER_NOPE": "1", "RECROMANCER_PRODUCTION": "maybe"})
    assert "RECROMANCER_NOPE" in err.message
    assert "RECROMANCER_PRODUCTION (expected bool)" in err.message


def test_input_is_hidden_from_error_text_and_chain() -> None:
    raw = "sekrit-value-xyz"
    err = _error({"RECROMANCER_DEMO_MODE": raw})
    for text in (str(err), repr(err), err.message):
        assert raw not in text
    assert err.__cause__ is None
    assert err.__context__ is None
    assert err.__suppress_context__ is True


def test_production_error_has_no_cause_or_context() -> None:
    err = _error({"RECROMANCER_PRODUCTION": "true"})
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.parametrize(
    "url",
    ["mysql://h/x", "postgresql:///db", "postgresql://h:notaport/db", "just-text"],
)
def test_invalid_database_url_names_field_only(url: str) -> None:
    err = _error({"RECROMANCER_DATABASE_URL": url})
    assert "RECROMANCER_DATABASE_URL" in err.message
    assert url not in err.message


def test_long_message_is_truncated_to_limit() -> None:
    env = {f"RECROMANCER_{'X' * 30}{i:03d}": "1" for i in range(40)}
    err = _error(env)
    assert len(err.message) == RecromancerError.MAX_MESSAGE_LENGTH
    assert err.message.endswith("...")


# --- production rules ---------------------------------------------------------


def test_valid_production_configuration() -> None:
    s = Settings.from_env(_prod())
    assert s.production is True
    assert s.demo_mode is False


@pytest.mark.parametrize("length", [31, 32])
def test_admin_token_length_boundary(length: int) -> None:
    env = _prod(RECROMANCER_ADMIN_TOKEN="a" * length)
    if length >= 32:
        assert Settings.from_env(env).admin_token is not None
        return
    err = _error(env)
    assert "RECROMANCER_ADMIN_TOKEN" in err.message
    assert "a" * length not in err.message
    assert str(length) not in err.message


def test_admin_token_missing_in_production() -> None:
    env = _prod()
    del env["RECROMANCER_ADMIN_TOKEN"]
    assert "RECROMANCER_ADMIN_TOKEN" in _error(env).message


def test_admin_token_placeholder_rejected_even_if_long_enough() -> None:
    assert len(ADMIN_TOKEN_PLACEHOLDER) < 32
    # Placeholder is shorter than the minimum, so it fails on either rule.
    err = _error(_prod(RECROMANCER_ADMIN_TOKEN=ADMIN_TOKEN_PLACEHOLDER))
    assert "RECROMANCER_ADMIN_TOKEN" in err.message
    assert ADMIN_TOKEN_PLACEHOLDER not in err.message


def test_admin_token_not_required_in_demo_mode() -> None:
    env = _prod(RECROMANCER_DEMO_MODE="true")
    del env["RECROMANCER_ADMIN_TOKEN"]
    s = Settings.from_env(env)
    assert s.admin_token is None


def test_production_with_demo_mode_and_explicit_url_is_valid() -> None:
    s = Settings.from_env(
        {
            "RECROMANCER_PRODUCTION": "true",
            "RECROMANCER_DEMO_MODE": "true",
            "RECROMANCER_DATABASE_URL": PROD_URL,
        }
    )
    assert s.production and s.demo_mode


def test_production_requires_database_url_set() -> None:
    env = _prod()
    del env["RECROMANCER_DATABASE_URL"]
    assert "RECROMANCER_DATABASE_URL" in _error(env).message


def test_production_rejects_default_database_url() -> None:
    err = _error(_prod(RECROMANCER_DATABASE_URL=DEFAULT_DATABASE_URL))
    assert "RECROMANCER_DATABASE_URL" in err.message
    assert DEFAULT_DATABASE_URL not in err.message


def test_non_production_may_use_default_url_and_no_token() -> None:
    s = Settings.from_env({"RECROMANCER_DEMO_MODE": "false"})
    assert s.admin_token is None


def test_multiple_violations_reported_in_one_error() -> None:
    err = _error({"RECROMANCER_PRODUCTION": "true", "RECROMANCER_DEMO_MODE": "false"})
    assert err.message == (
        "Invalid configuration. Production rules violated for: "
        "RECROMANCER_DATABASE_URL, RECROMANCER_ADMIN_TOKEN."
    )


# --- blank secrets ------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_optional_secrets_are_none(blank: str) -> None:
    s = Settings.from_env(
        {
            "RECROMANCER_ADMIN_TOKEN": blank,
            "RECROMANCER_ANTHROPIC_API_KEY": blank,
        }
    )
    assert s.admin_token is None
    assert s.anthropic_api_key is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_admin_token_fails_production_rule(blank: str) -> None:
    err = _error(_prod(RECROMANCER_ADMIN_TOKEN=blank))
    assert "RECROMANCER_ADMIN_TOKEN" in err.message


# --- summary and secrets ------------------------------------------------------


def test_log_safe_summary_defaults() -> None:
    summary = Settings.from_env({}).log_safe_summary()
    assert summary == {
        "production": False,
        "demo_mode": True,
        "database_url": "localhost:5432",
        "admin_token": "***",
        "anthropic_api_key": "***",
        "log_level": "INFO",
    }


def test_log_safe_summary_masks_values_and_keeps_port() -> None:
    token = "t" * 40
    s = Settings.from_env(
        _prod(RECROMANCER_ADMIN_TOKEN=token, RECROMANCER_LOG_LEVEL="ERROR")
    )
    summary = s.log_safe_summary()
    assert summary["database_url"] == "db.example.com:6543"
    assert summary["admin_token"] == "***"
    assert summary["log_level"] == "ERROR"
    assert summary["production"] is True
    assert token not in repr(summary)
    assert "pw" not in repr(summary)


def test_log_safe_summary_defaults_port_and_brackets_ipv6() -> None:
    s = Settings.from_env(
        {"RECROMANCER_DATABASE_URL": "postgres://u:p@db.example.com/x"}
    )
    assert s.log_safe_summary()["database_url"] == "db.example.com:5432"
    s6 = Settings.from_env({"RECROMANCER_DATABASE_URL": "postgres://u:p@[::1]/x"})
    assert s6.log_safe_summary()["database_url"] == "[::1]:5432"


def test_secret_values_length_filter() -> None:
    short = "s" * 7
    boundary = "b" * 8
    s = Settings.from_env(
        {
            "RECROMANCER_ADMIN_TOKEN": short,
            "RECROMANCER_ANTHROPIC_API_KEY": boundary,
        }
    )
    values = s.secret_values()
    assert boundary in values
    assert short not in values
    assert DEFAULT_DATABASE_URL in values


def test_secret_values_skips_unset_secrets() -> None:
    assert Settings.from_env({}).secret_values() == (DEFAULT_DATABASE_URL,)


# --- immutability -------------------------------------------------------------


def test_settings_are_frozen() -> None:
    s = Settings.from_env({})
    with pytest.raises(ValidationError):
        setattr(s, "demo_mode", False)  # noqa: B010
    assert isinstance(s.database_url, SecretStr)
