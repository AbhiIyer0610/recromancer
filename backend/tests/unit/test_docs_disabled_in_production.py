"""Interactive docs are routed outside production only, and app wiring flags.

Covers Requirements 4.5 and 5.10 to 5.13.
"""

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from recromancer.api.app import Lifespan, create_app
from tests.fakes_api import FakeClock, FakeProbe, counter_ids

DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def build(
    *,
    production: bool = False,
    demo_mode: bool = True,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    return create_app(
        readiness=FakeProbe(),
        clock=FakeClock(step=0.001),
        new_id=counter_ids(),
        production=production,
        demo_mode=demo_mode,
        lifespan=lifespan,
    )


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_a_standard_404_in_production(
    log_stream: io.StringIO, path: str
) -> None:
    response = TestClient(build(production=True)).get(path)
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_served_outside_production(log_stream: io.StringIO, path: str) -> None:
    assert TestClient(build(production=False)).get(path).status_code == 200


def test_state_reflects_the_arguments() -> None:
    app = build(production=True, demo_mode=False)
    assert (app.state.production, app.state.demo_mode) == (True, False)
    default = build()
    assert (default.state.production, default.state.demo_mode) == (False, True)


def test_lifespan_runs_on_startup_and_shutdown(log_stream: io.StringIO) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        events.append("start")
        yield
        events.append("stop")

    with TestClient(build(lifespan=lifespan)):
        assert events == ["start"]
    assert events == ["start", "stop"]
