"""Tests for secure credential resolution and WCM decoder."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from icepick.exceptions import AuthenticationError
from icepick.security.credentials import (
    _native_read_wcm,
    _native_read_wcm_pair,
    decode_credential_blob,
    format_actionable_error,
    format_actionable_pair_error,
    read_wcm_credential,
    read_wcm_credential_pair,
    resolve_credential,
    resolve_credential_pair,
)


class TestDecodeCredentialBlob:
    """Unit tests for decode_credential_blob function."""

    def test_empty_blob(self) -> None:
        """Verify that empty or null-only blobs return empty string."""
        assert decode_credential_blob(b"") == ""
        assert decode_credential_blob(b"\x00") == ""
        assert decode_credential_blob(b"\x00\x00") == ""

    def test_utf8_ascii_blob(self) -> None:
        """Verify that standard UTF-8 / ASCII strings are correctly decoded."""
        secret = "my_simple_secret_123"
        assert decode_credential_blob(secret.encode("utf-8")) == secret

    def test_utf8_with_trailing_null(self) -> None:
        """Verify that UTF-8 string with trailing null byte (C string) is trimmed."""
        secret = "secret_with_null"
        blob = secret.encode("utf-8") + b"\x00"
        assert decode_credential_blob(blob) == secret

    def test_cmdkey_utf16le_with_null_bytes(self) -> None:
        """Verify that cmdkey UTF-16LE encoded blob (interleaved null bytes) is cleanly decoded."""
        secret = "ghp_cmdkey_token_ABC123"
        # cmdkey stores ASCII characters encoded as UTF-16LE
        blob = secret.encode("utf-16le")
        # Check that null bytes exist in the blob
        assert b"\x00" in blob
        assert decode_credential_blob(blob) == secret

    def test_cmdkey_utf16le_with_trailing_nulls(self) -> None:
        """Verify that UTF-16LE blob with extra trailing null bytes is cleanly decoded."""
        secret = "snowflake_password_secure"
        blob = secret.encode("utf-16le") + b"\x00\x00"
        assert decode_credential_blob(blob) == secret

    def test_whitespace_trimmed(self) -> None:
        """Verify that leading and trailing whitespace is stripped."""
        assert decode_credential_blob(b"   token_with_spaces   ") == "token_with_spaces"

    def test_invalid_utf8_binary_falls_back_to_latin1(self) -> None:
        """Verify that invalid UTF-8/UTF-16 binary blobs safely fall back to latin-1 without crashing."""
        # Odd-length blob with high bytes (invalid UTF-8 sequence)
        invalid_blob = b"\xff\xfe\xfd"
        result = decode_credential_blob(invalid_blob)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_corrupted_utf16_blob_falls_back_to_latin1(self) -> None:
        """Verify that even-lengthed blob containing nulls but invalid UTF-16LE falls back to latin-1."""
        # Even length with null bytes but invalid/unpaired surrogate UTF-16 sequence
        corrupted_utf16 = b"\x00\xd8\x00\x00"
        result = decode_credential_blob(corrupted_utf16)
        assert isinstance(result, str)


class TestReadWcmCredential:
    """Tests for read_wcm_credential wrapper and mock boundary."""

    def test_read_wcm_credential_with_custom_fn(self) -> None:
        """Verify that read_wcm_credential invokes read_wcm_credential_fn hook."""
        with patch(
            "icepick.security.credentials.read_wcm_credential_fn", return_value="custom_secret"
        ) as mock_fn:
            result = read_wcm_credential("icepick:gemini_api_key")
            assert result == "custom_secret"
            mock_fn.assert_called_once_with("icepick:gemini_api_key")

    def test_native_read_wcm_on_non_windows(self) -> None:
        """Verify that _native_read_wcm safely returns None on non-Windows platforms."""
        with patch("sys.platform", "linux"):
            result = _native_read_wcm("icepick:gemini_api_key")
            assert result is None

    def test_native_read_wcm_handles_exception_gracefully(self) -> None:
        """Verify that _native_read_wcm safely returns None when Win32 API raises an exception."""
        with patch("sys.platform", "win32"), patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.advapi32.CredReadW.side_effect = RuntimeError("API call failed")
            result = _native_read_wcm("icepick:gemini_api_key")
            assert result is None


class TestResolveCredential:
    """Tests for strict priority pyramid in resolve_credential."""

    def test_debug_env_var_highest_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that DEBUG_ICEPICK_<KEY> takes precedence over Windows Credential Manager."""
        monkeypatch.setenv("DEBUG_ICEPICK_GEMINI_API_KEY", "env_override_key")

        with patch("icepick.security.credentials.read_wcm_credential", return_value="wcm_key"):
            val, source = resolve_credential("gemini_api_key")
            assert val == "env_override_key"
            assert "environment variable (DEBUG_ICEPICK_GEMINI_API_KEY)" in source

    def test_generic_ambient_env_var_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that generic ambient environment variables (GEMINI_API_KEY) are strictly ignored."""
        # Set generic environment variable which must NOT be used
        monkeypatch.setenv("GEMINI_API_KEY", "ambient_insecure_key")
        monkeypatch.delenv("DEBUG_ICEPICK_GEMINI_API_KEY", raising=False)

        # 1. When WCM has the key, it should resolve from WCM, ignoring GEMINI_API_KEY
        with patch("icepick.security.credentials.read_wcm_credential", return_value="wcm_key"):
            val, source = resolve_credential("gemini_api_key")
            assert val == "wcm_key"
            assert "Windows Credential Manager" in source
            assert val != "ambient_insecure_key"

        # 2. When WCM does NOT have the key, it should raise AuthenticationError, ignoring GEMINI_API_KEY
        with (
            patch("icepick.security.credentials.read_wcm_credential", return_value=None),
            pytest.raises(AuthenticationError),
        ):
            resolve_credential("gemini_api_key")

    def test_wcm_fallback_when_debug_env_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that WCM is queried when debug environment variable is absent."""
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)

        with patch(
            "icepick.security.credentials.read_wcm_credential", return_value="snowflake_pass"
        ) as mock_wcm:
            val, source = resolve_credential("snowflake_password")
            assert val == "snowflake_pass"
            assert "Windows Credential Manager (icepick:snowflake_password)" in source
            mock_wcm.assert_called_once_with("icepick:snowflake_password")

    def test_custom_app_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify custom app_prefix creates appropriate env var and target names."""
        monkeypatch.setenv("DEBUG_MYAPP_API_TOKEN", "myapp_token")

        val, source = resolve_credential("api_token", app_prefix="myapp")
        assert val == "myapp_token"
        assert "DEBUG_MYAPP_API_TOKEN" in source

    def test_unresolved_credential_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify AuthenticationError with actionable recovery commands when credential is missing."""
        monkeypatch.delenv("DEBUG_ICEPICK_GEMINI_API_KEY", raising=False)

        with patch("icepick.security.credentials.read_wcm_credential", return_value=None):
            with pytest.raises(AuthenticationError) as exc_info:
                resolve_credential("gemini_api_key")

            error_msg = str(exc_info.value)
            assert "[Authentication Error]" in error_msg
            assert "gemini_api_key" in error_msg

            # Verify PowerShell safe Get-Credential template
            assert 'Get-Credential -UserName "any"' in error_msg
            assert (
                "cmdkey /generic:icepick:gemini_api_key /user:any /pass:$($cred.GetNetworkCredential().Password)"
                in error_msg
            )

            # Verify direct cmdkey command template
            assert "cmdkey /generic:icepick:gemini_api_key /user:any /pass:<your_key>" in error_msg

            # Verify debug environment variable template
            assert '$env:DEBUG_ICEPICK_GEMINI_API_KEY="<your_key>"' in error_msg


