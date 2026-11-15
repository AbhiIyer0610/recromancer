"""Checks that the backend scaffold matches Requirement 1."""

import importlib
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
PACKAGE = BACKEND / "src" / "recromancer"
SUBPACKAGES = (
    "domain",
    "grounding",
    "ingest",
    "retrieval",
    "llm",
    "embed",
    "db",
    "service",
    "api",
    "mcp",
)
# Assembled at runtime so this file never contains the literals it scans for.
FORBIDDEN = ("pytest" + "-asyncio", "anyio" + "[trio]", "asyncio" + "_mode")


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    with (BACKEND / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[\[<>=!~ ;]", item, maxsplit=1)[0].lower() for item in requirements
    }


def test_build_system(pyproject: dict[str, Any]) -> None:
    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    project = pyproject["project"]
    assert project["requires-python"] == ">=3.11"
    wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/recromancer"]


def test_dependencies(pyproject: dict[str, Any]) -> None:
    project = pyproject["project"]
    runtime = _names(project["dependencies"])
    assert {
        "fastapi",
        "pydantic",
        "pydantic-settings",
        "httpx",
        "structlog",
        "psycopg",
        "psycopg-pool",
        "anyio",
        "uvicorn",
    } <= runtime
    dev = _names(project["optional-dependencies"]["dev"])
    assert {
        "pytest",
        "pytest-cov",
        "hypothesis",
        "anyio",
        "ruff",
        "mypy",
        "import-linter",
    } <= dev
    assert "psycopg[binary]" in {
        item.split(">=")[0].lower() for item in project["dependencies"]
    }


def test_tool_configuration(pyproject: dict[str, Any]) -> None:
    tool = pyproject["tool"]
    assert tool["ruff"]["line-length"] == 88
    assert {"E", "F", "I", "UP", "B"} <= set(tool["ruff"]["lint"]["select"])
    mypy = tool["mypy"]
    assert mypy["strict"] is True
    assert mypy["python_version"] == "3.11"
    assert set(mypy["files"]) == {"src", "tests"}
    assert "exclude" not in mypy
    pytest_cfg = tool["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["tests"]
    assert pytest_cfg["addopts"] == "--cov=recromancer --cov-report=term-missing"
    assert tool["coverage"]["report"]["fail_under"] == 85


def test_package_imports_from_src_layout() -> None:
    # Also gives coverage a measured module: without any import it reports
    # "module-not-imported" / "no data" and fails the floor.
    root = importlib.import_module("recromancer")
    assert Path(str(root.__file__)).resolve() == (PACKAGE / "__init__.py").resolve()
    for name in SUBPACKAGES:
        importlib.import_module(f"recromancer.{name}")


def test_package_layout() -> None:
    assert (PACKAGE / "__init__.py").is_file()
    assert (PACKAGE / "py.typed").is_file()
    for name in SUBPACKAGES:
        files = sorted(p.name for p in (PACKAGE / name).iterdir() if p.is_file())
        assert "__init__.py" in files, name


def test_tests_tree() -> None:
    tests = BACKEND / "tests"
    assert (tests / "__init__.py").is_file()
    for name in ("unit", "property", "integration", "contract"):
        assert (tests / name / "__init__.py").is_file(), name
    assert (tests / "conftest.py").is_file()
    assert (REPO / "scripts").is_dir()


def test_no_forbidden_async_tooling() -> None:
    # Scope is deliberately narrow: docs, specs, .kiro and this file legitimately
    # name the forbidden strings and are never scanned.
    files = [BACKEND / "pyproject.toml", BACKEND / "tests" / "conftest.py"]
    files += sorted(BACKEND.glob("requirements*.txt"))
    files += sorted(REPO.glob("requirements*.txt"))
    files += sorted((REPO / ".github" / "workflows").glob("*.yml"))
    files += sorted((REPO / ".github" / "workflows").glob("*.yaml"))
    for path in files:
        text = path.read_text(encoding="utf-8-sig").lower()
        for needle in FORBIDDEN:
            assert needle not in text, f"{needle} found in {path.name}"
