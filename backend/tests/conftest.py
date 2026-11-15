"""Shared pytest fixtures and Hypothesis profiles."""

import os

import pytest
from hypothesis import settings

settings.register_profile("default", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=1000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
