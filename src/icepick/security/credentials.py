"""Secure credential resolution provider for Icepick.

Implements the strict priority pyramid:
1. Debug / CI-only environment variables (DEBUG_ICEPICK_<KEY>)
2. Windows Credential Manager (Target: icepick:<key>)
(Generic environment variables like GEMINI_API_KEY or SNOWFLAKE_PASSWORD
are intentionally ignored to avoid accidental secret leak and ambient contamination.)
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from ctypes import wintypes

from icepick.exceptions import AuthenticationError

# Re-export AuthenticationError for convenience
__all__ = [
    "AuthenticationError",
    "decode_credential_blob",
    "format_actionable_error",
    "read_wcm_credential",
    "read_wcm_credential_fn",
    "resolve_credential",
]


# =============================================================================
# Win32 Credential Manager structures & bindings (Windows only)
# =============================================================================

class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)
_CRED_TYPE_GENERIC = 1


def decode_credential_blob(blob: bytes) -> str:
    """Decode raw CredentialBlob into a clean string, handling cmdkey UTF-16LE trap.

    When credentials are registered via Windows 'cmdkey /pass:<secret>', the OS
    stores them as UTF-16LE bytes with null characters (0x00) interleaved after
    each ASCII character. When registered via Windows GUI or standard Win32 APIs,
    they are stored as UTF-8 or ASCII byte arrays.

    This function automatically distinguishes between UTF-16LE and UTF-8/ASCII,
    strips trailing null terminators, and returns a clean secret string.

    Args:
        blob: Raw bytes array from CredentialBlob.

    Returns:
        Decoded credential string without trailing null bytes or surrounding whitespace.
    """
    if not blob:
        return ""

    # Strip trailing C null terminators
    trimmed = blob.rstrip(b"\x00")
    if not trimmed:
        return ""

    # If the blob is even-lengthed and contains null bytes within trimmed content,
    # it was written by cmdkey as UTF-16LE
    if len(blob) % 2 == 0 and b"\x00" in trimmed:
        try:
            decoded = blob.decode("utf-16le")
            return decoded.rstrip("\x00").strip()
        except UnicodeDecodeError:
            pass

    # Fallback to UTF-8 and latin-1
    try:
        return trimmed.decode("utf-8").strip()
    except UnicodeDecodeError:
        return trimmed.decode("latin-1", errors="replace").strip()


def _native_read_wcm(target: str) -> str | None:
    """Read a generic credential from Windows Credential Manager via advapi32.dll.

    Args:
        target: The target credential name (e.g., 'icepick:gemini_api_key').

    Returns:
        The decoded secret string if found, or None on failure / non-Windows.
    """
    if sys.platform != "win32":
        return None

    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        advapi32 = windll.advapi32
        CredReadW = advapi32.CredReadW
        CredFree = advapi32.CredFree

        CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_PCREDENTIALW),
        ]
        CredReadW.restype = wintypes.BOOL
        CredFree.argtypes = [ctypes.c_void_p]
        CredFree.restype = None

        cred_ptr = _PCREDENTIALW()
        success = CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if not success or not cred_ptr:
            return None

        try:
            cred = cred_ptr.contents
            blob_size = cred.CredentialBlobSize
            if blob_size > 0 and cred.CredentialBlob:
                raw_bytes = ctypes.string_at(cred.CredentialBlob, blob_size)
                return decode_credential_blob(raw_bytes)
            return None
        finally:
            CredFree(cred_ptr)
    except Exception:  # noqa: BLE001
        return None


# Pluggable hook for testing WCM without modifying native OS credentials
read_wcm_credential_fn: Callable[[str], str | None] = _native_read_wcm


def read_wcm_credential(target: str) -> str | None:
    """Read credential from Windows Credential Manager.

    Safe to call across all platforms. On non-Windows OS or if the target is
    not found, returns None without raising an exception.

    Args:
        target: The target credential name (e.g., 'icepick:gemini_api_key').

    Returns:
        The decoded secret string, or None if not found or unavailable.
    """
    return read_wcm_credential_fn(target)


def format_actionable_error(key_name: str, app_prefix: str = "icepick") -> str:
    """Generate actionable error instructions for missing credentials.

    Provides copy-pasteable commands for both secure PowerShell registration
    and debug environment variables.

    Args:
        key_name: Credential key identifier (e.g., 'gemini_api_key', 'snowflake_password').
        app_prefix: Application namespace prefix (default: 'icepick').

    Returns:
        Actionable error string following REQ-E-4 and design 3.8.
    """
    target = f"{app_prefix}:{key_name.lower()}"
    env_var = f"DEBUG_{app_prefix.upper()}_{key_name.upper()}"
    return (
        f"[Authentication Error] Credential for '{key_name}' is invalid or not provided.\n"
        f"To fix this, please register your credential using Windows Credential Manager:\n"
        f"  # Recommended (safe, masked input without leaving secrets in history):\n"
        f'  $cred = Get-Credential -UserName "any" -Message "Enter {key_name}"\n'
        f"  cmdkey /generic:{target} /user:any /pass:$($cred.GetNetworkCredential().Password)\n\n"
        f"  # Direct command:\n"
        f"  cmdkey /generic:{target} /user:any /pass:<your_key>\n\n"
        f"Or set the debug environment variable:\n"
        f'  $env:{env_var}="<your_key>"'
    )


def resolve_credential(
    key_name: str,
    app_prefix: str = "icepick",
) -> tuple[str, str]:
    """Resolve a credential following the strict priority pyramid.

    Priority Pyramid:
      1. Debug / CI-only environment variable: DEBUG_<APP_PREFIX>_<KEY_NAME>
      2. Windows Credential Manager: <app_prefix>:<key_name>
      (Generic ambient variables such as GEMINI_API_KEY or SNOWFLAKE_PASSWORD
       are intentionally ignored to avoid accidental secret leak and ambient contamination.)

    Args:
        key_name: Logical credential key name (e.g. 'gemini_api_key', 'snowflake_password').
        app_prefix: Application prefix for target namespace and env var (default: 'icepick').

    Returns:
        tuple[str, str]: (secret_value, source_description)

    Raises:
        AuthenticationError: If the credential cannot be resolved from any source.
    """
    # 1. Debug / CI-only environment variable
    debug_env_var = f"DEBUG_{app_prefix.upper()}_{key_name.upper()}"
    env_val = os.environ.get(debug_env_var)
    if env_val is not None and env_val.strip():
        return env_val.strip(), f"environment variable ({debug_env_var})"

    # 2. Windows Credential Manager
    target = f"{app_prefix}:{key_name.lower()}"
    wcm_val = read_wcm_credential(target)
    if wcm_val is not None and wcm_val.strip():
        return wcm_val.strip(), f"Windows Credential Manager ({target})"

    # 3. Actionable error
    error_msg = format_actionable_error(key_name=key_name, app_prefix=app_prefix)
    raise AuthenticationError(error_msg, key_name=key_name)
