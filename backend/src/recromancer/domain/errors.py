"""Error taxonomy shared by every layer. Stdlib only."""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable error codes."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFIG_INVALID = "CONFIG_INVALID"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RecromancerError(Exception):
    """Base project exception carrying an `ErrorCode` and a short message."""

    MAX_MESSAGE_LENGTH = 500

    def __init__(self, code: ErrorCode, message: str) -> None:
        if len(message) > self.MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"message length {len(message)} exceeds maximum "
                f"of {self.MAX_MESSAGE_LENGTH} characters"
            )
        super().__init__(message)
        self.code = code
        self.message = message
