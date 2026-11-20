"""Shared pytest fixtures and Hypothesis profiles."""

import io
import logging
from collections.abc import Iterator

import pytest
import structlog
from hypothesis import settings

from recromancer import config
from recromancer import observability as obs
from tests.fakes_api import FakeClock

settings.register_profile("default", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=1000, deadline=None)
settings.load_profile(config.read_environment().get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def log_stream(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Configure logging onto a fresh StringIO and restore global state afterwards."""
    monkeypatch.setattr(obs, "_state", None)
    root = logging.getLogger()
    saved_level = root.level
    saved = {
        name: (lg.propagate, lg.disabled, list(lg.handlers))
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        for lg in [logging.getLogger(name)]
    }
    buffer = io.StringIO()
    obs.configure_logging(level="INFO", clock=FakeClock(), stream=buffer)
    yield buffer
    for handler in list(root.handlers):
        if getattr(handler, "_recromancer_log_handler", False):
            root.removeHandler(handler)
    root.setLevel(saved_level)
    for name, (propagate, disabled, handlers) in saved.items():
        lg = logging.getLogger(name)
        lg.propagate = propagate
        lg.disabled = disabled
        lg.handlers[:] = handlers
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
