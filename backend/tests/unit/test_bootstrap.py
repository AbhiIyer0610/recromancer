"""Composition-root tests: no network, no real environment, logging state restored."""

import io
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from recromancer import bootstrap
from recromancer import observability as obs
from recromancer.api.app import create_app
from recromancer.config import Settings
from recromancer.db.postgres import PostgresDiagnostics, build_pool
from recromancer.service.ports import DbDiagnostics
from tests.fakes_api import FakeClock, log_records

NON_DEFAULT_URL = "postgresql://svc:" + "pw" + "x" * 10 + "@db.internal:6543/app"
SECRET_PASSWORD = "pw" + "x" * 10
ADMIN_TOKEN = "adm" + "q" * 40


def make_settings(**overrides: str) -> Settings:
    env: dict[str, str] = {
        "RECROMANCER_DATABASE_URL": NON_DEFAULT_URL,
        "RECROMANCER_ADMIN_TOKEN": ADMIN_TOKEN,
    }
    env.update(overrides)
    return Settings.from_env(env)


@pytest.fixture
def logs(
    log_stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> Iterator[io.StringIO]:
    """Route build_app's logging into the restored-afterwards ``log_stream``."""
    real = obs.configure_logging

    def forced(**kwargs: Any) -> None:
        kwargs["stream"] = log_stream
        real(**kwargs)

    monkeypatch.setattr(bootstrap, "configure_logging", forced)
    yield log_stream


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch) -> list[ConnectionPool]:
    """Record built pools and make any attempt to open one or connect fail."""
    built: list[ConnectionPool] = []
    real_build = build_pool

    def spy(url: Any) -> ConnectionPool:
        pool = real_build(url)
        built.append(pool)
        return pool

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network or pool use during build_app")

    monkeypatch.setattr("recromancer.bootstrap.build_pool", spy)
    monkeypatch.setattr(ConnectionPool, "open", boom)
    monkeypatch.setattr(psycopg, "connect", boom)
    monkeypatch.setattr(psycopg.Connection, "connect", boom)
    return built


class FakePool:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def open(self, *, wait: bool) -> None:
        self.calls.append(f"open(wait={wait})")

    def close(self) -> None:
        self.calls.append("close")


def test_system_clock_and_uuid() -> None:
    clock = bootstrap.SystemClock()
    moment = clock.now()
    assert moment.utcoffset() is not None
    assert moment.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    first = clock.monotonic()
    assert clock.monotonic() >= first
    value = bootstrap.uuid4_str()
    assert len(value) == 36 and value != bootstrap.uuid4_str()


def test_build_app_does_not_open_pool_or_connect(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    app = bootstrap.build_app(make_settings(), clock=FakeClock())
    assert isinstance(app, FastAPI)
    assert len(pools) == 1
    assert pools[0].closed  # never opened


def test_healthz_without_lifespan(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    app = bootstrap.build_app(make_settings(), clock=FakeClock())
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-Id"]
    assert pools[0].closed


def test_build_app_defaults_to_system_clock(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    app = bootstrap.build_app(make_settings())
    assert TestClient(app).get("/healthz").status_code == 200


def test_docs_disabled_in_production(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    settings = make_settings(
        RECROMANCER_PRODUCTION="true", RECROMANCER_DEMO_MODE="true"
    )
    app = bootstrap.build_app(settings, clock=FakeClock())
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert app.state.production is True


def test_docs_enabled_outside_production(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    app = bootstrap.build_app(make_settings(), clock=FakeClock())
    assert TestClient(app).get("/openapi.json").status_code == 200
    assert app.state.production is False


@pytest.mark.parametrize("demo", ["true", "false"])
def test_demo_mode_mirrors_settings(
    demo: str, logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    app = bootstrap.build_app(
        make_settings(RECROMANCER_DEMO_MODE=demo), clock=FakeClock()
    )
    assert app.state.demo_mode is (demo == "true")


def test_build_app_passes_production_and_demo_mode(
    logs: io.StringIO,
    pools: list[ConnectionPool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real = create_app

    def spy(**kwargs: Any) -> FastAPI:
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("recromancer.bootstrap.create_app", spy)
    settings = make_settings(
        RECROMANCER_PRODUCTION="true", RECROMANCER_DEMO_MODE="false"
    )
    bootstrap.build_app(settings, clock=FakeClock())
    assert captured["production"] is True
    assert captured["demo_mode"] is False
    assert captured["new_id"] is bootstrap.uuid4_str
    assert callable(captured["lifespan"])


def test_build_db_diagnostics() -> None:
    diagnostics: DbDiagnostics = bootstrap.build_db_diagnostics(make_settings())
    assert isinstance(diagnostics, PostgresDiagnostics)


def test_pool_lifespan_opens_then_closes() -> None:
    pool = FakePool()
    lifespan = bootstrap.pool_lifespan(pool)

    async def run() -> None:
        async with lifespan(FastAPI()):
            assert pool.calls == ["open(wait=False)"]

    import anyio

    anyio.run(run)
    assert pool.calls == ["open(wait=False)", "close"]


def test_pool_lifespan_closes_when_body_raises() -> None:
    pool = FakePool()
    lifespan = bootstrap.pool_lifespan(pool)

    async def run() -> None:
        async with lifespan(FastAPI()):
            raise RuntimeError("body failed")

    import anyio

    with pytest.raises(RuntimeError, match="body failed"):
        anyio.run(run)
    assert pool.calls == ["open(wait=False)", "close"]


def test_app_factory_loads_dotenv_before_settings(
    logs: io.StringIO,
    pools: list[ConnectionPool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    settings = make_settings()

    def fake_dotenv(**kwargs: object) -> Path | None:
        order.append("dotenv")
        return None

    def fake_from_env(environ: Mapping[str, str] | None = None) -> Settings:
        order.append("from_env")
        return settings

    monkeypatch.setattr(bootstrap, "load_dotenv", fake_dotenv)
    monkeypatch.setattr(Settings, "from_env", staticmethod(fake_from_env))
    app = bootstrap.app_factory()
    assert order == ["dotenv", "from_env"]
    assert isinstance(app, FastAPI)


def test_startup_log_has_no_secrets(
    logs: io.StringIO, pools: list[ConnectionPool]
) -> None:
    bootstrap.build_app(make_settings(), clock=FakeClock())
    text = logs.getvalue()
    records = [r for r in log_records(logs) if r["message"] == "starting"]
    assert len(records) == 1
    assert records[0]["logger"] == "recromancer.bootstrap"
    assert NON_DEFAULT_URL not in text
    assert SECRET_PASSWORD not in text
    assert ADMIN_TOKEN not in text
    json.loads(text.splitlines()[-1])
