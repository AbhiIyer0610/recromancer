"""Fakes satisfy each port structurally (checked by mypy) and behave as specified."""

from datetime import UTC, datetime, timedelta

import anyio
import pytest

from recromancer.service.ports import Clock, DbDiagnostics, IdGenerator, ReadinessProbe


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2024, 1, 1, tzinfo=UTC)
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


class FakeProbe:
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    async def is_ready(self) -> bool:
        return self._ready


class FakeDiagnostics:
    def __init__(self, extensions: set[str], *, fail: bool = False) -> None:
        self._extensions = extensions
        self._fail = fail

    def ping(self) -> None:
        if self._fail:
            raise ConnectionError("down")

    def has_extension(self, name: str) -> bool:
        return name in self._extensions


def test_clock_fake_is_utc_aware_and_advances() -> None:
    fake = FakeClock()
    clock: Clock = fake
    assert clock.now().utcoffset() == timedelta(0)
    fake.advance(2.5)
    assert clock.monotonic() == 2.5
    assert clock.now() == datetime(2024, 1, 1, 0, 0, 2, 500000, tzinfo=UTC)


@pytest.mark.anyio
@pytest.mark.parametrize("ready", [True, False])
async def test_readiness_probe_fake_reports_state(ready: bool) -> None:
    probe: ReadinessProbe = FakeProbe(ready)
    with anyio.fail_after(1):
        assert await probe.is_ready() is ready


def test_id_generator_alias_accepts_plain_callable() -> None:
    counter = iter(range(3))
    new_id: IdGenerator = lambda: f"id-{next(counter)}"  # noqa: E731
    assert [new_id(), new_id()] == ["id-0", "id-1"]


def test_db_diagnostics_fake_satisfies_port() -> None:
    diag: DbDiagnostics = FakeDiagnostics({"vector"})
    diag.ping()
    assert diag.has_extension("vector") is True
    assert diag.has_extension("other") is False
    failing: DbDiagnostics = FakeDiagnostics(set(), fail=True)
    with pytest.raises(ConnectionError):
        failing.ping()
