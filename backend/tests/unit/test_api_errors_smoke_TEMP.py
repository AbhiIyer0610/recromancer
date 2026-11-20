"""TEMPORARY smoke test for api/errors.py; task 6.7 replaces it."""

from __future__ import annotations

import itertools
import warnings
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest
from fastapi import FastAPI

# starlette 1.10 warns that TestClient prefers httpx2; -W error turns that into an
# import failure. Scoped ignore here; the project-wide decision belongs to task 6.7.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from recromancer.api.errors import (
    ERROR_HTTP_STATUS,
    INTERNAL_ERROR_MESSAGE,
    MAX_MESSAGE_LENGTH,
    register_exception_handlers,
    validation_message,
)
from recromancer.domain.errors import ErrorCode, RecromancerError

Scope = MutableMapping[str, Any]


class StateMiddleware:
    """Hand-set scope state, standing in for RequestContextMiddleware (task 6.4)."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["request_id"] = "req-fixed-0001"
        await self.app(scope, receive, send)


def _build() -> TestClient:
    counter = itertools.count(1)
    app = FastAPI()
    register_exception_handlers(app, lambda: f"gen-id-{next(counter):04d}")

    @app.get("/q")
    def q(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @app.get("/domain")
    def domain() -> None:
        raise RecromancerError(ErrorCode.DEPENDENCY_UNAVAILABLE, "db down")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-detail")

    app.add_middleware(StateMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def test_each_handler_once() -> None:
    client = _build()

    r = client.get("/domain")
    assert (r.status_code, r.json()["code"], r.json()["message"]) == (
        503,
        "DEPENDENCY_UNAVAILABLE",
        "db down",
    )
    assert r.json()["request_id"] == "req-fixed-0001"

    r = client.get("/q?limit=abc")
    assert r.status_code == 422
    assert r.json()["message"] == "Request validation failed: query.limit"
    assert "abc" not in r.text

    r = client.get("/missing")
    assert (r.status_code, r.json()["code"], r.json()["message"]) == (
        404,
        "NOT_FOUND",
        "Not found",
    )

    r = client.post("/q")
    assert (r.status_code, r.json()["code"]) == (405, "METHOD_NOT_ALLOWED")
    assert "GET" in r.headers["allow"]

    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {
        "code": "INTERNAL_ERROR",
        "message": INTERNAL_ERROR_MESSAGE,
        "request_id": "req-fixed-0001",
    }
    assert r.headers["x-request-id"] == "req-fixed-0001"
    assert "secret-detail" not in r.text


def test_missing_state_falls_back_to_generated_id() -> None:
    counter = itertools.count(1)
    app = FastAPI()
    register_exception_handlers(app, lambda: f"gen-id-{next(counter):04d}")
    r = TestClient(app, raise_server_exceptions=False).get("/missing")
    assert r.json()["request_id"] == "gen-id-0001"


def test_status_map_is_total() -> None:
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode)


@pytest.mark.parametrize("n", [0, 30])
def test_validation_message_is_bounded(n: int) -> None:
    from fastapi.exceptions import RequestValidationError

    errors: list[Any] = [
        {
            "type": "x",
            "loc": ("body", "f" * 300, i, True, 1.5),
            "input": "LEAK",
            "ctx": {"a": "LEAK"},
        }
        for i in range(n)
    ]
    message = validation_message(RequestValidationError(errors))
    assert len(message) <= MAX_MESSAGE_LENGTH
    assert "LEAK" not in message
    assert (message == "Request validation failed") == (n == 0)