class TestActionableErrorMessage:
    """Unit tests for format_actionable_error string formatting."""

    def test_format_actionable_error_contents(self) -> None:
        """Verify format_actionable_error contains all expected guidance sections."""
        msg = format_actionable_error("snowflake_password", app_prefix="icepick")
        assert (
            "[Authentication Error] Credential for 'snowflake_password' is invalid or not provided."
            in msg
        )
        assert "icepick:snowflake_password" in msg
        assert "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD" in msg
        assert "Get-Credential" in msg
        assert "cmdkey" in msg


class TestReadWcmCredentialPair:
    """Tests for read_wcm_credential_pair wrapper and mock boundary."""

    def test_read_wcm_credential_pair_mock_hook(self) -> None:
        """Verify that read_wcm_credential_pair invokes read_wcm_credential_pair_fn hook."""
        with patch(
            "icepick.security.credentials.read_wcm_credential_pair_fn",
            return_value=("snowflake_user", "snowflake_pass"),
        ) as mock_fn:
            result = read_wcm_credential_pair("icepick:snowflake")
            assert result == ("snowflake_user", "snowflake_pass")
            mock_fn.assert_called_once_with("icepick:snowflake")

    def test_native_read_wcm_pair_on_non_windows(self) -> None:
        """Verify that _native_read_wcm_pair safely returns None on non-Windows platforms."""
        with patch("sys.platform", "linux"):
            result = _native_read_wcm_pair("icepick:snowflake")
            assert result is None

    def test_native_read_wcm_pair_handles_exception_gracefully(self) -> None:
        """Verify that _native_read_wcm_pair safely returns None when Win32 API raises an exception."""
        with patch("sys.platform", "win32"), patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.advapi32.CredReadW.side_effect = RuntimeError("API call failed")
            result = _native_read_wcm_pair("icepick:snowflake")
            assert result is None


