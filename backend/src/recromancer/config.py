"""Typed settings and the only reader of the process environment.

Imports are limited to ``recromancer.domain``, the standard library, ``pydantic`` and
``pydantic-settings`` (import-linter contract C3).
"""

import os
import types
import typing
from collections.abc import Mapping
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from recromancer.domain.errors import ErrorCode, RecromancerError

ENV_PREFIX = "RECROMANCER_"
DEFAULT_DATABASE_URL = "postgresql://recromancer:recromancer@localhost:5432/recromancer"
ADMIN_TOKEN_PLACEHOLDER = "change-me-local-only"
MIN_ADMIN_TOKEN_LENGTH = 32
MIN_REDACTED_SECRET_LENGTH = 8
DEFAULT_POSTGRES_PORT = 5432

_DATABASE_URL_SCHEMES = frozenset({"postgresql", "postgres"})
_DATABASE_URL_ERROR = "database_url must be a postgresql URL with a host"
_REDACTED = "***"


class ConfigError(RecromancerError):
    """Raised when configuration is invalid. Never contains a configuration value."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CONFIG_INVALID, message)


def _describe_type(annotation: Any) -> str:
    """Return a human-readable expected type for a field annotation."""
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return "one of " + ", ".join(str(a) for a in typing.get_args(annotation))
    if origin is typing.Union or origin is types.UnionType:
        parts = [a for a in typing.get_args(annotation) if a is not type(None)]
        return " or ".join(_describe_type(a) for a in parts)
    if annotation is SecretStr:
        return "secret string"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _env_name(field: str) -> str:
    return ENV_PREFIX + field.upper()


def _truncate(message: str) -> str:
    limit = RecromancerError.MAX_MESSAGE_LENGTH
    return message if len(message) <= limit else message[: limit - 3] + "..."


class Settings(BaseSettings):
    """Application settings. Load with :meth:`from_env`, not by direct construction."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        case_sensitive=False,
        extra="forbid",
        hide_input_in_errors=True,
        frozen=True,
    )

    production: bool = False
    demo_mode: bool = True
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    admin_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    _SECRET_FIELDS: ClassVar[tuple[str, ...]] = (
        "database_url",
        "admin_token",
        "anthropic_api_key",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only explicit init values: the process environment is read in from_env.
        return (init_settings,)

    @field_validator("admin_token", "anthropic_api_key", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url", mode="after")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            parts = urlsplit(value.get_secret_value())
            _ = parts.port
            valid = parts.scheme in _DATABASE_URL_SCHEMES and bool(parts.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(_DATABASE_URL_ERROR)
        return value

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Load settings from ``environ`` (default: a copy of the process environment).

        Variable names are matched case-insensitively by upper-casing them. If two
        names differ only by case, the one that is already upper-case wins; otherwise
        the first in sorted order wins.
        """
        source: Mapping[str, str] = (
            dict(os.environ) if environ is None else dict(environ)
        )
        upper_to_value: dict[str, str] = {}
        for key in sorted(source):
            upper = key.upper()
            if not upper.startswith(ENV_PREFIX):
                continue
            if upper not in upper_to_value or key == upper:
                upper_to_value[upper] = source[key]

        known = {_env_name(name): name for name in cls.model_fields}
        unknown = sorted(name for name in upper_to_value if name not in known)
        kwargs: dict[str, Any] = {
            known[name]: value
            for name, value in upper_to_value.items()
            if name in known
        }

        invalid: list[str] = []
        settings: Settings | None = None
        try:
            settings = cls(**kwargs)
        except ValidationError as exc:
            seen: set[str] = set()
            for error in exc.errors(
                include_input=False, include_url=False, include_context=False
            ):
                loc = error["loc"]
                field = loc[0] if loc else None
                if not isinstance(field, str) or field in seen:
                    continue
                if field not in cls.model_fields:
                    continue
                seen.add(field)
                expected = _describe_type(cls.model_fields[field].annotation)
                invalid.append(f"{_env_name(field)} (expected {expected})")

        if unknown or invalid or settings is None:
            raise ConfigError(_format_error(unknown, invalid)) from None

        violations = settings._production_violations()
        if violations:
            raise ConfigError(
                _truncate(
                    "Invalid configuration. Production rules violated for: "
                    + ", ".join(_env_name(f) for f in violations)
                    + "."
                )
            ) from None
        return settings

    def _production_violations(self) -> list[str]:
        violations: list[str] = []
        if not self.production:
            return violations
        if (
            "database_url" not in self.model_fields_set
            or self.database_url.get_secret_value() == DEFAULT_DATABASE_URL
        ):
            violations.append("database_url")
        if not self.demo_mode:
            token = self.admin_token
            if (
                token is None
                or len(token.get_secret_value()) < MIN_ADMIN_TOKEN_LENGTH
                or token.get_secret_value() == ADMIN_TOKEN_PLACEHOLDER
            ):
                violations.append("admin_token")
        return violations

    def log_safe_summary(self) -> dict[str, Any]:
        """Every field, with secrets masked and ``database_url`` as ``host:port``."""
        summary: dict[str, Any] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name == "database_url":
                summary[name] = self._host_port()
            elif isinstance(value, SecretStr) or name in self._SECRET_FIELDS:
                summary[name] = _REDACTED
            else:
                summary[name] = value
        return summary

    def _host_port(self) -> str:
        parts = urlsplit(self.database_url.get_secret_value())
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{parts.port or DEFAULT_POSTGRES_PORT}"

    def secret_values(self) -> tuple[str, ...]:
        """Raw secret strings of at least 8 characters, to seed the log redactor."""
        values: list[str] = []
        for name in self._SECRET_FIELDS:
            secret = getattr(self, name)
            if secret is not None:
                raw = secret.get_secret_value()
                if len(raw) >= MIN_REDACTED_SECRET_LENGTH:
                    values.append(raw)
        return tuple(values)


def _format_error(unknown: list[str], invalid: list[str]) -> str:
    parts = ["Invalid configuration."]
    if unknown:
        parts.append("Unknown variables: " + ", ".join(unknown) + ".")
    if invalid:
        parts.append("Invalid fields: " + ", ".join(invalid) + ".")
    return _truncate(" ".join(parts))
