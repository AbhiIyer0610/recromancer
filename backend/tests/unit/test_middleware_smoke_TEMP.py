"""TEMPORARY smoke tests for RequestContextMiddleware; task 6.7 replaces them."""

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import anyio
import pytest
import structlog
from structlog.testing import capture_logs

from recromancer.api.middleware import RequestContextMiddleware
from recromancer.service.ports import Clock

Message = MutableMapping[str, Any]
_NONE: Any = None
App = Callable[[Any, Any, Any], Awaitable[None]]


class _Clock:
    def __init__(self, step: float = 0.0) -> None:
        self.mono = 10.0
        self.step = step

    def now(self) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    def monotonic(self) -> float:
        value = self.mono
        self.mono += self.step
        return value


def _ids() -> Callable[[], str]:
    return lambda: "generated-id-0001"


async def _ok(
    scope: Any,
    receive: Any,
    send: Any,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": headers or []})
    await send({"type": "http.response.body", "body": b""})


async def _call(
    app: App,
    headers: list[tuple[bytes, bytes]] | None = None,
    clock: Clock | None = None,
    query: bytes = b"",
) -> tuple[list[Message], list[Any], dict[str, Any]]:
    mw = RequestContextMiddleware(app, clock=clock or _Clock(), new_id=_ids())
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "query_string": query,
        "headers": headers or [],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    with capture_logs() as logs:
        try:
            await mw(scope, receive, send)
        finally:
            pass
    return sent, logs, scope


def _id_headers(sent: list[Message]) -> list[bytes]:
    return [v for k, v in sent[0]["headers"] if k == b"x-request-id"]


def test_valid_id_is_echoed_once_and_replaces_app_header() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await _ok(scope, receive, send, [(b"X-Request-Id", b"app-set-value")])

    sent, logs, scope = anyio.run(
        _call,
        app,
        [(b"X-REQUEST-ID", b"abcdefgh-1"), (b"x-request-id", b"second-id-22")],
    )
    assert _id_headers(sent) == [b"abcdefgh-1"]
    assert scope["state"]["request_id"] == "abcdefgh-1"
    assert len(logs) == 1


@pytest.mark.parametrize(
    "bad", [b"short", b"abc\r\ndef-ghi", b"a" * 65, "caf\xe9-12345".encode("latin-1")]
)
def test_invalid_id_is_replaced(bad: bytes) -> None:
    sent, _, _ = anyio.run(_call, _ok, [(b"x-request-id", bad)])
    assert _id_headers(sent) == [b"generated-id-0001"]


def test_404_style_response_gets_header() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 404})
        await send({"type": "http.response.body", "body": b""})

    sent, logs, _ = anyio.run(_call, app)
    assert _id_headers(sent) == [b"generated-id-0001"]
    assert logs[0]["status"] == 404


def test_exception_logs_500_once_and_propagates() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    captured: list[Any] = []

    async def run() -> None:
        with capture_logs() as logs:
            mw = RequestContextMiddleware(app, clock=_Clock(), new_id=_ids())
            try:
                await mw(
                    {"type": "http", "method": "GET", "path": "/p", "headers": []},
                    _NONE,
                    _NONE,
                )
            finally:
                captured.extend(logs)

    with pytest.raises(RuntimeError, match="boom"):
        anyio.run(run)
    assert [(e["status"], e["path"]) for e in captured] == [(500, "/p")]


def test_cancellation_logs_once_and_propagates() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise asyncio.CancelledError

    captured: list[Any] = []

    async def run() -> None:
        with capture_logs() as logs:
            mw = RequestContextMiddleware(app, clock=_Clock(), new_id=_ids())
            try:
                await mw(
                    {"type": "http", "method": "GET", "path": "/p", "headers": []},
                    _NONE,
                    _NONE,
                )
            finally:
                captured.extend(logs)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]["status"] == 500


def test_lifespan_passes_through() -> None:
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def run() -> None:
        mw = RequestContextMiddleware(app, clock=_Clock(), new_id=_ids())
        scope: dict[str, Any] = {"type": "lifespan"}
        await mw(scope, _NONE, _NONE)
        assert "state" not in scope

    anyio.run(run)
    assert seen == ["lifespan"]


def test_access_log_fields_use_injected_clock_and_omit_secrets() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await _ok(scope, receive, send)

    sent, logs, _ = anyio.run(
        lambda: _call(
            app,
            [(b"authorization", b"Bearer secret-token")],
            _Clock(step=0.0125),
            b"q=secret",
        )
    )
    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "request"
    assert entry["duration_ms"] == 12.5
    assert set(entry) == {
        "event",
        "log_level",
        "method",
        "path",
        "status",
        "duration_ms",
    }
    assert "secret" not in repr(entry)
    assert entry["path"] == "/x"


def test_broken_logger_does_not_mask_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def info(self, *args: Any, **kwargs: Any) -> None:
            raise ValueError("log failed")

    monkeypatch.setattr(structlog, "get_logger", lambda *a, **k: Broken())
    sent, _, _ = anyio.run(_call, _ok)
    assert sent[0]["status"] == 200
