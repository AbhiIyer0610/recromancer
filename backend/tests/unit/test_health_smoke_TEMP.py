"""TEMPORARY smoke test for api/routes/health.py; task 6.7 replaces it."""

from __future__ import annotations

import warnings

import anyio
import pytest
import structlog
from fastapi import FastAPI

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from recromancer.api.routes.health import make_health_router

DEGRADED = {"status": "degraded", "db": "unreachable"}


class FakeProbe:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def is_ready(self) -> bool:
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("password=hunter2 host=db.internal")
        if self.mode == "hang":
            await anyio.sleep_forever()
        return self.mode == "ready"


def _client(probe: FakeProbe, timeout_s: float = 1.0) -> TestClient:
    app = FastAPI()
    app.include_router(make_health_router(probe, timeout_s=timeout_s))
    return TestClient(app)


def test_healthz_never_calls_probe() -> None:
    probe = FakeProbe("raise")
    r = _client(probe).get("/healthz")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})
    assert probe.calls == 0


def test_readyz_ready_and_not_ready() -> None:
    r = _client(FakeProbe("ready")).get("/readyz")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "db": "ok"})
    r = _client(FakeProbe("no")).get("/readyz")
    assert (r.status_code, r.json()) == (503, DEGRADED)


def test_readyz_raising_logs_class_name_only() -> None:
    with structlog.testing.capture_logs() as logs:
        r = _client(FakeProbe("raise")).get("/readyz")
    assert (r.status_code, r.json()) == (503, DEGRADED)
    assert len(logs) == 1
    assert logs[0]["event"] == "readiness_probe_failed"
    assert logs[0]["exception_class"] == "RuntimeError"
    assert "hunter2" not in repr(logs)


def test_readyz_hanging_times_out() -> None:
    r = _client(FakeProbe("hang"), timeout_s=0.05).get("/readyz")
    assert (r.status_code, r.json()) == (503, DEGRADED)


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="timeout_s must be greater than zero"):
        make_health_router(FakeProbe("ready"), timeout_s=0)


def test_openapi_documents_both_bodies() -> None:
    schema = _client(FakeProbe("ready")).get("/openapi.json").json()
    responses = schema["paths"]["/readyz"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)


@pytest.mark.anyio
async def test_outer_cancellation_is_not_turned_into_503() -> None:
    app = FastAPI()
    router = make_health_router(FakeProbe("hang"), timeout_s=5.0)
    app.include_router(router)
    handler = next(r for r in router.routes if getattr(r, "path", "") == "/readyz")
    endpoint = handler.endpoint  # type: ignore[attr-defined]
    response = None
    with anyio.move_on_after(0.05) as outer:
        response = await endpoint()
    assert outer.cancel_called is True
    assert response is None
