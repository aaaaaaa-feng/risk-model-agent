"""Errors that are safe for the API layer to expose to callers."""

from typing import Any, Dict, Optional


class DomainError(Exception):
    """A controlled domain failure with an HTTP-compatible status code.

    ``message`` is deliberately separate from the exception's implementation
    detail so API handlers do not need to expose arbitrary tracebacks.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
