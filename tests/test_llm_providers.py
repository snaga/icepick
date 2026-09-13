"""Unit tests for pluggable LLM providers and provider registry."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from icepick.exceptions import AuthenticationError
from icepick.llm.providers import (
    PROVIDER_REGISTRY,
    BaseLLMProvider,
    GeminiProvider,
    VertexAIProvider,
    create_provider,
    get_provider_class,
    register_provider,
)


class DummyCustomProvider(BaseLLMProvider):
    """Custom provider for testing registration mechanism."""

    @property
    def name(self) -> str:
        return "dummy_custom"

    def generate_text(self, prompt: str) -> str:
        return f"custom echo: {prompt}"

    def health_check(self) -> dict[str, Any]:
        return {
            "success": True,
            "duration_ms": 1.23,
            "message": "Dummy health check passed.",
            "details": {"provider": self.name},
            "actionable_advice": None,
        }


class TestBaseLLMProvider:
    """Tests for BaseLLMProvider functionality and helper methods."""

    def test_options_copied(self) -> None:
        """Verify that provider copies options dict to prevent external mutations."""
        opts = {"foo": "bar"}
        provider = DummyCustomProvider(model="custom-m", options=opts)
        opts["foo"] = "mutated"
        assert provider.options["foo"] == "bar"

    def test_get_client_lifecycle(self) -> None:
        """Verify _get_client returns injected client or creates new client with close flag."""
        # 1. With injected client
        mock_client = MagicMock(spec=httpx.Client)
        provider1 = DummyCustomProvider(model="m", http_client=mock_client)
        client, should_close = provider1._get_client()
        assert client is mock_client
        assert should_close is False

        # 2. Without injected client
        provider2 = DummyCustomProvider(model="m", timeout=15.0)
        client2, should_close2 = provider2._get_client()
        assert isinstance(client2, httpx.Client)
        assert client2.timeout.read == 15.0
        assert should_close2 is True
        client2.close()


class TestGeminiProvider:
    """Tests for GeminiProvider REST integration and credential resolution."""

    def test_gemini_provider_with_options(self) -> None:
        """Test text generation with api_key explicitly passed in options."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            payload = json.loads(request.content)
            assert payload["contents"][0]["parts"][0]["text"] == "SELECT 1"
            return httpx.Response(
                status_code=200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "SELECT 1 AS optimized"}]}}
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        provider = GeminiProvider(
            model="gemini-2.5-flash",
            options={"api_key": "custom-gemini-key"},
            http_client=client,
        )

        assert provider.name == "gemini"
        assert provider.api_key == "custom-gemini-key"

        result = provider.generate_text("SELECT 1")
        assert result == "SELECT 1 AS optimized"

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        assert req.headers["x-goog-api-key"] == "custom-gemini-key"
        assert req.headers["content-type"] == "application/json"

    def test_gemini_provider_wcm_fallback(self) -> None:
        """Test resolving API key from credential resolution when not in options."""
        with patch("icepick.llm.providers.gemini.resolve_credential") as mock_resolve:
            mock_resolve.return_value = ("wcm-resolved-key", "Windows Credential Manager")

            provider = GeminiProvider(model="gemini-2.5-flash")
            assert provider.api_key == "wcm-resolved-key"
            mock_resolve.assert_called_once_with("gemini_api_key")

    def test_gemini_provider_missing_credentials_raises_authentication_error(self) -> None:
        """Test that AuthenticationError is raised when key is missing and cannot be resolved."""
        with patch("icepick.llm.providers.gemini.resolve_credential") as mock_resolve:
            mock_resolve.side_effect = AuthenticationError(
                "Missing key", key_name="gemini_api_key"
            )

            provider = GeminiProvider(model="gemini-2.5-flash")
            assert provider.api_key is None

            with pytest.raises(AuthenticationError, match="Missing key"):
                provider.generate_text("ping")

    def test_gemini_provider_health_check_success_and_failure(self) -> None:
        """Test health check round-trip reporting on success and credential failure."""
        # 1. Health check success
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={"candidates": [{"content": {"parts": [{"text": "pong"}]}}]},
            )

        transport = httpx.MockTransport(handler)
        mock_client = httpx.Client(transport=transport)

        provider = GeminiProvider(
            model="gemini-2.5-flash",
            options={"api_key": "valid-key"},
            http_client=mock_client,
        )

        status = provider.health_check()
        assert status["success"] is True
        assert status["duration_ms"] >= 0.0
        assert "Successfully connected" in status["message"]
        assert status["details"]["provider"] == "gemini"
        assert status["details"]["model"] == "gemini-2.5-flash"
        assert status["details"]["response_snippet"] == "pong"
        assert status["actionable_advice"] is None

        # 2. Health check failure due to missing credentials
        with patch("icepick.llm.providers.gemini.resolve_credential") as mock_resolve:
            mock_resolve.side_effect = AuthenticationError(
                "Missing key", key_name="gemini_api_key"
            )
            provider_fail = GeminiProvider(model="gemini-2.5-flash")
            fail_status = provider_fail.health_check()

            assert fail_status["success"] is False
            assert "Missing key" in fail_status["message"]
            assert fail_status["actionable_advice"] is not None
            assert "cmdkey /generic:icepick:gemini_api_key" in fail_status["actionable_advice"]

        # 3. Health check failure due to HTTP 401 unauthorized
        def unauthorized_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=401, text="API key not valid")

        client_unauth = httpx.Client(transport=httpx.MockTransport(unauthorized_handler))
        provider_unauth = GeminiProvider(
            model="gemini-2.5-flash",
            options={"api_key": "bad-key"},
            http_client=client_unauth,
        )
        unauth_status = provider_unauth.health_check()
        assert unauth_status["success"] is False
        assert unauth_status["actionable_advice"] is not None
        assert "cmdkey /generic:icepick:gemini_api_key" in unauth_status["actionable_advice"]

    def test_gemini_provider_malformed_response_errors(self) -> None:
        """Test error handling when LLM returns no candidates or parts."""
        def handler_empty(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"candidates": []})

        client_empty = httpx.Client(transport=httpx.MockTransport(handler_empty))
        provider = GeminiProvider(
            model="gemini-2.5-flash",
            options={"api_key": "k"},
            http_client=client_empty,
        )
        with pytest.raises(ValueError, match="LLM returned no candidates"):
            provider.generate_text("hi")

        def handler_no_parts(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json={"candidates": [{"content": {}}]})

        client_no_parts = httpx.Client(transport=httpx.MockTransport(handler_no_parts))
        provider_no_parts = GeminiProvider(
            model="gemini-2.5-flash",
            options={"api_key": "k"},
            http_client=client_no_parts,
        )
        with pytest.raises(ValueError, match="candidate content contains no parts"):
            provider_no_parts.generate_text("hi")


