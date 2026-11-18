"""R3.2 and R3.17: only ``config.py`` may read the process environment.

The scan is an AST walk over every Python file in the repository. Strings, comments and
docstrings never trip it because they are not ``ast.Attribute`` nodes.

Deliberately not detected (the design accepts these limits):

* aliasing the module, as in ``import os as o`` followed by ``o.environ``;
* dynamic access such as ``getattr(os, "environ")``, and ``from os import *``;
* ``os.environb`` and ``os.putenv``;
* reads through other modules such as ``importlib``, ``subprocess`` or ``posix``.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

FORBIDDEN_NAMES = frozenset({"environ", "getenv"})

REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_FILE = (REPO_ROOT / "backend" / "src" / "recromancer" / "config.py").resolve()
CONFTEST = (REPO_ROOT / "backend" / "tests" / "conftest.py").resolve()

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "env",
        ".git",
        "node_modules",
        ".hypothesis",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".import_linter_cache",
        ".tox",
        ".nox",
        ".idea",
        "__pycache__",
        "build",
        "dist",
        "site-packages",
    }
)
EXCLUDED_DIR_SUFFIXES = (".egg-info",)

MIN_SCANNED_FILES = 20


@dataclass(frozen=True)
class Finding:
    line: int


def find_environ_reads(source: str) -> list[Finding]:
    """Return the line of every forbidden environment access in ``source``.

    Raises ``SyntaxError`` when the source does not parse.
    """
    findings: list[Finding] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in FORBIDDEN_NAMES
        ):
            findings.append(Finding(node.lineno))
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module == "os"
            and node.level == 0
            and any(alias.name in FORBIDDEN_NAMES for alias in node.names)
        ):
            findings.append(Finding(node.lineno))
    return sorted(findings, key=lambda finding: finding.line)


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.endswith(EXCLUDED_DIR_SUFFIXES)


def iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded_dir(d))
        files.extend(Path(dirpath) / f for f in sorted(filenames) if f.endswith(".py"))
    return files


def _scan_repo() -> tuple[list[Path], list[str], list[str]]:
    """Return (scanned files, hit messages, parse-failure messages)."""
    scanned: list[Path] = []
    hits: list[str] = []
    failures: list[str] = []
    for path in iter_python_files(REPO_ROOT):
        if path.resolve() == ALLOWED_FILE:
            continue
        scanned.append(path)
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8-sig")
            findings = find_environ_reads(source)
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno or 0}")
            continue
        hits.extend(f"{relative}:{finding.line}" for finding in findings)
    return scanned, hits, failures


def test_repo_has_no_environment_reads_outside_config() -> None:
    scanned, hits, failures = _scan_repo()

    resolved = {path.resolve() for path in scanned}
    assert ALLOWED_FILE.is_file()
    assert ALLOWED_FILE not in resolved
    assert CONFTEST in resolved
    assert len(scanned) >= MIN_SCANNED_FILES

    assert not failures, "files that do not parse (SyntaxError):\n" + "\n".join(
        failures
    )
    assert not hits, "forbidden os.environ or os.getenv use at:\n" + "\n".join(hits)


def test_walk_skips_tool_directories(tmp_path: Path) -> None:
    for name in (".venv", "node_modules", "pkg.egg-info", "__pycache__", "keep"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "mod.py").write_text("x = 1\n", encoding="utf-8")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"keep/mod.py"}


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        ('import os\nx = os.environ["X"]\n', [2]),
        ('import os\nx = os.environ.get("X")\n', [2]),
        ('import os\nx = os.getenv("X")\n', [2]),
        ("from os import environ\n", [1]),
        ("from os import getenv as g\n", [1]),
        ("from os import path, environ as e\n", [1]),
    ],
)
def test_guard_flags_forbidden_snippets(source: str, lines: list[int]) -> None:
    assert [f.line for f in find_environ_reads(source)] == lines


@pytest.mark.parametrize(
    "source",
    [
        'x = "os.environ"\n',
        "# os.environ[...] is forbidden here\nx = 1\n",
        '"""Module docstring that mentions os.environ and os.getenv."""\n',
        'def f() -> None:\n    """Mentions os.environ."""\n',
        "from os import path\n",
        "import os\nx = os.path.join('a', 'b')\n",
    ],
)
def test_guard_ignores_strings_comments_and_docstrings(source: str) -> None:
    assert find_environ_reads(source) == []


def test_guard_reports_syntax_errors() -> None:
    with pytest.raises(SyntaxError):
        find_environ_reads("def (:\n")
