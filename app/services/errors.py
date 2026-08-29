from __future__ import annotations


class DomainError(Exception):
    """A business-rule violation that maps to a 4xx response."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
