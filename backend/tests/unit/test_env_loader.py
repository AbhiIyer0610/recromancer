"""Unit tests for ``load_dotenv`` and ``read_environment`` in ``recromancer.config``.

Every case uses ``tmp_path`` and an injected ``environ`` mapping; the process
environment is never read or modified by the loader under test.
"""

from pathlib import Path
from typing import Any

import pytest

from recromancer.config import load_dotenv, read_environment

KEY = "RECROMANCER_LOG_LEVEL"


def _repo(tmp_path: Path, content: bytes | str | None = None) -> Path:
    """Create a repo root with ``.env.example`` and optionally a ``.env``."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if content is not None:
        (tmp_path / ".env").write_bytes(content)
    return tmp_path


def _load(root: Path, env: dict[str, str]) -> Path | None:
    return load_dotenv(environ=env, start=root)


# --- R3.13: no override -------------------------------------------------------


def test_dev_loads_variables_from_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\nRECROMANCER_DEMO_MODE=false\n")
    env: dict[str, str] = {}
    assert _load(root, env) == root / ".env"
    assert env == {KEY: "DEBUG", "RECROMANCER_DEMO_MODE": "false"}


def test_existing_variables_are_not_overridden(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\nOTHER=file\n")
    env = {KEY: "ERROR"}
    _load(root, env)
    assert env == {KEY: "ERROR", "OTHER": "file"}


def test_existing_name_is_matched_case_insensitively(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    env = {"recromancer_log_level": "ERROR"}
    _load(root, env)
    assert env == {"recromancer_log_level": "ERROR"}


def test_existing_empty_value_still_blocks_file(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    env = {KEY: ""}
    _load(root, env)
    assert env == {KEY: ""}


# --- R3.15: production never opens the file ----------------------------------


def _forbid_file_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the .env file system must not be touched in production")

    for name in ("read_text", "read_bytes", "open", "is_file", "exists"):
        monkeypatch.setattr(Path, name, boom)


@pytest.mark.parametrize(
    "flag", ["maybe", "1", "true", "yes", "on", "TRUE", "On", " true ", "2"]
)
def test_production_never_opens_file_and_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    env = {"RECROMANCER_PRODUCTION": flag}
    _forbid_file_access(monkeypatch)
    assert load_dotenv(environ=env, start=root) is None
    assert env == {"RECROMANCER_PRODUCTION": flag}


def test_production_flag_name_is_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    env = {"recromancer_production": "true"}
    _forbid_file_access(monkeypatch)
    assert load_dotenv(environ=env, start=root) is None
    assert env == {"recromancer_production": "true"}


@pytest.mark.parametrize(
    "flag",
    [
        "",
        "0",
        "false",
        "no",
        "off",
        "FALSE",
        "False",
        "NO",
        "No",
        "OFF",
        "Off",
        " off ",
    ],
)
def test_non_production_values_load_the_file(tmp_path: Path, flag: str) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    env = {"RECROMANCER_PRODUCTION": flag}
    assert _load(root, env) == root / ".env"
    assert env[KEY] == "DEBUG"
    assert env["RECROMANCER_PRODUCTION"] == flag


@pytest.mark.parametrize(
    "name",
    ["RECROMANCER_PRODUCTION", "recromancer_production", "Recromancer_Production"],
)
def test_production_in_file_is_ignored(tmp_path: Path, name: str) -> None:
    root = _repo(tmp_path, f"{name}=true\n{KEY}=DEBUG\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {KEY: "DEBUG"}


def test_production_in_file_does_not_override_existing_flag(tmp_path: Path) -> None:
    root = _repo(tmp_path, "RECROMANCER_PRODUCTION=true\n")
    env = {"RECROMANCER_PRODUCTION": "false"}
    _load(root, env)
    assert env == {"RECROMANCER_PRODUCTION": "false"}


# --- R3.14: parsing -----------------------------------------------------------


def test_bom_before_first_key_is_consumed(tmp_path: Path) -> None:
    root = _repo(tmp_path, b"\xef\xbb\xbf" + f"{KEY}=DEBUG\n".encode())
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {KEY: "DEBUG"}
    assert not any(name.startswith("\ufeff") for name in env)


def test_crlf_and_lf_parse_identically_without_carriage_returns(
    tmp_path: Path,
) -> None:
    body = f"# comment\n{KEY}=DEBUG\n\nA=1\nB=\"two\"\nC='three'\nD=\n"
    lf_root = _repo(tmp_path / "lf", body)
    crlf_root = _repo(tmp_path / "crlf", body.replace("\n", "\r\n"))
    lf_env: dict[str, str] = {}
    crlf_env: dict[str, str] = {}
    _load(lf_root, lf_env)
    _load(crlf_root, crlf_env)
    assert lf_env == crlf_env
    assert lf_env == {KEY: "DEBUG", "A": "1", "B": "two", "C": "three", "D": ""}
    assert all("\r" not in k and "\r" not in v for k, v in crlf_env.items())


def test_quotes_are_removed_and_inner_hash_preserved(tmp_path: Path) -> None:
    root = _repo(tmp_path, 'A="a # b"\nB=\'c # d\'\nC="it\'s"\nD=plain\n')
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": "a # b", "B": "c # d", "C": "it's", "D": "plain"}


def test_comment_and_blank_lines_are_ignored(tmp_path: Path) -> None:
    root = _repo(tmp_path, "# top\n\n   \n  # indented\nA=1\n#B=2\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": "1"}


def test_lines_without_equals_and_empty_keys_are_ignored(tmp_path: Path) -> None:
    root = _repo(tmp_path, "JUSTTEXT\n=novalue\n  = spaced\nA=1\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": "1"}


def test_first_occurrence_wins_for_repeated_key(tmp_path: Path) -> None:
    root = _repo(tmp_path, "A=first\nA=second\na=third\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": "first"}


def test_value_may_contain_equals(tmp_path: Path) -> None:
    root = _repo(tmp_path, "URL=postgresql://u:p@h/db?x=1\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"URL": "postgresql://u:p@h/db?x=1"}


def test_whitespace_around_key_and_value_is_stripped(tmp_path: Path) -> None:
    root = _repo(tmp_path, "  A  =  1  \n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": "1"}


def test_unterminated_or_single_quote_char_is_kept_literally(tmp_path: Path) -> None:
    root = _repo(tmp_path, "A=\"open\nB='\nC=\"mixed'\n")
    env: dict[str, str] = {}
    _load(root, env)
    assert env == {"A": '"open', "B": "'", "C": "\"mixed'"}


# --- discovery ----------------------------------------------------------------


def test_discovery_walks_up_from_nested_directory(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{KEY}=DEBUG\n")
    nested = root / "backend" / "src" / "pkg"
    nested.mkdir(parents=True)
    env: dict[str, str] = {}
    assert _load(nested, env) == root / ".env"
    assert env == {KEY: "DEBUG"}


def test_nearest_marker_directory_wins(tmp_path: Path) -> None:
    _repo(tmp_path, "A=outer\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / ".env.example").write_text("", encoding="utf-8")
    (inner / ".env").write_text("A=inner\n", encoding="utf-8")
    env: dict[str, str] = {}
    assert _load(inner, env) == inner / ".env"
    assert env == {"A": "inner"}


def test_no_marker_above_start_returns_none(tmp_path: Path) -> None:
    # A .env exists, but no .env.example in the start directory or any parent
    # (pytest's tmp_path lives under the system temp directory).
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    env: dict[str, str] = {}
    assert _load(nested, env) is None
    assert env == {}


def test_marker_present_but_env_missing_returns_none(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    env: dict[str, str] = {}
    assert _load(root, env) is None
    assert env == {}


def test_env_that_is_a_directory_returns_none(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".env").mkdir()
    env: dict[str, str] = {}
    assert _load(root, env) is None
    assert env == {}


def test_return_value_is_path_of_env_file_read(tmp_path: Path) -> None:
    root = _repo(tmp_path, "A=1\n")
    result = _load(root, {})
    assert result == root.resolve() / ".env"
    assert result is not None and result.is_file()


# --- read_environment ---------------------------------------------------------


def test_read_environment_returns_independent_dict_copies() -> None:
    first = read_environment()
    second = read_environment()
    assert first is not second
    assert first == second
    sentinel = "RECROMANCER_TEST_ONLY_SENTINEL_KEY"
    first[sentinel] = "x"
    assert sentinel not in read_environment()
