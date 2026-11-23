"""Structured JSON logging with redaction.

One JSON object per line with ``timestamp`` (UTC, ISO-8601, ``Z`` suffix, taken from
the injected clock), ``level`` (lower-case, as structlog gives it), ``logger`` and
``message``. Structlog events and stdlib records (uvicorn, psycopg, ...) go through one
``ProcessorFormatter`` so they share the same fields and the same redaction.

Chain order (structlog events): ``filter_by_level``, ``merge_contextvars``,
``add_log_level``, ``add_logger_name``, exception formatting, ``wrap_for_formatter``.
Formatter processors, for every record: ``remove_processors_meta``, timestamp and
``event`` -> ``message`` rename, redaction, ``JSONRenderer`` (``ensure_ascii=True``, so
output is ASCII only), final line guard. For stdlib records ``foreign_pre_chain``
supplies the same context, level, logger and exception formatting before the shared
formatter processors run. Redaction therefore always runs after exception formatting,
so tracebacks are redacted too.

Registered secrets, the clock and the level live in module state that the processors
read at call time. ``configure_logging`` replaces that state on every call.

Only ``recromancer.domain``, the standard library and ``structlog`` may be imported.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TextIO
from urllib.parse import quote

import structlog
from structlog import contextvars as _ctx
from structlog import processors as _proc
from structlog import stdlib as _std
from structlog.typing import EventDict, Processor, WrappedLogger

from recromancer.domain.secrets import is_sensitive_key

REDACTED = "***"
REDACTION_FAILED_MESSAGE = "log record dropped: redaction failed"

_EXCEPTION_FAILED_TEXT = "exception formatting failed"
_DEPTH_MARKER = "[max depth]"
_CYCLE_MARKER = "[cycle]"
_UNPRINTABLE = "[unprintable]"
_FALLBACK_TIMESTAMP = "1970-01-01T00:00:00.000Z"
_FALLBACK_LINE = json.dumps({"message": REDACTION_FAILED_MESSAGE})
_MAX_DEPTH = 8
_QUESTION_KEYS = frozenset({"question", "question_text", "query_text"})
_SECRET_TYPE_NAMES = frozenset({"SecretStr", "SecretBytes"})
_HANDLER_MARKER = "_recromancer_log_handler"
_UVICORN_ACCESS = "uvicorn.access"
_UVICORN_PROPAGATING = ("uvicorn", "uvicorn.error")


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class _State:
    clock: Clock
    forms: tuple[str, ...]  # raw and JSON-escaped secrets, longest first


_state: _State | None = None


class _UnusedClock:
    def now(self) -> datetime:  # pragma: no cover - never called by redact_text
        raise RuntimeError("no clock")


_UNUSED_CLOCK = _UnusedClock()


def question_log_fields(text: str) -> dict[str, int]:
    """Return the only sanctioned way to refer to a question in a log entry."""
    return {"question_len": len(text)}


def _build_forms(secret_values: Iterable[str]) -> tuple[str, ...]:
    forms: set[str] = set()
    for secret in secret_values:
        if not isinstance(secret, str) or not secret:
            continue
        forms.add(secret)
        forms.add(json.dumps(secret)[1:-1])
        forms.add(json.dumps(secret, ensure_ascii=False)[1:-1])
        forms.add(quote(secret))
        forms.add(quote(secret, safe=""))
    forms.discard("")
    return tuple(sorted(forms, key=lambda form: (-len(form), form)))


def _require_state() -> _State:
    if _state is None:
        raise RuntimeError("logging is not configured")
    return _state


def _scrub(text: str, state: _State) -> str:
    for form in state.forms:
        if form in text:
            text = text.replace(form, REDACTED)
    return text


def redact_text(text: str, secret_values: Iterable[str]) -> str:
    """Replace every registered secret form in ``text`` with ``***``.

    Uses the same forms (raw, JSON-escaped, percent-encoded) as the log redactor and
    does not need logging to be configured.
    """
    return _scrub(text, _State(clock=_UNUSED_CLOCK, forms=_build_forms(secret_values)))


def _safe_str(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return _UNPRINTABLE


def _is_secret_type(value: object) -> bool:
    return any(cls.__name__ in _SECRET_TYPE_NAMES for cls in type(value).__mro__)


def _redact_value(value: object, state: _State, depth: int, seen: set[int]) -> object:
    if isinstance(value, str):
        return _scrub(value if type(value) is str else str.__str__(value), state)
    if value is None or isinstance(value, bool):
        return value
    if _is_secret_type(value):
        return REDACTED
    if isinstance(value, int | float):
        text = _safe_str(value)
        scrubbed = _scrub(text, state)
        if scrubbed != text or (isinstance(value, float) and not math.isfinite(value)):
            return scrubbed
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return _scrub(bytes(value).decode("utf-8", errors="replace"), state)
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return _redact_container(value, state, depth, seen)
    return _scrub(_safe_str(value), state)


def _redact_container(
    value: Any,
    state: _State,
    depth: int,
    seen: set[int],
) -> object:
    if depth >= _MAX_DEPTH:
        return _DEPTH_MARKER
    marker = id(value)
    if marker in seen:
        return _CYCLE_MARKER
    seen.add(marker)
    try:
        if isinstance(value, Mapping):
            return _redact_mapping(value, state, depth, seen)
        return [_redact_value(item, state, depth + 1, seen) for item in list(value)]
    finally:
        seen.discard(marker)


def _redact_mapping(
    mapping: Mapping[Any, Any], state: _State, depth: int, seen: set[int]
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in list(mapping.items()):
        key_text = key if isinstance(key, str) else _safe_str(key)
        out_key = _scrub(key_text, state)
        if is_sensitive_key(key_text) or key_text.lower() in _QUESTION_KEYS:
            result[out_key] = REDACTED
        else:
            result[out_key] = _redact_value(item, state, depth + 1, seen)
    return result


def _format_timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).isoformat(timespec="milliseconds")
    return stamp.removesuffix("+00:00") + "Z"


def _current_timestamp() -> str:
    try:
        return _format_timestamp(_require_state().clock.now())
    except Exception:
        return _FALLBACK_TIMESTAMP


def _dropped_event(event_dict: EventDict) -> EventDict:
    """Event holding only standard fields and the fixed text; never original values."""
    dropped: EventDict = {"timestamp": _current_timestamp()}
    try:
        state = _require_state()
        for field in ("level", "logger"):
            raw = event_dict.get(field)
            if type(raw) is str:
                dropped[field] = _scrub(raw, state)
    except Exception:
        dropped.pop("level", None)
        dropped.pop("logger", None)
    dropped["message"] = REDACTION_FAILED_MESSAGE
    return dropped


def _format_exception(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Format ``exc_info`` into ``exception``; on failure drop it without leaking."""
    if not event_dict.get("exc_info"):
        event_dict.pop("exc_info", None)
        return event_dict
    try:
        result = _proc.format_exc_info(logger, method_name, dict(event_dict))
        if not isinstance(result, dict):
            raise TypeError("unexpected processor result")
        return result
    except Exception:
        event_dict.pop("exc_info", None)
        event_dict["exception"] = _EXCEPTION_FAILED_TEXT
        return event_dict


