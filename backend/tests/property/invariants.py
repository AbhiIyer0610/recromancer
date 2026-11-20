"""Reusable invariant checks for property tests. Contains no test functions.

Failure messages never include the secret or the inspected text, so a failing run
cannot leak either into pytest or Hypothesis output.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from urllib.parse import quote

from recromancer.domain.errors import ErrorCode


def secret_forms(secret: str) -> dict[str, str]:
    """Named textual forms in which a secret could appear in observable output."""
    return {
        "raw": secret,
        "json-ascii": json.dumps(secret, ensure_ascii=True)[1:-1],
        "json-unicode": json.dumps(secret, ensure_ascii=False)[1:-1],
        "percent": quote(secret),
        "percent-strict": quote(secret, safe=""),
    }


def assert_secret_absent(secret: str, *texts: str) -> None:
    """Assert that no form of ``secret`` occurs in any of ``texts``.

    An empty secret is skipped. The error names the matching form and the text index
    only.
    """
    if not secret:
        return
    forms = secret_forms(secret)
    for index, text in enumerate(texts):
        for name, form in forms.items():
            if form and form in text:
                raise AssertionError(f"form {name!r} present in text #{index}")


_REQUEST_ID_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)


def is_valid_request_id(value: str) -> bool:
    """Regex-free oracle: 8 to 64 characters, each in ``A-Z a-z 0-9 . _ -``."""
    return 8 <= len(value) <= 64 and all(ch in _REQUEST_ID_ALPHABET for ch in value)


def assert_request_id_valid(header: str | None, result: str, generated: str) -> None:
    """Assert request-id resolution returned the right one of its two allowed forms.

    ``generated`` is the generator's output. The header is attacker-controlled, so
    messages report lengths and the failed clause only.
    """
    if header is not None and is_valid_request_id(header):
        if result != header:
            raise AssertionError(
                f"valid header (len {len(header)}) not returned unchanged "
                f"(result len {len(result)})"
            )
    else:
        header_len = None if header is None else len(header)
        if result != generated:
            raise AssertionError(
                f"invalid or absent header (len {header_len}) did not yield the "
                f"generated id (result len {len(result)})"
            )
        parsed = uuid.UUID(generated)
        if parsed.version != 4 or str(parsed) != generated:
            raise AssertionError("generated id is not a canonical UUID4 string")
    if not is_valid_request_id(result):
        raise AssertionError(f"result (len {len(result)}) is not a valid request id")


def _member_name(member: object) -> str:
    if isinstance(member, ErrorCode):
        return member.name
    return f"<{type(member).__name__}>"


def assert_mapping_total(
    mapping: Mapping[ErrorCode, int], codes: Iterable[ErrorCode]
) -> None:
    """Assert `mapping` covers exactly `codes` and every status is valid.

    A valid status is an int (not a bool) in 400..599 that `http.HTTPStatus`
    defines. Messages name the code and the status number only.
    """
    expected = set(codes)
    actual = set(mapping)
    missing = sorted(_member_name(c) for c in expected - actual)
    extra = sorted(_member_name(c) for c in actual - expected)
    if missing or extra:
        raise AssertionError(f"key set differs: missing {missing}, extra {extra}")
    for code, status in mapping.items():
        name = _member_name(code)
        if isinstance(status, bool) or not isinstance(status, int):
            raise AssertionError(f"{name}: status is not an int")
        if not 400 <= status <= 599:
            raise AssertionError(f"{name}: status {status} outside 400..599")
        try:
            HTTPStatus(status)
        except ValueError:
            raise AssertionError(
                f"{name}: status {status} is not a defined HTTP status"
            ) from None
