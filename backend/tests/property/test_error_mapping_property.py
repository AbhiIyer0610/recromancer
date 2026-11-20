"""Property 2: the error-to-status mapping is total and valid.

Validates Requirements 4.4 and 10.3.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

import recromancer
from recromancer.api.errors import ERROR_HTTP_STATUS, http_status_for
from recromancer.domain.errors import ErrorCode
from tests.property.invariants import assert_mapping_total

SRC_ROOT = Path(next(iter(recromancer.__path__)))
MAP_FILE = SRC_ROOT / "api" / "errors.py"


# Feature: platform-foundation, Property 2: The error-to-status mapping is total and valid  # noqa: E501
# Validates: Requirements 4.4, 10.3
@hyp_settings(deadline=None)
@given(code=st.sampled_from(list(ErrorCode)))
def test_mapping_is_total(code: ErrorCode) -> None:
    status = http_status_for(code)
    assert status == ERROR_HTTP_STATUS[code]
    assert type(status) is int
    assert 400 <= HTTPStatus(status) <= 599
    assert_mapping_total(ERROR_HTTP_STATUS, list(ErrorCode))


def test_mapping_is_read_only() -> None:
    mutable = cast("dict[ErrorCode, int]", ERROR_HTTP_STATUS)
    with pytest.raises(TypeError):
        mutable[ErrorCode.NOT_FOUND] = 200


def _copy() -> dict[ErrorCode, int]:
    return dict(ERROR_HTTP_STATUS)


def test_missing_member_fails() -> None:
    broken = _copy()
    del broken[ErrorCode.NOT_FOUND]
    with pytest.raises(AssertionError, match="missing .*NOT_FOUND") as info:
        assert_mapping_total(broken, list(ErrorCode))
    assert "404" not in str(info.value)


def test_extra_key_fails() -> None:
    broken = cast("dict[ErrorCode, int]", {**_copy(), "bogus": 400})
    with pytest.raises(AssertionError, match="extra") as info:
        assert_mapping_total(broken, list(ErrorCode))
    assert "bogus" not in str(info.value)


def test_bool_status_fails() -> None:
    broken = _copy()
    broken[ErrorCode.NOT_FOUND] = True
    with pytest.raises(AssertionError, match="NOT_FOUND: status is not an int"):
        assert_mapping_total(broken, list(ErrorCode))


@pytest.mark.parametrize("bad", [200, 600, 499])
def test_invalid_status_fails(bad: int) -> None:
    broken = _copy()
    broken[ErrorCode.NOT_FOUND] = bad
    with pytest.raises(AssertionError) as info:
        assert_mapping_total(broken, list(ErrorCode))
    message = str(info.value)
    assert message.startswith(f"NOT_FOUND: status {bad} ")
    assert "404" not in message and "ErrorCode" not in message


def _is_code_key(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ErrorCode"
    )


def _annotation_is_code_mapping(node: ast.expr) -> bool:
    text = ast.unparse(node).replace(" ", "")
    return "ErrorCode," in text and "Mapping" in text or "dict[ErrorCode," in text


def _map_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(_is_code_key(k) for k in node.keys):
            found.append(f"dict literal at line {node.lineno}")
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and _annotation_is_code_mapping(
            node.annotation
        ):
            found.append(f"annotated variable at line {node.lineno}")
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "ERROR_HTTP_STATUS":
                found.append(f"assignment at line {node.lineno}")
    return found


def test_status_map_lives_in_one_place() -> None:
    offenders = {
        str(path.relative_to(SRC_ROOT)): defs
        for path in SRC_ROOT.rglob("*.py")
        if path != MAP_FILE and (defs := _map_definitions(path))
    }
    assert offenders == {}
    assert _map_definitions(MAP_FILE), "scanner no longer finds the map"


def test_scanner_detects_a_second_map(tmp_path: Path) -> None:
    other = tmp_path / "other.py"
    other.write_text(
        "from collections.abc import Mapping\n"
        "M: Mapping[ErrorCode, int] = {ErrorCode.NOT_FOUND: 404}\n",
        encoding="utf-8",
    )
    assert len(_map_definitions(other)) == 2


def test_mapping_type_alias_is_mapping() -> None:
    assert isinstance(ERROR_HTTP_STATUS, Mapping)
