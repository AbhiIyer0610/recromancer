"""Reusable invariant checks for property tests. Contains no test functions.

Failure messages never include the secret or the inspected text, so a failing run
cannot leak either into pytest or Hypothesis output.
"""

from __future__ import annotations

import json
from urllib.parse import quote


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
