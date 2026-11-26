"""The built wheel ships the Ledgerline fixture bundle byte-for-byte (R2.11).

Builds a wheel with ``pip wheel --no-deps ./backend`` (the command the platform
foundation spec documents for the packaging check), so hatchling does not have
to be importable in the test environment. pip fetches the build backend into an
isolated environment, so the test needs the package index to be reachable.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_SOURCE = _BACKEND / "src" / "recromancer" / "ingest" / "fixtures" / "ledgerline"
_PREFIX = "recromancer/ingest/fixtures/ledgerline/"


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            str(_BACKEND),
            "-w",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    wheels = list(out.glob("recromancer-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contains_ledgerline_bundle_byte_for_byte(wheel_path: Path) -> None:
    manifest = json.loads((_SOURCE / "manifest.json").read_text(encoding="utf-8"))
    expected = ["manifest.json", "README.md", *(f["path"] for f in manifest["files"])]
    assert len(expected) == len(set(expected))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        for rel in expected:
            assert _PREFIX + rel in names, rel
            assert wheel.read(_PREFIX + rel) == (_SOURCE / rel).read_bytes(), rel
        shipped = {n for n in names if n.startswith("recromancer/ingest/fixtures/")}
    assert shipped == {_PREFIX + rel for rel in expected}
    assert not any(n.endswith("__init__.py") for n in shipped)
