"""Log-key rule: decides whether a log field name may carry a secret value.

Order of evaluation: counter allowlist, substring markers, whole-segment markers.
"""

from __future__ import annotations

import re

SECRET_SUBSTRINGS = (
    "secret",
    "password",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "signingkey",
    "authkey",
)
SECRET_SEGMENTS = frozenset({"key", "authorization", "cookie"})
COUNTER_SUFFIX = "tokens"

_SEPARATORS = re.compile(r"[_-]")


def name_segments(name: str) -> tuple[str, ...]:
    """Split on ``_`` and ``-`` and drop empty segments."""
    return tuple(part for part in _SEPARATORS.split(name) if part)


def is_sensitive_key(key: str) -> bool:
    """Return True when a log key looks like it holds a secret."""
    lowered = key.lower()
    segments = name_segments(lowered)
    if segments and segments[-1] == COUNTER_SUFFIX:
        return False
    if any(marker in lowered for marker in SECRET_SUBSTRINGS):
        return True
    return any(segment in SECRET_SEGMENTS for segment in segments)
