"""Credentials module alias for backward compatibility and convenience.

Re-exports core credential resolution routines from icepick.security.credentials.
"""

from __future__ import annotations

from icepick.security.credentials import (
    AuthenticationError,
    decode_credential_blob,
    format_actionable_error,
    format_actionable_pair_error,
    read_wcm_credential,
    read_wcm_credential_fn,
    read_wcm_credential_pair,
    read_wcm_credential_pair_fn,
    resolve_credential,
    resolve_credential_pair,
)

__all__ = [
    "AuthenticationError",
    "decode_credential_blob",
    "format_actionable_error",
    "format_actionable_pair_error",
    "read_wcm_credential",
    "read_wcm_credential_fn",
    "read_wcm_credential_pair",
    "read_wcm_credential_pair_fn",
    "resolve_credential",
    "resolve_credential_pair",
]

