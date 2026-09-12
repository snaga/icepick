"""Security module for Icepick.

Provides secure credential resolution using Windows Credential Manager (WCM)
and debug/CI environment overrides with strict precedence and actionable errors.
"""

from __future__ import annotations

from icepick.exceptions import AuthenticationError
from icepick.security.credentials import (
    decode_credential_blob,
    format_actionable_error,
    read_wcm_credential,
    resolve_credential,
)

__all__ = [
    "AuthenticationError",
    "decode_credential_blob",
    "format_actionable_error",
    "read_wcm_credential",
    "resolve_credential",
]
