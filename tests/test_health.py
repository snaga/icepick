"""Unit tests for ConnectionTester and connection health diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from icepick.config import Config
from icepick.credentials import (
    AuthenticationError,
    format_actionable_error,
    format_actionable_pair_error,
)
from icepick.health.tester import (
    ConnectionHealthReport,
    ConnectionTester,
    ServiceTestResult,
)


class TestConnectionTesterLLM:
    """Test suite for LLM connection testing in ConnectionTester."""

    def test_llm_check_gemini_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify successful Gemini connection ping records latency and metadata."""
        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.model = "gemini-3.8-flash"
        mock_client.generate_text.return_value = "pong response from gemini"

        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(return_value=mock_client),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="gemini", llm_model="gemini-3.8-flash")
        result = tester.test_llm(cfg, timeout=5.0)

        assert result.service == "llm"
        assert result.success is True
        assert result.duration_ms >= 0.0
        assert result.message == "Successfully connected to LLM provider."
        assert result.details["provider"] == "gemini"
        assert result.details["model"] == "gemini-3.8-flash"
        assert result.details["response_snippet"] == "pong response from gemini"
        assert result.actionable_advice is None

    def test_llm_check_vertex_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify successful Vertex AI connection records project and location details."""
        mock_client = MagicMock()
        mock_client.provider = "vertex"
        mock_client.model = "gemini-3.8-flash"
        mock_client.project = "test-project-123"
        mock_client.location = "us-central1"
        mock_client.generate_text.return_value = "pong response from vertex"

        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(return_value=mock_client),
        )

        tester = ConnectionTester()
        cfg = Config(
            llm_provider="vertex",
            llm_model="gemini-3.8-flash",
            gcp_project="test-project-123",
            gcp_location="us-central1",
        )
        result = tester.test_llm(cfg, timeout=5.0)

        assert result.service == "llm"
        assert result.success is True
        assert result.details["provider"] == "vertex"
        assert result.details["project"] == "test-project-123"
        assert result.details["location"] == "us-central1"
        assert result.actionable_advice is None

    def test_llm_check_auth_failure_actionable_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify missing Gemini API key yields actionable WCM advice."""
        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(side_effect=AuthenticationError("Key missing", key_name="gemini_api_key")),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="gemini")
        result = tester.test_llm(cfg)

        assert result.service == "llm"
        assert result.success is False
        assert result.actionable_advice is not None
        assert "cmdkey" in result.actionable_advice
        assert "icepick:gemini_api_key" in result.actionable_advice
        assert result.actionable_advice == format_actionable_error("gemini_api_key")

    def test_llm_check_vertex_adc_failure_actionable_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify missing Vertex ADC token yields actionable gcloud advice."""
        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(
                side_effect=ValueError(
                    "Failed to acquire Google Cloud ADC token for Vertex AI. "
                    "Please run 'gcloud auth application-default login' in your terminal."
                )
            ),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="vertex", gcp_project="my-proj")
        result = tester.test_llm(cfg)

        assert result.service == "llm"
        assert result.success is False
        assert result.actionable_advice is not None
        assert "gcloud auth application-default login" in result.actionable_advice

    def test_llm_check_vertex_missing_project_advice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify Vertex AI missing project ID returns guidance."""
        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(side_effect=ValueError("Google Cloud project ID is required for Vertex AI.")),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="vertex")
        result = tester.test_llm(cfg)

        assert result.service == "llm"
        assert result.success is False
        assert result.actionable_advice is not None
        assert "gcp_project" in result.actionable_advice

    def test_llm_check_timeout_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify request timeout produces clear advice."""
        import httpx

        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(side_effect=httpx.TimeoutException("timed out")),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="gemini")
        result = tester.test_llm(cfg, timeout=3.0)

        assert result.service == "llm"
        assert result.success is False
        assert "timed out after 3.0s" in result.message
        assert result.actionable_advice is not None
        assert "timed out" in result.actionable_advice

    def test_llm_check_provider_health_check_success_integration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify test_llm integrates provider health_check dictionary output on success."""
        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.model = "gemini-3.8-flash"
        mock_client.health_check.return_value = {
            "success": True,
            "duration_ms": 12.34,
            "message": "Successfully connected to Gemini (gemini-3.8-flash).",
            "details": {
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "response_snippet": "pong response",
            },
            "actionable_advice": None,
        }

        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(return_value=mock_client),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="gemini", llm_model="gemini-3.8-flash")
        result = tester.test_llm(cfg, timeout=5.0)

        assert result.service == "llm"
        assert result.success is True
        assert result.duration_ms == 12.34
        assert result.message == "Successfully connected to LLM provider."
        assert result.details["provider"] == "gemini"
        assert result.details["response_snippet"] == "pong response"
        assert result.actionable_advice is None

    def test_llm_check_provider_health_check_failure_integration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify test_llm integrates provider health_check dictionary output on failure."""
        mock_client = MagicMock()
        mock_client.provider = "vertex"
        mock_client.model = "gemini-2.5-flash"
        mock_client.project = "proj"
        mock_client.location = "us-central1"
        mock_client.health_check.return_value = {
            "success": False,
            "duration_ms": 45.67,
            "message": "Token acquisition failed",
            "details": {
                "provider": "vertex",
                "model": "gemini-2.5-flash",
                "project": "proj",
                "location": "us-central1",
            },
            "actionable_advice": "Please run 'gcloud auth application-default login'",
        }

        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(return_value=mock_client),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="vertex", llm_model="gemini-2.5-flash")
        result = tester.test_llm(cfg, timeout=5.0)

        assert result.service == "llm"
        assert result.success is False
        assert result.duration_ms == 45.67
        assert result.message == "Token acquisition failed"
        assert result.details["provider"] == "vertex"
        assert result.actionable_advice == "Please run 'gcloud auth application-default login'"


class TestConnectionTesterSnowflake:
    """Test suite for Snowflake connection testing in ConnectionTester."""

    def test_snowflake_check_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify successful Snowflake connection extracts metadata into details."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("8.12.0", "SYS_USER", "COMPUTE_WH")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_connector = MagicMock()
        mock_connector.connect.return_value = mock_conn

        monkeypatch.setattr(
            "icepick.health.tester.importlib.import_module",
            lambda name: mock_connector if name == "snowflake.connector" else None,
        )

        tester = ConnectionTester()
        cfg = Config(
            snowflake_account="test-account",
            snowflake_database="TEST_DB",
            snowflake_schema="PUBLIC",
            snowflake_warehouse="COMPUTE_WH",
            snowflake_user="SYS_USER",
            snowflake_password="secret_password",
        )

        result = tester.test_snowflake(cfg, timeout=8.0)

        assert result.service == "snowflake"
        assert result.success is True
        assert result.duration_ms >= 0.0
        assert result.message == "Successfully connected to Snowflake."
        assert result.details["version"] == "8.12.0"
        assert result.details["user"] == "SYS_USER"
        assert result.details["warehouse"] == "COMPUTE_WH"
        assert result.details["account"] == "test-account"
        assert result.details["database"] == "TEST_DB"
        assert result.actionable_advice is None
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_snowflake_check_missing_credentials_actionable_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify missing credentials triggers WCM pair registration guidance."""
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
            "icepick.health.tester.resolve_credential_pair",
            MagicMock(side_effect=AuthenticationError("Not found", key_name="snowflake")),
        )

        tester = ConnectionTester()
        cfg = Config(
            snowflake_account="my-acct",
            snowflake_database="my-db",
            snowflake_warehouse="my-wh",
            snowflake_user=None,
            snowflake_password=None,
        )

        result = tester.test_snowflake(cfg)

        assert result.service == "snowflake"
        assert result.success is False
        assert "user, password" in result.message
        assert result.actionable_advice is not None
        assert "icepick:snowflake" in result.actionable_advice
        assert "cmdkey" in result.actionable_advice
        assert "Get-Credential" in result.actionable_advice

    def test_snowflake_check_missing_infra_parameters_advice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify missing infrastructure configuration prompts for config or env vars."""
        tester = ConnectionTester()
        cfg = Config(
            snowflake_account="",
            snowflake_database="",
            snowflake_warehouse="",
            snowflake_user="myuser",
            snowflake_password="mypassword",
        )

        result = tester.test_snowflake(cfg)

        assert result.service == "snowflake"
        assert result.success is False
        assert "Missing required Snowflake parameter(s)" in result.message
        assert result.actionable_advice is not None
        assert ".icepick.toml" in result.actionable_advice

    def test_snowflake_check_missing_driver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify missing snowflake-connector module prompts installation instructions."""

        def fake_import(name: str) -> Any:
            if name == "snowflake.connector":
                raise ImportError("No module named 'snowflake'")
            return None

        monkeypatch.setattr("icepick.health.tester.importlib.import_module", fake_import)

        tester = ConnectionTester()
        cfg = Config(
            snowflake_account="my-acct",
            snowflake_database="my-db",
            snowflake_warehouse="my-wh",
            snowflake_user="myuser",
            snowflake_password="mypassword",
        )

        result = tester.test_snowflake(cfg)

        assert result.service == "snowflake"
        assert result.success is False
        assert "snowflake-connector-python is not installed" in result.message
        assert result.actionable_advice is not None
        assert "pip install snowflake-connector-python" in result.actionable_advice

    def test_snowflake_check_connection_auth_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify database authentication error 250001 triggers WCM pair advice."""
        mock_connector = MagicMock()
        mock_connector.connect.side_effect = Exception("250001: Incorrect username or password.")

        monkeypatch.setattr(
            "icepick.health.tester.importlib.import_module",
            lambda name: mock_connector if name == "snowflake.connector" else None,
        )

        tester = ConnectionTester()
        cfg = Config(
            snowflake_account="my-acct",
            snowflake_database="my-db",
            snowflake_warehouse="my-wh",
            snowflake_user="myuser",
            snowflake_password="wrong_password",
        )

        result = tester.test_snowflake(cfg)

        assert result.service == "snowflake"
        assert result.success is False
        assert "Incorrect username or password" in result.message
        assert result.actionable_advice == format_actionable_pair_error("snowflake")


class TestConnectionHealthReportAndAll:
    """Test suite for ConnectionHealthReport aggregation and ConnectionTester.test_all."""

    def test_health_report_all_passed_and_to_dict(self) -> None:
        """Verify all_passed logic and serialization for ConnectionHealthReport."""
        r1 = ServiceTestResult(
            service="llm",
            success=True,
            duration_ms=42.5,
            message="LLM ok",
            details={"model": "gemini-3.8-flash"},
        )
        r2 = ServiceTestResult(
            service="snowflake",
            success=True,
            duration_ms=120.0,
            message="Snowflake ok",
            details={"version": "8.10"},
        )

        report = ConnectionHealthReport(results={"llm": r1, "snowflake": r2})
        assert report.all_passed is True

        data = report.to_dict()
        assert data["all_passed"] is True
        assert "llm" in data["results"]
        assert data["results"]["llm"]["success"] is True
        assert data["results"]["snowflake"]["duration_ms"] == 120.0

        # Partial failure
        r2_fail = ServiceTestResult(
            service="snowflake",
            success=False,
            duration_ms=10.0,
            message="Failed",
            actionable_advice="Fix config",
        )
        report_fail = ConnectionHealthReport(results={"llm": r1, "snowflake": r2_fail})
        assert report_fail.all_passed is False
        assert report_fail.to_dict()["all_passed"] is False

        # Empty report
        empty_report = ConnectionHealthReport()
        assert empty_report.all_passed is False

    def test_test_all_runs_specified_targets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify test_all executes only specified targets and aggregates them."""
        tester = ConnectionTester()
        cfg = Config()

        res_llm = ServiceTestResult(service="llm", success=True, duration_ms=10.0, message="ok")
        res_sf = ServiceTestResult(
            service="snowflake", success=True, duration_ms=20.0, message="ok"
        )

        monkeypatch.setattr(tester, "test_llm", MagicMock(return_value=res_llm))
        monkeypatch.setattr(tester, "test_snowflake", MagicMock(return_value=res_sf))

        # Default runs both
        report = tester.test_all(cfg)
        assert len(report.results) == 2
        assert report.all_passed is True

        # LLM only
        report_llm = tester.test_all(cfg, targets=["llm"])
        assert list(report_llm.results.keys()) == ["llm"]

        # Invalid target raises ValueError
        with pytest.raises(ValueError, match="Unsupported health test target"):
            tester.test_all(cfg, targets=["postgres"])
