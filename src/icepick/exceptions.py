"""Exception hierarchy for Icepick.

Defines the base IcepickError and domain-specific exceptions such as ParseError.
"""

from __future__ import annotations


class IcepickError(Exception):
    """Base exception for all Icepick errors."""


class ParseError(IcepickError):
    """Exception raised when SQL parsing fails.

    Attributes:
        message: Human-readable error description.
        line: 1-indexed line number where the error occurred, if available.
        col: 1-indexed column number where the error occurred, if available.
        token: Highlighted offending token, if available.
        context: Code snippet context around the error, if available.
        raw_error: Original underlying sqlglot exception, if any.
    """

    def __init__(
        self,
        message: str,
        line: int | None = None,
        col: int | None = None,
        token: str | None = None,
        context: str | None = None,
        raw_error: Exception | None = None,
    ) -> None:
        self.message = message
        self.line = line
        self.col = col
        self.token = token
        self.context = context
        self.raw_error = raw_error
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Construct a detailed human-readable error string."""
        parts: list[str] = [self.message]
        location_info: list[str] = []
        if self.line is not None:
            location_info.append(f"Line {self.line}")
        if self.col is not None:
            location_info.append(f"Col {self.col}")
        if location_info:
            parts.append(f"({', '.join(location_info)})")
        if self.token:
            parts.append(f"near token '{self.token}'")
        if self.context:
            parts.append(f"\nContext:\n  {self.context}")
        return " ".join(parts)


class AuthenticationError(IcepickError):
    """Exception raised when credential resolution fails.

    Attributes:
        message: Actionable error message describing recovery steps.
        key_name: The credential key that failed to resolve, if specified.
    """

    def __init__(self, message: str, key_name: str | None = None) -> None:
        self.message = message
        self.key_name = key_name
        super().__init__(message)
