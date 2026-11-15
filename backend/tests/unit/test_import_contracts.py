"""Checks that import-linter contracts C1 to C8 are declared and hold on the tree."""

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[2]
PREFIX = "recromancer."
ADAPTERS = ("llm", "embed", "retrieval", "db")
EXPECTED_NAMES = (
    "domain-is-pure",
    "pure-logic-imports-domain-only",
    "config-is-narrow",
    "service-uses-ports-only",
    "delivery-skips-adapters",
    "only-bootstrap-wires-adapters",
    "adapters-are-independent",
    "layer-order",
)


@pytest.fixture(scope="module")
def contracts() -> dict[str, dict[str, Any]]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    section: dict[str, Any] = data["tool"]["importlinter"]
    assert section["root_package"] == "recromancer"
    assert section["include_external_packages"] is True
    return {c["name"]: c for c in section["contracts"]}


def test_all_contracts_declared_by_name(
    contracts: dict[str, dict[str, Any]],
) -> None:
    assert set(contracts) == set(EXPECTED_NAMES)


def test_c5_allows_indirect_imports(contracts: dict[str, dict[str, Any]]) -> None:
    assert contracts["delivery-skips-adapters"]["allow_indirect_imports"] is True


def test_c2_covers_grounding_and_ingest(
    contracts: dict[str, dict[str, Any]],
) -> None:
    sources = set(contracts["pure-logic-imports-domain-only"]["source_modules"])
    assert sources == {PREFIX + "grounding", PREFIX + "ingest"}


def test_c6_forbids_all_adapters(contracts: dict[str, dict[str, Any]]) -> None:
    forbidden = set(contracts["only-bootstrap-wires-adapters"]["forbidden_modules"])
    assert forbidden == {PREFIX + name for name in ADAPTERS}


def test_c7_independence_covers_adapters(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["adapters-are-independent"]
    assert contract["type"] == "independence"
    assert set(contract["modules"]) == {PREFIX + name for name in ADAPTERS}


def test_c8_layers_match_documented_order(
    contracts: dict[str, dict[str, Any]],
) -> None:
    contract = contracts["layer-order"]
    assert contract["type"] == "layers"
    assert contract["containers"] == ["recromancer"]
    assert contract["layers"] == [
        "bootstrap",
        "api | mcp",
        "service",
        "llm | embed | retrieval | db",
        "grounding | ingest",
        "domain",
    ]


def test_lint_imports_passes_on_real_tree() -> None:
    result = subprocess.run(
        # `python -m importlinter.cli` is a silent no-op (no __main__ guard), so call
        # the click entry point of the running interpreter's environment directly.
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command;lint_imports_command()",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
