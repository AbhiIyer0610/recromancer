"""TEMPORARY smoke test for api/app.py; task 6.7 replaces it."""

from __future__ import annotations

import itertools
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from recromancer.api.app import create_app


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> datetime:
        return datetime(2024, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        self._t += 0.01
        return self._t


class FakeProbe:
    async def is_ready(self) -> bool:
        return True


def _build(*, production: bool = False, demo_mode: bool = True) -> FastAPI:
    counter = itertools.count(1)
    return create_app(
        readiness=FakeProbe(),
        clock=FakeClock(),
        new_id=lambda: f"gen-id-{next(counter):04d}",
        production=production,
        demo_mode=demo_mode,
    )


def test_docs_enabled_outside_production() -> None:
    client = TestClient(_build(production=False))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 200
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "ErrorBody" in schemas


def test_docs_are_404_in_production() -> None:
    client = TestClient(_build(production=True))
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == r.headers["x-request-id"]


def test_state_flags() -> None:
    app = _build(production=True, demo_mode=False)
    assert app.state.production is True
    assert app.state.demo_mode is False
    assert _build().state.demo_mode is True


def test_lifespan_runs_on_startup_and_shutdown() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        events.append("start")
        yield
        events.append("stop")

    app = create_app(
        readiness=FakeProbe(),
        clock=FakeClock(),
        new_id=lambda: "gen-id-0001",
        lifespan=lifespan,
    )
    with TestClient(app):
        assert events == ["start"]
    assert events == ["start", "stop"]


def test_catch_all_sets_request_id_header_via_handler() -> None:
    app = _build()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-detail")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.headers["x-request-id"] == r.json()["request_id"]
    assert "secret-detail" not in r.text


def test_invalid_timeout_rejected_with_fixed_message() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        create_app(
            readiness=FakeProbe(),
            clock=FakeClock(),
            new_id=lambda: "x",
            readyz_timeout_s=0,
        )
