"""Unit and integration tests for EquivalenceVerifier credential resolution and actionable guidance."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icepick.config import Config
from icepick.credentials import format_actionable_pair_error
from icepick.exceptions import AuthenticationError
from icepick.verifier.equivalence import EquivalenceVerifier, VerificationResult


class TestEquivalenceVerifierCredentialHandling:
    """Test suite for credential resolution and actionable advice in EquivalenceVerifier."""

    def test_verify_with_snowflake_missing_credentials_contains_actionable_pair_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that missing both user and password returns VerificationResult with pair guidance."""
        for key in [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_PASSWORD",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_WAREHOUSE",
            "DEBUG_ICEPICK_SNOWFLAKE_USER",
            "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD",
        ]:
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr(
            "icepick.verifier.equivalence.resolve_credential_pair",
            MagicMock(side_effect=AuthenticationError("Not found", key_name="snowflake")),
        )

        verifier = EquivalenceVerifier()
        result: VerificationResult = verifier.verify_with_snowflake(
            orig_sql="SELECT 1",
            opt_sql="SELECT 1",
            config=Config(
                snowflake_account="test-acct",
                snowflake_database="test-db",
                snowflake_warehouse="test-wh",
            ),
        )

        assert result.is_equivalent is False
        assert result.orig_not_in_opt_count == -1
        assert result.opt_not_in_orig_count == -1
        assert result.error_message is not None

        expected_guidance = format_actionable_pair_error("snowflake")
        assert expected_guidance in result.error_message
        assert "icepick:snowflake" in result.error_message
        assert "cmdkey" in result.error_message
        assert "Get-Credential" in result.error_message

    def test_verify_with_snowflake_missing_user_only_returns_pair_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that when only username is missing, pair guidance is presented."""
        for key in ["DEBUG_ICEPICK_SNOWFLAKE_USER", "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD"]:
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr(
            "icepick.verifier.equivalence.resolve_credential_pair",
            MagicMock(side_effect=AuthenticationError("Not found", key_name="snowflake")),
        )

        verifier = EquivalenceVerifier()
        result = verifier.verify_with_snowflake(
            orig_sql="SELECT 1",
            opt_sql="SELECT 1",
            config=Config(
                snowflake_account="test-acct",
                snowflake_password="test-password",
                snowflake_database="test-db",
                snowflake_warehouse="test-wh",
            ),
        )

        assert result.is_equivalent is False
        assert result.error_message is not None
        assert "icepick:snowflake" in result.error_message
        assert "cmdkey" in result.error_message

    def test_verify_with_snowflake_missing_password_only_returns_pair_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that when only password is missing, pair guidance is presented."""
        for key in ["DEBUG_ICEPICK_SNOWFLAKE_USER", "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD"]:
            monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr(
            "icepick.verifier.equivalence.resolve_credential_pair",
            MagicMock(side_effect=AuthenticationError("Not found", key_name="snowflake")),
        )

        verifier = EquivalenceVerifier()
        result = verifier.verify_with_snowflake(
            orig_sql="SELECT 1",
            opt_sql="SELECT 1",
            config=Config(
                snowflake_account="test-acct",
                snowflake_user="test-user",
                snowflake_database="test-db",
                snowflake_warehouse="test-wh",
            ),
        )

        assert result.is_equivalent is False
        assert result.error_message is not None
        assert "icepick:snowflake" in result.error_message
        assert "Get-Credential" in result.error_message

    def test_verify_with_snowflake_resolves_fallback_from_credential_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that resolve_credential_pair successfully populates missing user and password."""
        mock_resolve = MagicMock(return_value=("resolved_user", "resolved_pass", "mock_source"))
        monkeypatch.setattr(
            "icepick.verifier.equivalence.resolve_credential_pair",
            mock_resolve,
        )

        mock_connector = MagicMock()
        mock_conn = MagicMock()
        mock_connector.connect.return_value = mock_conn

        monkeypatch.setattr("importlib.import_module", MagicMock(return_value=mock_connector))

        verifier = EquivalenceVerifier()
        # Mock verify method so it doesn't execute DB query
        mock_verify_result = VerificationResult(
            is_equivalent=True,
            orig_not_in_opt_count=0,
            opt_not_in_orig_count=0,
            verification_sql="-- verification",
        )
        monkeypatch.setattr(verifier, "verify", MagicMock(return_value=mock_verify_result))

        result = verifier.verify_with_snowflake(
            orig_sql="SELECT 1",
            opt_sql="SELECT 1",
            config=Config(
                snowflake_account="test-acct",
                snowflake_database="test-db",
                snowflake_warehouse="test-wh",
            ),
        )

        assert result.is_equivalent is True
        mock_resolve.assert_called_once_with("snowflake")
        mock_connector.connect.assert_called_once()
        call_kwargs = mock_connector.connect.call_args[1]
        assert call_kwargs["user"] == "resolved_user"
        assert call_kwargs["password"] == "resolved_pass"
