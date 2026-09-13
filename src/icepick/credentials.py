"""Credentials module alias for backward compatibility and convenience.

Re-exports core credential resolution routines from icepick.security.credentials.
"""

from __future__ import annotations

from icepick.security.credentials import (
    AuthenticationError,
    decode_credential_blob,
    format_actionable_error,
    read_wcm_credential,
    read_wcm_credential_fn,
    resolve_credential,
)

__all__ = [
    "AuthenticationError",
    "decode_credential_blob",
    "format_actionable_error",
    "read_wcm_credential",
    "read_wcm_credential_fn",
    "resolve_credential",
]