class TestResolveCredentialPair:
    """Tests for strict priority pyramid in resolve_credential_pair."""

    def test_resolve_credential_pair_debug_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that DEBUG_ICEPICK_SNOWFLAKE_USER and DEBUG_ICEPICK_SNOWFLAKE_PASSWORD are used."""
        monkeypatch.setenv("DEBUG_ICEPICK_SNOWFLAKE_USER", "debug_user")
        monkeypatch.setenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", "debug_password")

        with patch(
            "icepick.security.credentials.read_wcm_credential_pair",
            return_value=("wcm_user", "wcm_pass"),
        ):
            user, password, source = resolve_credential_pair("snowflake")
            assert user == "debug_user"
            assert password == "debug_password"
            assert "environment variables (DEBUG_ICEPICK_SNOWFLAKE_*)" in source

    def test_resolve_credential_pair_wcm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that WCM is queried when debug environment variables are absent."""
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)

        with patch(
            "icepick.security.credentials.read_wcm_credential_pair",
            return_value=("wcm_user", "wcm_secret"),
        ) as mock_wcm:
            user, password, source = resolve_credential_pair("snowflake")
            assert user == "wcm_user"
            assert password == "wcm_secret"
            assert "Windows Credential Manager (icepick:snowflake)" in source
            mock_wcm.assert_called_once_with("icepick:snowflake")

    def test_resolve_credential_pair_missing_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify AuthenticationError with actionable recovery commands when credential pair is missing."""
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)

        with patch("icepick.security.credentials.read_wcm_credential_pair", return_value=None):
            with pytest.raises(AuthenticationError) as exc_info:
                resolve_credential_pair("snowflake")

            error_msg = str(exc_info.value)
            assert "[Authentication Error]" in error_msg
            assert "snowflake" in error_msg
            assert "Get-Credential" in error_msg
            assert "cmdkey" in error_msg
            assert "icepick:snowflake" in error_msg
            assert "DEBUG_ICEPICK_SNOWFLAKE_USER" in error_msg
            assert "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD" in error_msg
            assert (
                "cmdkey /generic:icepick:snowflake /user:$($cred.UserName) /pass:$($cred.GetNetworkCredential().Password)"
                in error_msg
            )

    def test_resolve_credential_pair_partial_env_falls_back_to_wcm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that if only one debug env var is set, it falls back to WCM."""
        monkeypatch.setenv("DEBUG_ICEPICK_SNOWFLAKE_USER", "debug_user")
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)

        with patch(
            "icepick.security.credentials.read_wcm_credential_pair",
            return_value=("wcm_user", "wcm_secret"),
        ):
            user, password, source = resolve_credential_pair("snowflake")
            assert user == "wcm_user"
            assert password == "wcm_secret"
            assert "Windows Credential Manager (icepick:snowflake)" in source

    def test_resolve_credential_pair_ambient_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that generic ambient environment variables (SNOWFLAKE_USER / SNOWFLAKE_PASSWORD) are ignored."""
        monkeypatch.setenv("SNOWFLAKE_USER", "ambient_user")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "ambient_pass")
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_USER", raising=False)
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)

        with (
            patch("icepick.security.credentials.read_wcm_credential_pair", return_value=None),
            pytest.raises(AuthenticationError),
        ):
            resolve_credential_pair("snowflake")


class TestActionablePairErrorMessage:
    """Unit tests for format_actionable_pair_error string formatting."""

    def test_format_actionable_pair_error_contents(self) -> None:
        """Verify format_actionable_pair_error contains all expected guidance sections."""
        msg = format_actionable_pair_error("snowflake", app_prefix="icepick")
        assert "[Authentication Error] Credential pair for 'snowflake' (username and password)" in msg
        assert "icepick:snowflake" in msg
        assert "DEBUG_ICEPICK_SNOWFLAKE_USER" in msg
        assert "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD" in msg
        assert "Get-Credential" in msg
        assert "cmdkey" in msg
        assert (
            "cmdkey /generic:icepick:snowflake /user:$($cred.UserName) /pass:$($cred.GetNetworkCredential().Password)"
            in msg
        )


def test_icepick_credentials_module_reexport() -> None:
    """Verify that icepick.credentials re-exports all new pair functions."""
    import icepick.credentials as creds

    assert hasattr(creds, "read_wcm_credential_pair")
    assert hasattr(creds, "read_wcm_credential_pair_fn")
    assert hasattr(creds, "resolve_credential_pair")
    assert hasattr(creds, "format_actionable_pair_error")

