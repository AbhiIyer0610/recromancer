"""Request-id resolution: echo a safe client id, otherwise generate one."""

from __future__ import annotations

import re

from recromancer.service.ports import IdGenerator

# fullmatch, not ^...$: "$" would accept a trailing newline.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")


def resolve_request_id(header: str | None, new_id: IdGenerator) -> str:
    """Return ``header`` unchanged when it is a valid id, else ``new_id()``."""
    if header is not None and REQUEST_ID_PATTERN.fullmatch(header):
        return header
    return new_id()