class TestVertexAIProvider:
    """Tests for VertexAIProvider REST integration, token resolution, and health checks."""

    def test_vertex_provider_with_options(self) -> None:
        """Test that options dictionary correctly configures project and location."""
        provider = VertexAIProvider(
            model="gemini-2.5-flash",
            options={"project": "my-custom-project", "location": "us-east4"},
        )
        assert provider.name == "vertex"
        assert provider.project == "my-custom-project"
        assert provider.location == "us-east4"

    def test_vertex_provider_token_and_health_check(self) -> None:
        """Test text generation and health check with mocked token acquisition."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                status_code=200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "vertex answer"}]}}
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        provider = VertexAIProvider(
            model="gemini-2.5-flash",
            options={"project": "proj-123", "location": "us-central1"},
            http_client=client,
        )

        with patch.object(provider, "_get_token", return_value="mock-bearer-token"):
            text = provider.generate_text("SELECT 42")
            assert text == "vertex answer"

            assert len(captured_requests) == 1
            req = captured_requests[0]
            assert req.url == "https://us-central1-aiplatform.googleapis.com/v1/projects/proj-123/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent"
            assert req.headers["authorization"] == "Bearer mock-bearer-token"
            assert req.headers["content-type"] == "application/json"

            # Test health_check
            health = provider.health_check()
            assert health["success"] is True
            assert health["details"]["project"] == "proj-123"
            assert health["details"]["location"] == "us-central1"
            assert health["details"]["response_snippet"] == "vertex answer"
            assert health["actionable_advice"] is None

    def test_vertex_provider_global_location_url(self) -> None:
        """Test that location='global' produces aiplatform.googleapis.com host."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(
                status_code=200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        provider = VertexAIProvider(
            model="gemini-2.5-flash",
            options={"project": "global-proj", "location": "global"},
            http_client=client,
        )

        with patch.object(provider, "_get_token", return_value="tok"):
            provider.generate_text("ping")
            assert len(captured_requests) == 1
            assert captured_requests[0].url == "https://aiplatform.googleapis.com/v1/projects/global-proj/locations/global/publishers/google/models/gemini-2.5-flash:generateContent"

    def test_vertex_provider_missing_project_error_and_health_check(self) -> None:
        """Test that missing project raises ValueError and produces actionable advice."""
        with patch.dict("os.environ", {}, clear=True):
            provider = VertexAIProvider(model="gemini-2.5-flash", options={})
            assert provider.project is None

            with pytest.raises(ValueError, match="Google Cloud project ID is required"):
                provider.generate_text("ping")

            status = provider.health_check()
            assert status["success"] is False
            assert "Google Cloud project ID is required" in status["message"]
            assert status["actionable_advice"] is not None
            assert "GOOGLE_CLOUD_PROJECT" in status["actionable_advice"]

    def test_vertex_provider_token_acquisition_failure(self) -> None:
        """Test failure when google-auth and gcloud fallback both fail."""
        provider = VertexAIProvider(
            model="gemini-2.5-flash",
            options={"project": "p"},
        )
        with (
            patch.object(provider, "_get_vertex_token_from_gcloud", return_value=None),
            patch("google.auth.default", side_effect=Exception("No credentials")),
        ):
            with pytest.raises(ValueError, match="Failed to acquire Google Cloud ADC token"):
                provider.generate_text("ping")

            health = provider.health_check()
            assert health["success"] is False
            assert "Failed to acquire Google Cloud ADC token" in health["message"]
            assert health["actionable_advice"] is not None
            assert "gcloud auth application-default login" in health["actionable_advice"]

    def test_vertex_provider_gcloud_cli_fallback(self) -> None:
        """Test acquiring token via gcloud CLI fallback when google.auth fails."""
        provider = VertexAIProvider(
            model="gemini-2.5-flash",
            options={"project": "p"},
        )

        with (
            patch("google.auth.default", side_effect=Exception("google.auth unavailable")),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="gcloud-cli-token\n")
            token = provider._get_token()
            assert token == "gcloud-cli-token"