def _stamp_and_rename(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    event_dict["timestamp"] = _current_timestamp()
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _redact(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    try:
        state = _require_state()
        return _redact_mapping(event_dict, state, 0, {id(event_dict)})
    except Exception:
        return _dropped_event(event_dict)


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _final_guard(logger: Any, method_name: str, rendered: Any) -> Any:
    """Last resort: drop the line if a registered secret survives in it.

    The line is parsed and only decoded strings are checked, so JSON structure and key
    names cannot cause false matches, and no text is rewritten inside the encoded line
    (rewriting could split an escape sequence and corrupt the JSON).
    """
    try:
        state = _require_state()
        line = rendered.decode("utf-8") if isinstance(rendered, bytes) else rendered
        for text in _strings(json.loads(line)):
            if any(form in text for form in state.forms):
                return json.dumps(_dropped_event({}), ensure_ascii=True)
        return rendered
    except Exception:
        return _FALLBACK_LINE


def _level_number(level: str | int) -> int:
    if isinstance(level, int):
        return level
    number = logging.getLevelNamesMapping().get(level.upper())
    if number is None:
        raise ValueError("unknown log level name")
    return number


def _remove_our_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def configure_logging(
    *,
    level: str | int,
    clock: Clock,
    secret_values: Iterable[str] = (),
    stream: TextIO = sys.stdout,
) -> None:
    """Configure JSON logging. Idempotent: each call replaces the previous setup."""
    global _state
    number = _level_number(level)
    _state = _State(clock=clock, forms=_build_forms(secret_values))

    shared_pre: list[Processor] = [
        _ctx.merge_contextvars,
        _std.add_log_level,
        _std.add_logger_name,
        _format_exception,
    ]
    structlog.configure(
        processors=[
            _std.filter_by_level,
            *shared_pre,
            _std.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=_std.LoggerFactory(),
        wrapper_class=_std.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = _std.ProcessorFormatter(
        processors=[
            _std.ProcessorFormatter.remove_processors_meta,
            _stamp_and_rename,
            _redact,
            _proc.JSONRenderer(ensure_ascii=True),
            _final_guard,
        ],
        foreign_pre_chain=shared_pre,
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARKER, True)

    root = logging.getLogger()
    _remove_our_handlers(root)
    root.addHandler(handler)
    root.setLevel(number)

    for name in _UVICORN_PROPAGATING:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.disabled = False
    access = logging.getLogger(_UVICORN_ACCESS)
    access.handlers.clear()
    access.propagate = False
    access.disabled = True


__all__ = [
    "REDACTED",
    "REDACTION_FAILED_MESSAGE",
    "Clock",
    "configure_logging",
    "question_log_fields",
    "redact_text",
]
