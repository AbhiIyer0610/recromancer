"""Unit tests for ``recromancer.observability``.

Covers Requirements 5.1, 5.2, 5.5, 5.7, 5.8 and 5.9. Only a ``StringIO`` stream and a
fake clock are used. Secrets are built at runtime, never as token-shaped literals.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import quote

import pytest
import structlog
from pydantic import SecretBytes, SecretStr
from structlog.contextvars import bound_contextvars

from recromancer import observability as obs

SECRET = "s3cr3t-" + "x" * 12
OTHER_SECRET = "0ther-" + "y" * 12
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


class FakeClock:
    """Deterministic clock whose time only moves when the test advances it."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class BrokenClock:
    def now(self) -> datetime:
        raise RuntimeError("clock down")


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Configure logging onto a fresh StringIO and restore global state afterwards."""
    monkeypatch.setattr(obs, "_state", None)
    root = logging.getLogger()
    saved_level = root.level
    saved = {
        name: (lg.propagate, lg.disabled, list(lg.handlers))
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        for lg in [logging.getLogger(name)]
    }
    buffer = io.StringIO()
    obs.configure_logging(level="INFO", clock=FakeClock(), stream=buffer)
    yield buffer
    for handler in list(root.handlers):
        if getattr(handler, "_recromancer_log_handler", False):
            root.removeHandler(handler)
    root.setLevel(saved_level)
    for name, (propagate, disabled, handlers) in saved.items():
        lg = logging.getLogger(name)
        lg.propagate = propagate
        lg.disabled = disabled
        lg.handlers[:] = handlers
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def _configure(
    stream: io.StringIO,
    *,
    level: str | int = "INFO",
    clock: Any = None,
    secrets: tuple[str, ...] = (SECRET,),
) -> FakeClock:
    clk = clock if clock is not None else FakeClock()
    obs.configure_logging(level=level, clock=clk, secret_values=secrets, stream=stream)
    return cast("FakeClock", clk)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    text = stream.getvalue()
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def _raise_with(message: str) -> ValueError:
    try:
        raise ValueError(message)
    except ValueError as exc:
        return exc


# --- standard fields, clock, context ---


def test_every_record_is_one_json_line_with_standard_fields(
    stream: io.StringIO,
) -> None:
    clock = _configure(stream)
    log = structlog.get_logger("app.test")
    log.info("first\nline two")
    clock.advance(seconds=1, milliseconds=500)
    logging.getLogger("std.logger").warning("second")
    first, second = _lines(stream)
    assert len(stream.getvalue().splitlines()) == 2
    assert first["timestamp"] == "2026-01-02T03:04:05.678Z"
    assert second["timestamp"] == "2026-01-02T03:04:07.178Z"
    assert TIMESTAMP_RE.fullmatch(first["timestamp"])
    assert (first["level"], first["logger"], first["message"]) == (
        "info",
        "app.test",
        "first\nline two",
    )
    assert (second["level"], second["logger"], second["message"]) == (
        "warning",
        "std.logger",
        "second",
    )


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 1, 1, 0, 0, 0),
        datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        datetime(2026, 1, 1, tzinfo=UTC),
    ],
)
def test_timestamp_is_utc_with_z_suffix(stream: io.StringIO, moment: datetime) -> None:
    _configure(stream, clock=FakeClock(moment))
    structlog.get_logger("a").info("m")
    assert _lines(stream)[0]["timestamp"] == "2026-01-01T00:00:00.000Z"


def test_broken_clock_falls_back_without_raising(stream: io.StringIO) -> None:
    _configure(stream, clock=BrokenClock())
    structlog.get_logger("a").info("m")
    assert _lines(stream)[0]["timestamp"] == "1970-01-01T00:00:00.000Z"


def test_context_variables_on_structlog_and_stdlib_records(stream: io.StringIO) -> None:
    _configure(stream)
    with bound_contextvars(request_id="req-12345678", method="GET", path="/ask"):
        structlog.get_logger("a").info("one")
        logging.getLogger("b").info("two")
    structlog.get_logger("a").info("three")
    one, two, three = _lines(stream)
    for record in (one, two):
        assert record["request_id"] == "req-12345678"
        assert record["method"] == "GET"
        assert record["path"] == "/ask"
    assert "request_id" not in three


def test_level_names_are_accepted_as_int_or_str(stream: io.StringIO) -> None:
    _configure(stream, level=logging.WARNING)
    structlog.get_logger("a").info("dropped")
    structlog.get_logger("a").warning("kept")
    assert [r["message"] for r in _lines(stream)] == ["kept"]


# --- redaction: keys ---


@pytest.mark.parametrize(
    "key",
    ["password", "API_KEY", "Authorization", "client_secret", "x-token", "Cookie"],
)
def test_sensitive_keys_are_redacted(stream: io.StringIO, key: str) -> None:
    _configure(stream)
    structlog.get_logger("a").info("m", **{key: "visible-value"})
    record = _lines(stream)[0]
    assert record[key] == "***"
    assert "visible-value" not in stream.getvalue()


def test_counter_keys_are_not_redacted(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info("m", max_tokens=5)
    assert _lines(stream)[0]["max_tokens"] == 5


@pytest.mark.parametrize("key", ["question", "Question", "QUESTION_TEXT", "query_text"])
def test_question_keys_are_redacted_in_any_case(stream: io.StringIO, key: str) -> None:
    _configure(stream)
    structlog.get_logger("a").info("m", **{key: "why was the cache removed?"})
    assert _lines(stream)[0][key] == "***"
    assert "cache removed" not in stream.getvalue()


def test_question_log_fields_returns_only_the_length(stream: io.StringIO) -> None:
    text = "why was the cache removed?"
    fields = obs.question_log_fields(text)
    assert fields == {"question_len": len(text)}
    _configure(stream)
    structlog.get_logger("a").info("asked", **fields)
    record = _lines(stream)[0]
    assert record["question_len"] == len(text)
    assert "cache" not in stream.getvalue()


def test_sensitive_key_at_depth_three_is_redacted(stream: io.StringIO) -> None:
    _configure(stream)
    value = "p" + "w" * 9
    structlog.get_logger("a").info("m", data={"x": {"y": {"password": value}}})
    assert _lines(stream)[0]["data"]["x"]["y"]["password"] == "***"
    assert value not in stream.getvalue()


# --- redaction: registered secrets ---


def test_secret_in_message_and_fields(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info("login " + SECRET, note=f"n={SECRET}")
    record = _lines(stream)[0]
    assert record["message"] == "login ***"
    assert record["note"] == "n=***"
    assert SECRET not in stream.getvalue()


def test_secret_nested_in_dict_list_dict_and_tuple(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info(
        "m",
        data={"a": [{"b": SECRET, "c": [SECRET]}]},
        pair=(SECRET, "ok"),
        keyed={SECRET: 1},
    )
    record = _lines(stream)[0]
    assert record["data"] == {"a": [{"b": "***", "c": ["***"]}]}
    assert record["pair"] == ["***", "ok"]
    assert record["keyed"] == {"***": 1}
    assert SECRET not in stream.getvalue()


def test_secret_in_stdlib_percent_args_fstring_and_uvicorn_error(
    stream: io.StringIO,
) -> None:
    _configure(stream)
    logging.getLogger("x").error("value %s here", SECRET)
    logging.getLogger("x").error(f"formatted {SECRET} here")
    logging.getLogger("uvicorn.error").error("uv %s", SECRET)
    first, second, third = _lines(stream)
    assert first["message"] == "value *** here"
    assert second["message"] == "formatted *** here"
    assert (third["logger"], third["message"]) == ("uvicorn.error", "uv ***")
    assert SECRET not in stream.getvalue()


def test_secret_in_structlog_exception_traceback(stream: io.StringIO) -> None:
    _configure(stream)
    try:
        raise ValueError("bad " + SECRET)
    except ValueError:
        structlog.get_logger("a").exception("failed")
    try:
        raise ValueError("bad " + SECRET)
    except ValueError:
        structlog.get_logger("a").error("failed", exc_info=True)
    for record in _lines(stream):
        assert "ValueError: bad ***" in record["exception"]
    assert SECRET not in stream.getvalue()


def test_secret_in_stdlib_exception_traceback(stream: io.StringIO) -> None:
    _configure(stream)
    try:
        raise ValueError("bad " + SECRET)
    except ValueError:
        logging.getLogger("x").exception("failed")
    record = _lines(stream)[0]
    assert "ValueError: bad ***" in record["exception"]
    assert SECRET not in stream.getvalue()


def test_secret_with_quotes_backslashes_and_non_ascii(stream: io.StringIO) -> None:
    secret = 'a"b' + "\\" + "c" + "\u00e9\u00fc\u4e2d" + " " + "z" * 6
    escaped = json.dumps(secret)[1:-1]
    forms = [
        secret,
        escaped,
        json.dumps(secret, ensure_ascii=False)[1:-1],
        quote(secret),
    ]
    _configure(stream, secrets=(secret,))
    log = structlog.get_logger("a")
    log.info(f"raw {secret} escaped {escaped}", field=secret, nested=[{"k": secret}])
    try:
        raise ValueError(secret)
    except ValueError:
        log.exception("failed")
    logging.getLogger("x").error("std %s", secret)
    output = stream.getvalue()
    for form in forms:
        assert form not in output
    first = _lines(stream)[0]
    assert first["message"] == "raw *** escaped ***"
    assert output.isascii()


def test_longer_secret_with_shorter_prefix_is_replaced_first(
    stream: io.StringIO,
) -> None:
    short = "sh0rt-" + "a" * 6
    long = short + "-longer-" + "b" * 6
    _configure(stream, secrets=(short, long))
    structlog.get_logger("a").info(f"v {long} end {short}")
    assert _lines(stream)[0]["message"] == "v *** end ***"
    assert "longer" not in stream.getvalue()
    assert "bbbbbb" not in stream.getvalue()


class _Holder:
    def __str__(self) -> str:
        return f"holder({SECRET})"


def test_non_string_values_do_not_leak(stream: io.StringIO) -> None:
    int_secret = 12345678
    _configure(stream, secrets=(SECRET, str(int_secret)))
    structlog.get_logger("a").info(
        "m",
        obj=_Holder(),
        raw=SECRET.encode(),
        view=memoryview(SECRET.encode()),
        err=_raise_with(SECRET),
        wrapped=SecretStr(SECRET),
        wrapped_bytes=SecretBytes(SECRET.encode()),
        number=int_secret,
        members={SECRET},
    )
    record = _lines(stream)[0]
    assert record["wrapped"] == "***"
    assert record["wrapped_bytes"] == "***"
    assert record["number"] == "***"
    assert record["obj"] == "holder(***)"
    assert record["members"] == ["***"]
    output = stream.getvalue()
    assert SECRET not in output
    assert str(int_secret) not in output


def test_scalar_values_keep_their_json_types(stream: io.StringIO) -> None:
    _configure(stream)

    class Name(str):
        __slots__ = ()

    structlog.get_logger("a").info(
        "m", none=None, flag=True, count=3, ratio=1.5, nan=float("nan"), name=Name("n")
    )
    record = _lines(stream)[0]
    assert (record["none"], record["flag"], record["count"], record["ratio"]) == (
        None,
        True,
        3,
        1.5,
    )
    assert record["nan"] == "nan"
    assert record["name"] == "n"


def test_non_str_registered_values_and_empty_secret_are_ignored(
    stream: io.StringIO,
) -> None:
    secrets = cast("tuple[str, ...]", (12345678, "", SECRET))
    _configure(stream, secrets=secrets)
    structlog.get_logger("a").info("hello world " + SECRET)
    assert _lines(stream)[0]["message"] == "hello world ***"


def test_empty_secret_does_not_blank_the_message(stream: io.StringIO) -> None:
    _configure(stream, secrets=("",))
    structlog.get_logger("a").info("hello world")
    logging.getLogger("x").info("std world")
    assert [r["message"] for r in _lines(stream)] == ["hello world", "std world"]


def test_secretstr_renders_masked(stream: io.StringIO) -> None:
    _configure(stream, secrets=())
    structlog.get_logger("a").info(
        "m", value=SecretStr(SECRET), items=[SecretStr(SECRET)]
    )
    record = _lines(stream)[0]
    assert record["value"] == "***"
    assert record["items"] == ["***"]
    assert SECRET not in stream.getvalue()


# --- configure_logging behaviour ---


def test_configure_twice_does_not_duplicate_output(stream: io.StringIO) -> None:
    _configure(stream)
    _configure(stream)
    structlog.get_logger("a").info("one")
    logging.getLogger("x").info("two")
    assert [r["message"] for r in _lines(stream)] == ["one", "two"]
    ours = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "_recromancer_log_handler", False)
    ]
    assert len(ours) == 1


def test_second_call_replaces_secrets_clock_and_level(stream: io.StringIO) -> None:
    first_clock = FakeClock()
    _configure(stream, clock=first_clock, secrets=(SECRET,), level="INFO")
    second_clock = FakeClock(datetime(2030, 5, 6, 7, 8, 9, tzinfo=UTC))
    _configure(stream, clock=second_clock, secrets=(OTHER_SECRET,), level="WARNING")
    structlog.get_logger("a").info("dropped")
    logging.getLogger("x").info("dropped")
    structlog.get_logger("a").warning("s " + SECRET + " o " + OTHER_SECRET)
    logging.getLogger("x").warning("s " + SECRET + " o " + OTHER_SECRET)
    records = _lines(stream)
    assert len(records) == 2
    for record in records:
        assert record["message"] == f"s {SECRET} o ***"
        assert record["timestamp"] == "2030-05-06T07:08:09.000Z"
    assert OTHER_SECRET not in stream.getvalue()


def test_levels_below_threshold_are_dropped(stream: io.StringIO) -> None:
    _configure(stream, level="INFO")
    structlog.get_logger("a").debug("no")
    logging.getLogger("x").debug("no")
    structlog.get_logger("a").info("yes-structlog")
    logging.getLogger("x").info("yes-stdlib")
    assert [r["message"] for r in _lines(stream)] == ["yes-structlog", "yes-stdlib"]


def test_unknown_level_name_raises_without_echoing_value(stream: io.StringIO) -> None:
    with pytest.raises(ValueError) as info:
        obs.configure_logging(level="zzz-level", clock=FakeClock(), stream=stream)
    assert "zzz-level" not in str(info.value)


def test_uvicorn_access_logger_is_silent(stream: io.StringIO) -> None:
    _configure(stream)
    access = logging.getLogger("uvicorn.access")
    access.info("GET / 200")
    access.error("GET / 500")
    assert stream.getvalue() == ""


def test_output_is_ascii_only(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info("caf\u00e9 \u4e2d", extra="\u00fc")
    logging.getLogger("x").info("na\u00efve")
    output = stream.getvalue()
    assert output.isascii()
    assert _lines(stream)[0]["message"] == "caf\u00e9 \u4e2d"


# --- failure paths ---


class _BadMapping(dict[str, Any]):
    def items(self) -> Any:
        raise RuntimeError("items failed")


class _UnprintableObject:
    def __str__(self) -> str:
        raise RuntimeError(SECRET)


def _assert_dropped(stream: io.StringIO, *values: str) -> None:
    (record,) = _lines(stream)
    assert record["message"] == obs.REDACTION_FAILED_MESSAGE
    assert set(record) <= {"timestamp", "level", "logger", "message"}
    for value in values:
        assert value not in stream.getvalue()


def test_redaction_failure_from_hostile_mapping_drops_the_record(
    stream: io.StringIO,
) -> None:
    _configure(stream)
    structlog.get_logger("a").info(
        "visible-message",
        bad=_BadMapping(),
        other="visible-field-value",
        obj=_UnprintableObject(),
    )
    _assert_dropped(stream, "visible-message", "visible-field-value", SECRET)


def test_redaction_failure_from_internal_error_drops_the_record(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(obs, "_redact_mapping", boom)
    _configure(stream)
    structlog.get_logger("a").info("visible-message", other="visible-field-value")
    logging.getLogger("x").info("visible-std %s", "visible-arg")
    first, second = _lines(stream)
    for record in (first, second):
        assert record["message"] == obs.REDACTION_FAILED_MESSAGE
    assert "visible" not in stream.getvalue()
    assert SECRET not in stream.getvalue()


def test_unprintable_object_does_not_raise(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info("m", obj=_UnprintableObject())
    assert _lines(stream)[0]["obj"] == "[unprintable]"
    assert SECRET not in stream.getvalue()


@pytest.mark.parametrize("returns", ["raise", "non-dict"])
def test_exception_formatting_failure_does_not_leak(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch, returns: str
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        if returns == "raise":
            raise RuntimeError(SECRET)
        return ["not", "a", "dict"]

    monkeypatch.setattr("recromancer.observability._proc.format_exc_info", broken)
    _configure(stream)
    try:
        raise ValueError("bad " + SECRET)
    except ValueError:
        structlog.get_logger("a").exception("structlog failed")
        logging.getLogger("x").exception("stdlib failed")
    first, second = _lines(stream)
    assert first["exception"] == second["exception"] == "exception formatting failed"
    assert SECRET not in stream.getvalue()


def test_exc_info_false_is_removed(stream: io.StringIO) -> None:
    _configure(stream)
    structlog.get_logger("a").info("m", exc_info=False)
    record = _lines(stream)[0]
    assert "exc_info" not in record
    assert "exception" not in record


def test_final_guard_drops_line_when_secret_survives_redaction(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(obs, "_redact", lambda logger, method, event: event)
    _configure(stream)
    structlog.get_logger("a").info("leaky " + SECRET, items=[{"k": SECRET}])
    (record,) = _lines(stream)
    assert record["message"] == obs.REDACTION_FAILED_MESSAGE
    assert SECRET not in stream.getvalue()


def test_final_guard_handles_bytes_and_invalid_input(stream: io.StringIO) -> None:
    _configure(stream)
    clean = json.dumps({"m": "fine", "n": [1, {"k": "v"}]})
    assert obs._final_guard(None, "info", clean.encode()) == clean.encode()
    leaky = json.dumps({"m": SECRET})
    assert json.loads(obs._final_guard(None, "info", leaky.encode()))["message"] == (
        obs.REDACTION_FAILED_MESSAGE
    )
    assert json.loads(obs._final_guard(None, "info", "not json")) == {
        "message": obs.REDACTION_FAILED_MESSAGE
    }


def test_redact_without_configured_state_drops_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(obs, "_state", None)
    with pytest.raises(RuntimeError):
        obs._require_state()
    result = obs._redact(None, "info", {"level": "info", "message": SECRET})
    assert result == {
        "timestamp": "1970-01-01T00:00:00.000Z",
        "message": obs.REDACTION_FAILED_MESSAGE,
    }


# --- depth and cycles ---


def test_cycle_and_depth_limit_do_not_crash_or_leak(stream: io.StringIO) -> None:
    _configure(stream)
    cyclic: dict[str, Any] = {"secret_holder": SECRET, "plain": SECRET}
    cyclic["self"] = cyclic
    nested: Any = SECRET
    for _ in range(20):
        nested = {"k": nested}
    structlog.get_logger("a").info("m", cyclic=cyclic, deep=nested, marker="[ok]")
    record = _lines(stream)[0]
    assert record["cyclic"]["self"] == "[cycle]"
    assert record["cyclic"]["secret_holder"] == "***"
    assert record["cyclic"]["plain"] == "***"
    assert "[max depth]" in json.dumps(record["deep"])
    assert SECRET not in stream.getvalue()


def test_shared_but_acyclic_references_are_not_marked_as_cycles(
    stream: io.StringIO,
) -> None:
    _configure(stream)
    shared = {"v": 1}
    structlog.get_logger("a").info("m", data=[shared, shared])
    assert _lines(stream)[0]["data"] == [{"v": 1}, {"v": 1}]


# --- redaction: percent-encoded forms ---

ENCODED_SECRET = "pa ss/w@rd%41-" + "x" * 6


def _encoded_forms(secret: str) -> list[str]:
    forms = [quote(secret), quote(secret, safe="")]
    assert forms[0] != forms[1]
    assert all(form != secret for form in forms)
    return forms


@pytest.mark.parametrize("which", [0, 1])
def test_percent_encoded_secret_is_redacted_in_structlog_record(
    stream: io.StringIO, which: int
) -> None:
    form = _encoded_forms(ENCODED_SECRET)[which]
    _configure(stream, secrets=(ENCODED_SECRET,))
    structlog.get_logger("a").info(f"url?k={form}", field=f"v={form}")
    record = _lines(stream)[0]
    assert record["message"] == "url?k=***"
    assert record["field"] == "v=***"
    for text in (*_encoded_forms(ENCODED_SECRET), ENCODED_SECRET):
        assert text not in stream.getvalue()


@pytest.mark.parametrize("which", [0, 1])
def test_percent_encoded_secret_is_redacted_in_stdlib_record(
    stream: io.StringIO, which: int
) -> None:
    form = _encoded_forms(ENCODED_SECRET)[which]
    _configure(stream, secrets=(ENCODED_SECRET,))
    logging.getLogger("x").error("std %s", form)
    logging.getLogger("x").error(f"fstring {form}")
    assert [r["message"] for r in _lines(stream)] == ["std ***", "fstring ***"]
    for text in (*_encoded_forms(ENCODED_SECRET), ENCODED_SECRET):
        assert text not in stream.getvalue()


@pytest.mark.parametrize("which", [0, 1])
def test_percent_encoded_secret_is_redacted_in_exception_text(
    stream: io.StringIO, which: int
) -> None:
    form = _encoded_forms(ENCODED_SECRET)[which]
    _configure(stream, secrets=(ENCODED_SECRET,))
    try:
        raise ValueError("bad " + form)
    except ValueError:
        structlog.get_logger("a").exception("failed")
        logging.getLogger("x").exception("failed")
    for record in _lines(stream):
        assert "ValueError: bad ***" in record["exception"]
    for text in (*_encoded_forms(ENCODED_SECRET), ENCODED_SECRET):
        assert text not in stream.getvalue()


def test_url_safe_secret_has_one_form_and_one_replacement(
    stream: io.StringIO,
) -> None:
    assert quote(SECRET) == quote(SECRET, safe="") == SECRET
    assert obs._build_forms((SECRET,)) == (SECRET,)
    _configure(stream)
    structlog.get_logger("a").info(f"a {SECRET} b")
    assert _lines(stream)[0]["message"] == "a *** b"


def test_final_guard_drops_line_when_percent_encoded_form_survives(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(obs, "_redact", lambda logger, method, event: event)
    _configure(stream, secrets=(ENCODED_SECRET,))
    forms = _encoded_forms(ENCODED_SECRET)
    structlog.get_logger("a").info("leaky " + forms[0])
    structlog.get_logger("a").info("leaky", items=[{"k": forms[1]}])
    for record in _lines(stream):
        assert record["message"] == obs.REDACTION_FAILED_MESSAGE
    for text in forms:
        assert text not in stream.getvalue()


def test_longer_encoded_form_containing_shorter_secret_is_replaced_first(
    stream: io.StringIO,
) -> None:
    short = "sh0rt-" + "a" * 6
    long = "pa ss/" + short
    encoded = quote(long, safe="")
    assert short in encoded
    forms = obs._build_forms((short, long))
    assert forms.index(encoded) < forms.index(short)
    _configure(stream, secrets=(short, long))
    structlog.get_logger("a").info(f"v {encoded} end {short}")
    assert _lines(stream)[0]["message"] == "v *** end ***"
    assert "pa%20ss" not in stream.getvalue()
