"""Packaging checks: migration files ship in the package and repo rules hold."""

import hashlib
import tomllib
from importlib.resources import files
from pathlib import Path

from recromancer.db.runner import discover

_NAME = "0001_create_vector_extension.sql"
_EXPECTED = b"CREATE EXTENSION IF NOT EXISTS vector;\n"
_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent


def test_sql_file_is_found_through_importlib_resources() -> None:
    entry = files("recromancer.db") / "migrations" / _NAME
    data = entry.read_bytes()
    assert data == _EXPECTED  # exact statement with a single trailing LF
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data


def test_discover_returns_the_single_initial_migration() -> None:
    migrations = discover(files("recromancer.db") / "migrations")
    assert [m.name for m in migrations] == [_NAME]
    assert migrations[0].sql == _EXPECTED.decode("utf-8")
    assert migrations[0].checksum == hashlib.sha256(_EXPECTED).hexdigest()
    assert len(migrations[0].checksum) == 64


def test_wheel_target_lists_the_source_package() -> None:
    config = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "src/recromancer" in wheel["packages"]
    package_dir = _BACKEND / "src/recromancer"
    assert (package_dir / "db" / "migrations" / _NAME).is_file()
    assert (package_dir / "py.typed").is_file()


def test_gitattributes_pins_lf_for_sql_and_python() -> None:
    lines = (_REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.sql text eol=lf" in lines
    assert "*.py text eol=lf" in lines


def test_repo_migrations_are_valid_and_strictly_increasing() -> None:
    migrations = discover(files("recromancer.db") / "migrations")
    numbers = [int(m.name[:4]) for m in migrations]
    assert numbers  # discover() already rejects bad names, duplicates and BOMs
    assert numbers[0] == 1
    assert numbers == sorted(set(numbers))
    assert all(not m.sql.startswith("\ufeff") for m in migrations)
