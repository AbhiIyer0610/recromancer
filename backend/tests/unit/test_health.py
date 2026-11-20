"""Liveness and readiness endpoints.

Covers Requirements 5.10 to 5.14. Probes are fakes; the only real wait is the
single 0.05 s ``/readyz`` timeout case.
"""

import asyncio
import io
import time
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from recromancer.api.app import create_app
from recromancer.api.routes.health import make_health_router
from tests.fakes_api import FakeClock, FakeProbe, counter_ids, log_records

DEGRADED = {"status": "degraded", "db": "unreachable"}
MARKER = "probe-marker-" + "m" * 8
TIMEOUT_MESSAGE = "timeout_s must be greater than zero"


def make_client(probe: FakeProbe, *, timeout_s: float = 1.0) -> TestClient:
    app = create_app(
        readiness=probe,
        clock=FakeClock(step=0.001),
        new_id=counter_ids(),
        readyz_timeout_s=timeout_s,
    )
    return TestClient(app)


def readyz_endpoint(probe: Any, timeout_s: float) -> Any:
    router = make_health_router(probe, timeout_s=timeout_s)
    (route,) = [
        r for r in router.routes if isinstance(r, APIRoute) and r.path == "/readyz"
    ]
    return route.endpoint


# --- /healthz and /readyz bodies ------------------------------------------------------


def test_healthz_never_calls_the_probe(log_stream: io.StringIO) -> None:
    probe = FakeProbe("raise")
    response = make_client(probe).get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    assert probe.calls == 0


def test_readyz_ready(log_stream: io.StringIO) -> None:
    response = make_client(FakeProbe("ready")).get("/readyz")
    assert (response.status_code, response.json()) == (
        200,
        {"status": "ok", "db": "ok"},
    )


def test_readyz_not_ready(log_stream: io.StringIO) -> None:
    response = make_client(FakeProbe("not_ready")).get("/readyz")
    assert (response.status_code, response.json()) == (503, DEGRADED)


def test_readyz_raising_probe_logs_only_the_exception_class(
    log_stream: io.StringIO,
) -> None:
    probe = FakeProbe("raise", message=f"password={MARKER} host=db.internal")
    response = make_client(probe).get("/readyz")
    assert (response.status_code, response.json()) == (503, DEGRADED)
    assert MARKER not in response.text

    (line,) = [
        r for r in log_records(log_stream) if r["logger"] == "recromancer.api.health"
    ]
    assert set(line) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "request_id",
        "method",
        "path",
        "exception_class",
    }
    assert line["message"] == "readiness_probe_failed"
    assert line["level"] == "warning"
    assert line["exception_class"] == "RuntimeError"
    assert MARKER not in log_stream.getvalue()
    assert "db.internal" not in log_stream.getvalue()


def test_readyz_hanging_probe_times_out_with_503(log_stream: io.StringIO) -> None:
    started = time.monotonic()
    response = make_client(FakeProbe("hang"), timeout_s=0.05).get("/readyz")
    assert time.monotonic() - started < 2.0
    assert (response.status_code, response.json()) == (503, DEGRADED)


# --- cancellation and BaseException ---------------------------------------------------


class CancellingProbe:
    """Cancels an outer scope from inside the probe, then hangs."""

    def __init__(self, outer: anyio.CancelScope) -> None:
        self.outer = outer

    async def is_ready(self) -> bool:
        self.outer.cancel()
        await anyio.sleep_forever()
        return True  # pragma: no cover


@pytest.mark.anyio
async def test_outer_cancellation_is_not_swallowed_or_turned_into_503() -> None:
    response: Any = None
    with anyio.CancelScope() as outer:
        endpoint = readyz_endpoint(CancellingProbe(outer), timeout_s=60.0)
        response = await endpoint()
    assert outer.cancel_called is True
    assert response is None


class RaisingBase:
    def __init__(self, exc_type: type[BaseException]) -> None:
        self.exc_type = exc_type

    async def is_ready(self) -> bool:
        raise self.exc_type()


@pytest.mark.anyio
@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, asyncio.CancelledError])
async def test_base_exceptions_from_the_probe_propagate(
    exc_type: type[BaseException],
) -> None:
    endpoint = readyz_endpoint(RaisingBase(exc_type), timeout_s=60.0)
    with pytest.raises(exc_type):
        await endpoint()


# --- configuration validation ---------------------------------------------------------

BAD_TIMEOUTS = [0, -1, -0.0, float("nan")]


@pytest.mark.parametrize("value", BAD_TIMEOUTS)
def test_create_app_rejects_non_positive_and_nan_timeouts(value: float) -> None:
    with pytest.raises(ValueError) as info:
        create_app(
            readiness=FakeProbe(),
            clock=FakeClock(),
            new_id=counter_ids(),
            readyz_timeout_s=value,
        )
    assert str(info.value) == "readyz_timeout_s must be greater than zero"
    assert str(value) not in str(info.value)


@pytest.mark.parametrize("value", BAD_TIMEOUTS)
def test_make_health_router_rejects_non_positive_and_nan_timeouts(
    value: float,
) -> None:
    with pytest.raises(ValueError) as info:
        make_health_router(FakeProbe(), timeout_s=value)
    assert str(info.value) == TIMEOUT_MESSAGE
    assert str(value) not in str(info.value)


# --- OpenAPI --------------------------------------------------------------------------


def test_openapi_documents_the_health_responses(log_stream: io.StringIO) -> None:
    app: FastAPI = create_app(
        readiness=FakeProbe(), clock=FakeClock(), new_id=counter_ids()
    )
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    healthz = paths["/healthz"]["get"]["responses"]
    readyz = paths["/readyz"]["get"]["responses"]
    assert set(healthz) == {"200", "500"}
    assert set(readyz) == {"200", "500", "503"}
    for responses in (healthz, readyz):
        schema = responses["500"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ErrorBody")
    degraded = readyz["503"]["content"]["application/json"]["schema"]
    assert degraded["$ref"].endswith("/DegradedBody")
