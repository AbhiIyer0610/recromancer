"""Pure ASGI middleware: request id, logging context, response header, access log."""

from __future__ import annotations

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bound_contextvars

from recromancer.api.errors import REQUEST_ID_HEADER
from recromancer.api.request_id import resolve_request_id
from recromancer.service.ports import Clock, IdGenerator

_HEADER_NAME = REQUEST_ID_HEADER.lower().encode("latin-1")
_ACCESS_LOGGER_NAME = "recromancer.access"
_NO_RESPONSE_STATUS = 500


def _first_request_id_header(scope: Scope) -> str | None:
    """Return the first inbound ``x-request-id`` value, decoded as latin-1."""
    for name, value in scope.get("headers", ()):
        if bytes(name).lower() == _HEADER_NAME:
            return bytes(value).decode("latin-1")
    return None


class RequestContextMiddleware:
    """Resolve the request id, bind log context, set the header, log once."""

    def __init__(self, app: ASGIApp, *, clock: Clock, new_id: IdGenerator) -> None:
        self._app = app
        self._clock = clock
        self._new_id = new_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = self._clock.monotonic()
        request_id = resolve_request_id(_first_request_id_header(scope), self._new_id)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        method: str = scope["method"]
        path: str = scope["path"]
        header_value = request_id.encode("latin-1")
        sent_status: list[int] = []

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", ())
                    if bytes(name).lower() != _HEADER_NAME
                ]
                headers.append((_HEADER_NAME, header_value))
                message = {**message, "headers": headers}
                sent_status.append(int(message["status"]))
            await send(message)

        logger = structlog.get_logger(_ACCESS_LOGGER_NAME)
        with bound_contextvars(request_id=request_id, method=method, path=path):
            try:
                await self._app(scope, receive, send_with_id)
            finally:
                try:
                    elapsed_ms = (self._clock.monotonic() - started) * 1000
                    logger.info(
                        "request",
                        method=method,
                        path=path,
                        status=sent_status[0] if sent_status else _NO_RESPONSE_STATUS,
                        duration_ms=round(max(elapsed_ms, 0.0), 3),
                    )
                except Exception:  # noqa: BLE001, S110
                    # A logging failure must not mask the original outcome.
                    pass
