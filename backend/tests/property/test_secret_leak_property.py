"""Property 1: secret values never appear in observable output.

Validates Requirements 3.5, 3.6, 3.11, 5.7 and 10.2.

Hypothesis prints falsifying examples with ``repr``. Generated secrets are therefore
wrapped in ``_Masked`` whose ``repr`` shows only the length, and every other value that
holds a secret goes through that wrapper, so a failing run never echoes a secret.

How the secret is supplied per field:

* ``admin_token`` and ``anthropic_api_key``: the raw secret is the variable value.
* ``database_url``: ``postgresql://u:<quote(secret, safe="")>@h:5432/d``. A URL must
  percent-encode its password, so the encoded form is what the settings hold. The
  settings register the whole URL with the redactor, so the URL is what gets logged,
  and both the raw secret and ``quote(secret, safe="")`` must be absent everywhere.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
import structlog
from hypothesis import assume, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from pydantic import SecretStr

from recromancer import observability as obs
from recromancer.config import ConfigError, Settings
from recromancer.observability import configure_logging
from tests.property.invariants import assert_secret_absent, secret_forms

SECRET_FIELDS = ("database_url", "admin_token", "anthropic_api_key")
CO_FIELDS = (
    None,
    "demo_mode_invalid",
    "log_level_invalid",
    "demo_mode_secret",
    "log_level_secret",
    "unknown_variable",
)

# A placeholder that is not a substring of anything structural in the outputs.
PLACEHOLDER = "zq9-placeholder-value-7731"
_FIXED_PROD_URL = "postgresql://svc:fixedpw@db.internal:5432/app"
_FIXED_PROD_TOKEN = "fixed-admin-token-" + "k" * 24

# Fixed log message texts and field names used by ``_collect``. A secret that is a
# substring of one of these could match harmless structure and fail wrongly.
_FIXED_TEXTS = (
    "structlog message ",
    "stdlib message ",
    "structlog form ",
    "stdlib form ",
    "structlog failure",
    "stdlib failure",
    "boom ",
    "config error",
    "plain",
    "nested",
    "items",
    "inner",
    "wrapped",
    "settings",
    "error",
    "tup",
    "json-ascii",
    "json-unicode",
    "percent-strict",
)


class _Masked:
    """A secret whose ``repr`` hides the value, so Hypothesis output stays clean.

    Deliberately not a dataclass: Hypothesis' pretty printer prints dataclass fields
    and would bypass a custom ``__repr__``.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"<masked secret, {len(self.value)} chars>"


@dataclass(frozen=True)
class Case:
    secret: _Masked
    field: str
    co_field: str | None
    production: bool


class _FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


_STRESS_CHARS = [
    *"\"\\ /@%:'\n\t\r\x0b\x1f\x7f",
    "\u00e9",
    "\u4e2d",
    "\U0001f600",
]


@st.composite
def secret_values(draw: st.DrawFn) -> _Masked:
    """Masked text of 8 to 64 characters: no surrogates, no NUL, escaping stress mix.

    A composite strategy is used because ``.map(_Masked)`` makes Hypothesis print the
    constructor call with its raw argument in falsifying examples.
    """
    alphabet = st.one_of(
        st.sampled_from(_STRESS_CHARS),
        st.characters(exclude_categories=["Cs"], exclude_characters="\x00"),
        st.characters(categories=["Lu", "Ll", "Nd"]),
    )
    return _Masked(draw(st.text(alphabet=alphabet, min_size=8, max_size=64)))


def cases() -> SearchStrategy[Case]:
    return st.builds(
        Case,
        secret=secret_values(),
        field=st.sampled_from(SECRET_FIELDS),
        co_field=st.sampled_from(CO_FIELDS),
        production=st.booleans(),
    )


def _build_environ(
    secret: str, field: str, co_field: str | None, production: bool
) -> dict[str, str]:
    environ: dict[str, str] = {}
    if production:
        environ["RECROMANCER_PRODUCTION"] = "true"
        environ["RECROMANCER_DEMO_MODE"] = "false"
        environ["RECROMANCER_DATABASE_URL"] = _FIXED_PROD_URL
        environ["RECROMANCER_ADMIN_TOKEN"] = _FIXED_PROD_TOKEN
    if field == "database_url":
        environ["RECROMANCER_DATABASE_URL"] = (
            "postgresql://u:" + quote(secret, safe="") + "@h:5432/d"
        )
    else:
        environ["RECROMANCER_" + field.upper()] = secret
    if co_field == "demo_mode_invalid":
        environ["RECROMANCER_DEMO_MODE"] = "not-a-bool"
    elif co_field == "log_level_invalid":
        environ["RECROMANCER_LOG_LEVEL"] = "not-a-level"
    elif co_field == "demo_mode_secret":
        environ["RECROMANCER_DEMO_MODE"] = secret
    elif co_field == "log_level_secret":
        environ["RECROMANCER_LOG_LEVEL"] = secret
    elif co_field == "unknown_variable":
        environ["RECROMANCER_UNKNOWN_THING"] = "x"
    return environ


