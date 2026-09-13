"""Unit tests for ConnectionTester and connection health diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icepick.config import Config
from icepick.credentials import (
    AuthenticationError,
    format_actionable_provider_guidance,
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
        assert result.actionable_advice == format_actionable_provider_guidance("gemini")

    def test_llm_check_gemini_auth_failure_shows_vertex_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify Gemini auth failure provides actionable guidance with Vertex AI options."""
        monkeypatch.setattr(
            "icepick.health.tester.LLMClient",
            MagicMock(
                side_effect=AuthenticationError("API key missing", key_name="gemini_api_key")
            ),
        )

        tester = ConnectionTester()
        cfg = Config(llm_provider="gemini")
        result = tester.test_llm(cfg)

        assert result.service == "llm"
        assert result.success is False
        assert result.actionable_advice is not None
        assert "--provider vertex" in result.actionable_advice
        assert "ICEPICK_LLM_PROVIDER" in result.actionable_advice
        assert "cmdkey" in result.actionable_advice
        assert "icepick:gemini_api_key" in result.actionable_advice

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

        report = ConnectionHealthReport(results={"llm": r1})
        assert report.all_passed is True

        data = report.to_dict()
        assert data["all_passed"] is True
        assert "llm" in data["results"]
        assert data["results"]["llm"]["success"] is True
        assert data["results"]["llm"]["duration_ms"] == 42.5

        # Failure case
        r1_fail = ServiceTestResult(
            service="llm",
            success=False,
            duration_ms=10.0,
            message="Failed",
            actionable_advice="Fix config",
        )
        report_fail = ConnectionHealthReport(results={"llm": r1_fail})
        assert report_fail.all_passed is False
        assert report_fail.to_dict()["all_passed"] is False

        # Empty report
        empty_report = ConnectionHealthReport()
        assert empty_report.all_passed is False

    def test_test_all_runs_specified_targets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify test_all executes LLM target by default and rejects invalid targets."""
        tester = ConnectionTester()
        cfg = Config()

        res_llm = ServiceTestResult(service="llm", success=True, duration_ms=10.0, message="ok")
        monkeypatch.setattr(tester, "test_llm", MagicMock(return_value=res_llm))

        # Default runs ("llm",)
        report = tester.test_all(cfg)
        assert len(report.results) == 1
        assert "llm" in report.results
        assert report.all_passed is True

        # Explicit LLM target
        report_llm = tester.test_all(cfg, targets=["llm"])
        assert list(report_llm.results.keys()) == ["llm"]

        # Snowflake or other targets now raise ValueError
        with pytest.raises(ValueError, match="Unsupported health test target"):
            tester.test_all(cfg, targets=["snowflake"])

        with pytest.raises(ValueError, match="Unsupported health test target"):
            tester.test_all(cfg, targets=["postgres"])
