"""Property 3: request-id resolution is safe and total.

Validates Requirements 5.3, 5.4 and 10.4.
"""

from __future__ import annotations

import uuid

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from recromancer.api.request_id import resolve_request_id
from tests.property.invariants import assert_request_id_valid, is_valid_request_id

ALLOWED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
_ALLOWED_TEXT = st.text(alphabet=ALLOWED, min_size=8, max_size=64)


def _of_length(n: int) -> SearchStrategy[str]:
    return st.text(alphabet=ALLOWED, min_size=n, max_size=n)


_NON_ASCII = st.one_of(
    _ALLOWED_TEXT.map(lambda s: s[:-1] + "\u0663"),  # Arabic-Indic digit three
    _ALLOWED_TEXT.map(lambda s: s[:-1] + "\uff21"),  # full-width A
    _ALLOWED_TEXT.map(lambda s: s[:-1] + "\u212a"),  # Kelvin sign, casefolds to k
    _ALLOWED_TEXT.map(lambda s: s[:-1] + "\u017f"),  # long s, casefolds to s
    _ALLOWED_TEXT.map(lambda s: s[:-1] + "\u00e9"),
    st.text(alphabet=st.characters(min_codepoint=128), min_size=8, max_size=64),
)

_DECORATED = st.one_of(
    _ALLOWED_TEXT.map(lambda s: s + "\n"),
    _ALLOWED_TEXT.map(lambda s: s + "\r\n"),
    _ALLOWED_TEXT.map(lambda s: " " + s),
    _ALLOWED_TEXT.map(lambda s: s[:4] + " " + s[4:]),
    _ALLOWED_TEXT.map(lambda s: s[:4] + "\x00" + s[4:]),
    _ALLOWED_TEXT.map(lambda s: s[:4] + "\t" + s[4:]),
)

headers = st.one_of(
    st.none(),
    _ALLOWED_TEXT,
    st.sampled_from([7, 8, 64, 65]).flatmap(_of_length),
    _DECORATED,
    _NON_ASCII,
    st.text(),
)


# Feature: platform-foundation, Property 3: Request-id resolution is safe and total
# Validates: Requirements 5.3, 5.4, 10.4
@hyp_settings(deadline=None)
@given(header=headers)
def test_request_id_resolution(header: str | None) -> None:
    calls = 0

    def new_id() -> str:
        nonlocal calls
        calls += 1
        return str(uuid.UUID(int=calls, version=4))

    result = resolve_request_id(header, new_id)
    accepted = header is not None and is_valid_request_id(header)
    generated = str(uuid.UUID(int=1, version=4))
    assert_request_id_valid(header, result, generated)
    if calls != (0 if accepted else 1):
        raise AssertionError(f"generator called {calls} times, accepted={accepted}")


def test_oracle_rejects_trailing_newline_and_bad_lengths() -> None:
    assert not is_valid_request_id("abcdefgh\n")
    assert not is_valid_request_id("a" * 7)
    assert not is_valid_request_id("a" * 65)
    assert not is_valid_request_id("")


def test_oracle_rejects_non_ascii_digits_and_letters() -> None:
    assert not is_valid_request_id("abcdefg\u0663")
    assert not is_valid_request_id("abcdefg\uff21")
    assert not is_valid_request_id("abcdefg\u212a")


def test_oracle_accepts_boundary_lengths_and_all_punctuation() -> None:
    assert is_valid_request_id("a" * 8)
    assert is_valid_request_id("a" * 64)
    assert is_valid_request_id("._-" * 3)
    assert is_valid_request_id(("." + "_" + "-") * 21 + ".")
    assert is_valid_request_id(ALLOWED[:64])