@contextmanager
def _isolated_logging() -> Iterator[None]:
    """Restore global logging state afterwards.

    Touching ``obs._state`` (private) is deliberate: the module keeps the active
    redaction state there and the test must not leave it behind.
    """
    root = logging.getLogger()
    saved_level = root.level
    saved_state = obs._state
    # Detach other root handlers (pytest's log capture would otherwise print the raw,
    # unredacted event dict of a failing example). They are restored afterwards.
    saved_handlers = list(root.handlers)
    root.handlers[:] = []
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        name: (lg.propagate, lg.disabled, list(lg.handlers))
        for name in names
        for lg in [logging.getLogger(name)]
    }
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_recromancer_log_handler", False):
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, (propagate, disabled, handlers) in saved.items():
            lg = logging.getLogger(name)
            lg.propagate = propagate
            lg.disabled = disabled
            lg.handlers[:] = handlers
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()
        obs._state = saved_state


def _collect(
    secret: str, field: str, co_field: str | None, production: bool
) -> tuple[list[str], str]:
    """Return (settings and error texts, JSON log output) for one configuration."""
    environ = _build_environ(secret, field, co_field, production)
    loaded: Settings | None = None
    error: ConfigError | None = None
    try:
        loaded = Settings.from_env(environ=environ)
    except ConfigError as exc:
        error = exc

    texts: list[str] = []
    if error is not None:
        assert error.__cause__ is None
        assert error.__context__ is None
        texts += [str(error), repr(error), error.message]
        registered: tuple[str, ...] = (secret,)
    else:
        assert loaded is not None
        summary = loaded.log_safe_summary()
        texts += [
            repr(loaded),
            str(loaded),
            repr(summary),
            json.dumps(summary, default=str),
            loaded.model_dump_json(),
            repr(loaded.model_dump()),
        ]
        registered = loaded.secret_values()

    # For database_url the registered value is the whole URL, so the URL is logged.
    logged = environ["RECROMANCER_DATABASE_URL"] if field == "database_url" else secret
    buffer = io.StringIO()
    with _isolated_logging():
        configure_logging(
            level="INFO",
            clock=_FakeClock(),
            secret_values=registered,
            stream=buffer,
        )
        slog = structlog.get_logger("p")
        std = logging.getLogger("p")
        slog.info(
            f"structlog message {logged}",
            plain=logged,
            nested={"items": [logged, {"inner": logged}]},
            wrapped=SecretStr(logged),
            tup=(logged,),
            settings=loaded,
            error=None if error is None else str(error),
        )
        std.error("stdlib message %s", logged)
        for name, form in secret_forms(logged).items():
            slog.info(f"structlog form {name}: {form}")
            std.error("stdlib form %s: %s", name, form)
        try:
            raise ValueError("boom " + logged)
        except ValueError:
            slog.exception("structlog failure")
            std.exception("stdlib failure")
    return texts, buffer.getvalue()


def occurs_in_structure(
    secret: str, field: str, co_field: str | None, production: bool
) -> bool:
    """True if a form of ``secret`` already occurs in the output for the placeholder.

    Such a secret (for example a field name, a JSON key, a masking string or a path in
    a traceback) would match harmless structure rather than a leak, so the property
    drops it with ``assume``. The fixed message texts and field names are checked too.
    """
    texts, log = _collect(PLACEHOLDER, field, co_field, production)
    structure = [*texts, log, *_FIXED_TEXTS]
    try:
        assert_secret_absent(secret, *structure)
    except AssertionError:
        return True
    return False


def check_secret_not_leaked(
    secret: str, field: str, co_field: str | None, production: bool = False
) -> None:
    texts, log = _collect(secret, field, co_field, production)
    assert_secret_absent(secret, *texts, log)
    # The final guard is only a backstop: a dropped record means the redactor missed.
    if obs.REDACTION_FAILED_MESSAGE in log:
        # Explicit raise: pytest's assert rewriting would print ``log``, which may
        # hold the secret when redaction failed.
        raise AssertionError("a log record was dropped by the final guard")
    for line in log.splitlines():
        json.loads(line)


# Feature: platform-foundation, Property 1: Secret values never appear in observable output  # noqa: E501
# Validates: Requirements 3.5, 3.6, 3.11, 5.7, 10.2
# No health check is suppressed: nothing function-scoped is used under @given.
@hyp_settings(deadline=None)
@given(case=cases())
def test_secret_never_appears(case: Case) -> None:
    secret = case.secret.value
    if case.field != "database_url":
        # A blank value means "unset", so there is no secret to protect.
        assume(bool(secret.strip()))
    assume(not occurs_in_structure(secret, case.field, case.co_field, case.production))
    check_secret_not_leaked(secret, case.field, case.co_field, case.production)


def test_hardest_secret_is_not_leaked() -> None:
    secret = 'q"uo\\te \u00e9\u4e2d sp ace' + "zzzz"
    for field in SECRET_FIELDS:
        for co_field in (None, "log_level_invalid", "unknown_variable"):
            check_secret_not_leaked(secret, field, co_field)
    check_secret_not_leaked(secret, "admin_token", None, production=True)
    check_secret_not_leaked(secret, "database_url", "demo_mode_secret", production=True)


def test_assert_secret_absent_detects_each_form() -> None:
    secret = 'q"uo\\te \u00e9\u4e2d sp/ace' + "zzzz"
    forms = secret_forms(secret)
    assert len(set(forms.values())) == 5
    for name, form in forms.items():
        with pytest.raises(AssertionError) as info:
            assert_secret_absent(secret, "clean", "prefix " + form + " suffix")
        message = str(info.value)
        assert message == f"form {name!r} present in text #1"
        assert secret not in message
        assert form not in message
    assert_secret_absent(secret, "clean", "")
    assert_secret_absent("", "anything")