class TestProviderRegistry:
    """Tests for provider registry and factory functions."""

    def test_registry_create_provider(self) -> None:
        """Test create_provider creates appropriate provider instances."""
        gemini = create_provider("gemini", model="gemini-2.5-flash", options={"api_key": "k"})
        assert isinstance(gemini, GeminiProvider)
        assert gemini.model == "gemini-2.5-flash"
        assert gemini.api_key == "k"

        # Alias google -> GeminiProvider
        google_p = create_provider("google", model="gemini-2.5-flash", options={"api_key": "k"})
        assert isinstance(google_p, GeminiProvider)

        # Alias vertex / vertex_ai / vertexai -> VertexAIProvider
        vertex1 = create_provider("vertex", model="gemini-2.5-flash", options={"project": "p"})
        vertex2 = create_provider("vertex_ai", model="gemini-2.5-flash", options={"project": "p"})
        vertex3 = create_provider("VERTEXAI", model="gemini-2.5-flash", options={"project": "p"})
        assert isinstance(vertex1, VertexAIProvider)
        assert isinstance(vertex2, VertexAIProvider)
        assert isinstance(vertex3, VertexAIProvider)

    def test_registry_unknown_provider_raises_error(self) -> None:
        """Test get_provider_class raises Actionable ValueError for unknown providers."""
        with pytest.raises(ValueError) as exc_info:
            get_provider_class("unknown_provider")

        msg = str(exc_info.value)
        assert "Unsupported LLM provider: 'unknown_provider'" in msg
        assert "Available providers are:" in msg
        assert "'gemini'" in msg
        assert "'vertex'" in msg

    def test_registry_custom_provider_registration(self) -> None:
        """Test registering a custom provider and creating an instance through registry."""
        try:
            register_provider("custom_llm", DummyCustomProvider)
            provider = create_provider("custom_llm", model="dummy-model")
            assert isinstance(provider, DummyCustomProvider)
            assert provider.generate_text("hello") == "custom echo: hello"
        finally:
            PROVIDER_REGISTRY.pop("custom_llm", None)
