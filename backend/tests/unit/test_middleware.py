"""``RequestContextMiddleware`` driven directly at the ASGI level.

Covers Requirements 5.3 to 5.6. The middleware is called with a hand-built scope,
receive and send. Logs are read from the ``log_stream`` fixture.
"""

import asyncio
import io
from collections.abc import Awaitable, Callable, MutableMapping
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from recromancer.api import middleware as middleware_module
from recromancer.api.middleware import RequestContextMiddleware
from tests.fakes_api import FakeClock, counter_ids, log_records

Message = MutableMapping[str, Any]
Scope = dict[str, Any]
Downstream = Callable[[Any, Any, Any], Awaitable[None]]

GENERATED = "gen-id-0001"
VALID_ID = b"client-id-0001"


class Driven:
    """What a middleware call produced."""

    def __init__(self, scope: Scope, sent: list[Message]) -> None:
        self.scope = scope
        self.sent = sent

    def id_headers(self) -> list[bytes]:
        start = self.sent[0]
        return [v for k, v in start["headers"] if k.lower() == b"x-request-id"]


def make_scope(
    headers: list[tuple[bytes, bytes]] | None = None,
    *,
    path: str = "/x",
    query: bytes = b"",
) -> Scope:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }


async def drive(
    app: Downstream,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    clock: FakeClock | None = None,
    query: bytes = b"",
) -> Driven:
    mw = RequestContextMiddleware(
        app, clock=clock or FakeClock(), new_id=counter_ids("gen-id")
    )
    scope = make_scope(headers, query=query)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    await mw(scope, receive, send)
    return Driven(scope, sent)


async def respond(
    send: Any, status: int = 200, headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    await send(
        {"type": "http.response.start", "status": status, "headers": headers or []}
    )
    await send({"type": "http.response.body", "body": b""})


async def ok_app(scope: Any, receive: Any, send: Any) -> None:
    await respond(send)


def access_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [r for r in log_records(stream) if r["logger"] == "recromancer.access"]


pytestmark = pytest.mark.anyio


# --- pass-through and request id ----------------------------------------------------


async def test_non_http_scope_passes_through_untouched(
    log_stream: io.StringIO,
) -> None:
    seen: list[tuple[Any, Any, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append((scope, receive, send))

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        raise AssertionError("nothing should be sent")

    mw = RequestContextMiddleware(app, clock=FakeClock(), new_id=counter_ids())
    scope: Scope = {"type": "lifespan"}
    await mw(scope, receive, send)

    assert seen[0][0] is scope
    assert seen[0][1] is receive
    assert seen[0][2] is send
    assert scope == {"type": "lifespan"}
    assert log_stream.getvalue() == ""


async def test_first_of_two_inbound_ids_is_used_and_stored_in_scope_state(
    log_stream: io.StringIO,
) -> None:
    result = await drive(
        ok_app,
        headers=[(b"X-REQUEST-ID", VALID_ID), (b"x-request-id", b"second-id-0002")],
    )
    assert result.id_headers() == [VALID_ID]
    assert result.scope["state"]["request_id"] == VALID_ID.decode()
    (line,) = access_lines(log_stream)
    assert line["request_id"] == VALID_ID.decode()


@pytest.mark.parametrize(
    "bad",
    [
        b"short",
        b"abc\r\ndef-ghi",
        b"a" * 65,
        b"has space1",
        "caf\u00e9-123".encode("latin-1"),
    ],
    ids=["short", "crlf", "65-chars", "space", "non-ascii"],
)
async def test_invalid_inbound_id_is_replaced(bad: bytes) -> None:
    result = await drive(ok_app, headers=[(b"x-request-id", bad)])
    assert result.id_headers() == [GENERATED.encode()]
    assert result.scope["state"]["request_id"] == GENERATED


async def test_app_set_request_id_headers_are_replaced_case_insensitively() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await respond(
            send,
            headers=[
                (b"x-request-id", b"app-value-1"),
                (b"content-type", b"text/plain"),
                (b"X-REQUEST-ID", b"app-value-2"),
            ],
        )

    result = await drive(app)
    assert result.id_headers() == [GENERATED.encode()]
    assert (b"content-type", b"text/plain") in result.sent[0]["headers"]
    assert len(result.sent[0]["headers"]) == 2


async def test_streaming_response_gets_the_header_once_and_bodies_unchanged() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await send({"type": "http.response.body", "body": b"b", "more_body": False})

    result = await drive(app)
    assert result.id_headers() == [GENERATED.encode()]
    assert result.sent[1:] == [
        {"type": "http.response.body", "body": b"a", "more_body": True},
        {"type": "http.response.body", "body": b"b", "more_body": False},
    ]


# --- logging context ----------------------------------------------------------------


async def test_contextvars_are_bound_during_the_call_and_gone_afterwards(
    log_stream: io.StringIO,
) -> None:
    during: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        during.append(dict(structlog.contextvars.get_contextvars()))
        await respond(send)

    await drive(app, query=b"token=secret-value")
    assert during == [{"request_id": GENERATED, "method": "GET", "path": "/x"}]
    assert structlog.contextvars.get_contextvars() == {}


async def test_access_line_has_only_the_expected_fields_and_no_query_string(
    log_stream: io.StringIO,
) -> None:
    await drive(ok_app, query=b"token=secret-value", clock=FakeClock(step=0.0125))
    (line,) = access_lines(log_stream)
    assert set(line) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
    }
    assert (line["method"], line["path"], line["status"]) == ("GET", "/x", 200)
    assert line["duration_ms"] == 12.5
    assert "secret-value" not in log_stream.getvalue()


async def test_backwards_clock_gives_zero_duration(log_stream: io.StringIO) -> None:
    await drive(ok_app, clock=FakeClock(step=-1.0))
    (line,) = access_lines(log_stream)
    assert line["duration_ms"] == 0.0


# --- failures -----------------------------------------------------------------------


async def test_exception_before_response_logs_500_once_and_propagates(
    log_stream: io.StringIO,
) -> None:
    boom = RuntimeError("boom")

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise boom

    with pytest.raises(RuntimeError) as info:
        await drive(app)
    assert info.value is boom
    (line,) = access_lines(log_stream)
    assert line["status"] == 500
    assert structlog.contextvars.get_contextvars() == {}


async def test_cancellation_logs_500_once_reraises_and_restores_context(
    log_stream: io.StringIO,
) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await drive(app)
    (line,) = access_lines(log_stream)
    assert line["status"] == 500
    assert structlog.contextvars.get_contextvars() == {}


async def test_exception_after_response_start_logs_the_sent_status(
    log_stream: io.StringIO,
) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 201})
        raise RuntimeError("late")

    with pytest.raises(RuntimeError, match="late"):
        await drive(app)
    (line,) = access_lines(log_stream)
    assert line["status"] == 201


async def test_broken_logger_changes_neither_response_nor_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenLogger:
        def info(self, *args: Any, **kwargs: Any) -> None:
            raise ValueError("log failed")

    monkeypatch.setattr(
        middleware_module,
        "structlog",
        SimpleNamespace(get_logger=lambda name: BrokenLogger()),
    )
    result = await drive(ok_app)
    assert result.sent[0]["status"] == 200
    assert result.id_headers() == [GENERATED.encode()]

    original = RuntimeError("original")

    async def failing(scope: Any, receive: Any, send: Any) -> None:
        raise original

    with pytest.raises(RuntimeError) as info:
        await drive(failing)
    assert info.value is original
