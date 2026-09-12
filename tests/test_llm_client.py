"""London-style unit tests for the LLMClient module using httpx mock transports."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlglot import exp

from icepick.config import Config
from icepick.exceptions import AuthenticationError
from icepick.llm.client import LLMClient
from icepick.llm.slicer import SliceContext


def _make_gemini_response(text: str) -> dict[str, Any]:
    """Helper to construct a Gemini / Vertex AI JSON response payload."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }


def _dummy_slice_context(prompt: str = "Rewrite this query") -> SliceContext:
    """Helper to create a minimal SliceContext."""
    return SliceContext(
        target_sql="SELECT * FROM tbl",
        parent_info="Top-level SELECT",
        referenced_tables=["tbl"],
        referenced_columns=["col1"],
        prompt=prompt,
    )


class TestLLMClient:
    """Unit tests for LLMClient."""

    def test_gemini_generate_text_and_rewrite_success(self) -> None:
        """Test successful text generation and AST rewriting using Gemini provider."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            data = _make_gemini_response(
                "```sql\nSELECT order_id, amount FROM orders WHERE amount > 0\n```"
            )
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        llm = LLMClient(
            provider="gemini",
            model="gemini-3.8-flash",
            api_key="test-api-key",
            http_client=client,
        )

        slice_ctx = _dummy_slice_context()
        ast_node = llm.rewrite_fragment(slice_ctx)

        assert ast_node is not None
        assert isinstance(ast_node, exp.Select)
        assert len(captured_requests) == 1

        req = captured_requests[0]
        assert req.method == "POST"
        assert (
            req.url
            == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
        )
        assert req.headers["x-goog-api-key"] == "test-api-key"
        assert req.headers["Content-Type"] == "application/json"

        body = json.loads(req.content.decode("utf-8"))
        assert body == {"contents": [{"parts": [{"text": slice_ctx.prompt}]}]}
        assert "generationConfig" not in body
        assert "temperature" not in str(body)

    def test_vertex_generate_text_and_rewrite_success(self) -> None:
        """Test successful text generation and AST rewriting using Vertex AI provider."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            data = _make_gemini_response(
                "```sql\nSELECT customer_id, count(*) FROM customers GROUP BY customer_id\n```"
            )
            return httpx.Response(200, json=data)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "mock-bearer-token-12345"

        with patch("google.auth.default", return_value=(mock_creds, "mock-project")):
            llm = LLMClient(
                provider="vertex",
                model="gemini-2.5-pro",
                project="my-gcp-project",
                location="us-central1",
                http_client=client,
            )

            slice_ctx = _dummy_slice_context()
            ast_node = llm.rewrite_fragment(slice_ctx)

            assert ast_node is not None
            assert isinstance(ast_node, exp.Select)
            assert len(captured_requests) == 1

            req = captured_requests[0]
            assert req.method == "POST"
            expected_url = (
                "https://us-central1-aiplatform.googleapis.com/v1/projects/my-gcp-project"
                "/locations/us-central1/publishers/google/models/gemini-2.5-pro:generateContent"
            )
            assert str(req.url) == expected_url
            assert req.headers["Authorization"] == "Bearer mock-bearer-token-12345"

            body = json.loads(req.content.decode("utf-8"))
            assert body == {"contents": [{"parts": [{"text": slice_ctx.prompt}]}]}
            assert "generationConfig" not in body
            assert "temperature" not in str(body)

    def test_vertex_token_refresh_when_invalid(self) -> None:
        """Test that google-auth token is refreshed when invalid."""
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.token = "refreshed-token"

        def refresh_side_effect(request: object) -> None:
            mock_creds.valid = True

        mock_creds.refresh.side_effect = refresh_side_effect

        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json=_make_gemini_response("SELECT 1"))
        )
        client = httpx.Client(transport=transport)

        with patch("google.auth.default", return_value=(mock_creds, "project")):
            llm = LLMClient(
                provider="vertex",
                project="project",
                http_client=client,
            )
            text = llm.generate_text("test")
            assert text == "SELECT 1"
            mock_creds.refresh.assert_called_once()

    def test_vertex_token_missing_raises_value_error(self) -> None:
        """Test error when credentials token cannot be acquired."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = None

        with patch("google.auth.default", return_value=(mock_creds, "project")):
            llm = LLMClient(
                provider="vertex",
                project="project",
            )
            with pytest.raises(ValueError, match="Failed to obtain OAuth2 token"):
                llm.generate_text("test")

    def test_sql_extraction_formats(self) -> None:
        """Test extracting SQL from various code-fence markdown formats and raw text."""
        # 1. ```sql ... ```
        sql1 = LLMClient._extract_sql(
            "Here is the answer:\n```sql\nSELECT 1 AS num\n```\nExplanation..."
        )
        assert sql1 == "SELECT 1 AS num"

        # 2. ``` ... ``` (without sql tag)
        sql2 = LLMClient._extract_sql("```\nSELECT 2 AS num\n```")
        assert sql2 == "SELECT 2 AS num"

        # 3. Raw SQL with no markdown fences
        sql3 = LLMClient._extract_sql("   SELECT 3 AS num   ")
        assert sql3 == "SELECT 3 AS num"

        # 4. Empty string
        assert LLMClient._extract_sql("") == ""

    def test_fail_safe_on_syntax_error_returns_none(self) -> None:
        """Test that invalid SQL returned by LLM results in None without raising an exception."""
        transport = httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=_make_gemini_response("```sql\nSELECT FROM WHERE !!! INVALID\n```")
            )
        )
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        result = llm.rewrite_fragment(_dummy_slice_context())
        assert result is None

    def test_fail_safe_on_empty_sql_returns_none(self) -> None:
        """Test that empty or whitespace-only response results in None without crashing."""
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json=_make_gemini_response("```sql\n\n```"))
        )
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        result = llm.rewrite_fragment(_dummy_slice_context())
        assert result is None

    def test_fail_safe_on_http_status_error_returns_none(self) -> None:
        """Test that HTTP 500 error from LLM API results in None via rewrite_fragment."""
        transport = httpx.MockTransport(lambda r: httpx.Response(500, text="Internal Server Error"))
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        result = llm.rewrite_fragment(_dummy_slice_context())
        assert result is None

    def test_fail_safe_on_http_timeout_returns_none(self) -> None:
        """Test that request timeout results in None via rewrite_fragment."""

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Request timed out", request=request)

        transport = httpx.MockTransport(timeout_handler)
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        result = llm.rewrite_fragment(_dummy_slice_context())
        assert result is None

    def test_generate_text_raises_on_empty_candidates(self) -> None:
        """Test that an empty candidates array in response raises ValueError."""
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"candidates": []}))
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        with pytest.raises(ValueError, match="LLM returned no candidates"):
            llm.generate_text("test")

    def test_generate_text_raises_on_empty_parts(self) -> None:
        """Test that candidate content without parts raises ValueError."""
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]})
        )
        client = httpx.Client(transport=transport)

        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)
        with pytest.raises(ValueError, match="candidate content contains no parts"):
            llm.generate_text("test")

    def test_missing_credentials_raise_actionable_or_value_error(self) -> None:
        """Test AuthenticationError for Gemini and ValueError for Vertex when credentials are not configured."""
        # Gemini without API key: raises actionable AuthenticationError
        with (
            patch("icepick.security.credentials.read_wcm_credential_fn", return_value=None),
            patch.dict("os.environ", {}, clear=True),
        ):
            llm_gemini = LLMClient(provider="gemini", api_key=None)
            assert llm_gemini.api_key is None
            with pytest.raises(AuthenticationError, match=r"\[Authentication Error\]") as exc_info:
                llm_gemini.generate_text("test")
            assert "gemini_api_key" in str(exc_info.value)
            assert "cmdkey" in str(exc_info.value)

        # Vertex without project ID: raises ValueError
        llm_vertex = LLMClient(provider="vertex", project=None)
        with patch.dict("os.environ", {}, clear=True):
            llm_vertex.project = None
            with pytest.raises(ValueError, match="Google Cloud project ID is required"):
                llm_vertex.generate_text("test")

    def test_gemini_resolves_credential_from_debug_env_var(self) -> None:
        """Test resolving Gemini API key from DEBUG_ICEPICK_GEMINI_API_KEY environment variable."""
        with patch.dict("os.environ", {"DEBUG_ICEPICK_GEMINI_API_KEY": "debug-secret-key-123"}):
            llm = LLMClient(provider="gemini", api_key=None)
            assert llm.api_key == "debug-secret-key-123"

    def test_gemini_resolves_credential_from_wcm(self) -> None:
        """Test resolving Gemini API key from Windows Credential Manager when env var is absent."""

        def mock_wcm(target: str) -> str | None:
            if target == "icepick:gemini_api_key":
                return "wcm-vault-key-456"
            return None

        with (
            patch("icepick.security.credentials.read_wcm_credential_fn", side_effect=mock_wcm),
            patch.dict("os.environ", {}, clear=True),
        ):
            llm = LLMClient(provider="gemini", api_key=None)
            assert llm.api_key == "wcm-vault-key-456"

    def test_gemini_generic_env_var_ignored(self) -> None:
        """Test that generic ambient GEMINI_API_KEY is strictly ignored to prevent secret leakage."""
        with (
            patch("icepick.security.credentials.read_wcm_credential_fn", return_value=None),
            patch.dict("os.environ", {"GEMINI_API_KEY": "leaked-ambient-key"}, clear=True),
        ):
            llm = LLMClient(provider="gemini", api_key=None)
            assert llm.api_key is None
            with pytest.raises(AuthenticationError):
                llm.generate_text("test")

    def test_gemini_explicit_api_key_takes_highest_priority(self) -> None:
        """Test that explicit api_key argument overrides both DEBUG env var and WCM."""
        with patch.dict("os.environ", {"DEBUG_ICEPICK_GEMINI_API_KEY": "debug-env-key"}):
            llm = LLMClient(provider="gemini", api_key="explicit-param-key")
            assert llm.api_key == "explicit-param-key"

    def test_gemini_config_api_key_takes_priority(self) -> None:
        """Test that Config(gemini_api_key=...) overrides DEBUG env var and WCM."""
        config = Config(gemini_api_key="cfg-explicit-key")
        with patch.dict("os.environ", {"DEBUG_ICEPICK_GEMINI_API_KEY": "debug-env-key"}):
            llm = LLMClient(config=config)
            assert llm.api_key == "cfg-explicit-key"

    def test_provider_normalization_and_validation(self) -> None:
        """Test provider aliases and rejection of invalid providers."""
        client_vertex = LLMClient(provider="vertex_ai", project="p")
        assert client_vertex.provider == "vertex"

        client_vertex2 = LLMClient(provider="VERTEXAI", project="p")
        assert client_vertex2.provider == "vertex"

        client_gemini = LLMClient(provider="google", api_key="k")
        assert client_gemini.provider == "gemini"

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMClient(provider="unsupported_ai")

    def test_initialization_defaults(self) -> None:
        """Test default model and provider values when no config or overrides are provided."""
        llm = LLMClient(api_key="mock-key")
        assert llm.provider == "gemini"
        assert llm.model == "gemini-3.8-flash"

    def test_initialization_from_config(self) -> None:
        """Test initializing LLMClient with a Config instance."""
        config = Config(
            llm_provider="gemini",
            llm_model="gemini-3.8-flash",
            gemini_api_key="cfg-key",
            gcp_project="cfg-project",
            gcp_location="us-east4",
        )
        llm = LLMClient(config=config)
        assert llm.provider == "gemini"
        assert llm.model == "gemini-3.8-flash"
        assert llm.api_key == "cfg-key"
        assert llm.project == "cfg-project"
        assert llm.location == "us-east4"

    def test_default_http_client_lifecycle(self) -> None:
        """Test that default temporary httpx.Client is created and closed when not injected."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = _make_gemini_response("SELECT 1")
            mock_client.post.return_value = mock_response

            llm = LLMClient(provider="gemini", api_key="k")
            text = llm.generate_text("test")

            assert text == "SELECT 1"
            mock_client.post.assert_called_once()
            mock_client.close.assert_called_once()

    def test_httpx_auth_adapter_properties_and_request(self) -> None:
        """Test _HttpxAuthResponse and _HttpxAuthRequest functionality and lifecycle."""
        from google.auth import transport as auth_transport

        from icepick.llm.client import _HttpxAuthRequest, _HttpxAuthResponse

        # Test _HttpxAuthResponse properties
        raw_resp = httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"token": "xyz"}',
        )
        adapted_resp = _HttpxAuthResponse(raw_resp)
        assert isinstance(adapted_resp, auth_transport.Response)
        assert adapted_resp.status == 200
        assert adapted_resp.headers["content-type"] == "application/json"
        assert adapted_resp.data == b'{"token": "xyz"}'

        # Test _HttpxAuthRequest with injected client
        mock_client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok"))
        )
        req_adapter = _HttpxAuthRequest(client=mock_client)
        resp = req_adapter(url="https://example.com", method="POST", body=b"data")
        assert resp.status == 200
        assert resp.data == b"ok"

        # Test _HttpxAuthRequest without injected client (creates and closes client)
        with patch("httpx.Client") as mock_client_cls:
            mock_c = MagicMock()
            mock_client_cls.return_value = mock_c
            mock_c.request.return_value = raw_resp

            req_adapter_default = _HttpxAuthRequest()
            resp2 = req_adapter_default(url="https://example.com")
            assert resp2.status == 200
            mock_c.request.assert_called_once()
            mock_c.close.assert_called_once()

    def test_httpx_auth_adapter_error(self) -> None:
        """Test that _HttpxAuthRequest translates exceptions to TransportError."""
        from google.auth.exceptions import TransportError

        from icepick.llm.client import _HttpxAuthRequest

        def err_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        client = httpx.Client(transport=httpx.MockTransport(err_handler))
        req_adapter = _HttpxAuthRequest(client=client)

        with pytest.raises(TransportError, match="Connection refused"):
            req_adapter(url="https://example.com")

    def test_rewrite_fragment_non_expression_fallback(self) -> None:
        """Test that rewrite_fragment returns None when sqlglot parses a non-Expression node."""
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json=_make_gemini_response("```sql\nSELECT 1\n```"))
        )
        client = httpx.Client(transport=transport)
        llm = LLMClient(provider="gemini", api_key="dummy", http_client=client)

        # Mock parse_one to return something that is not exp.Expression
        with patch("sqlglot.parse_one", return_value="not_an_ast_expression"):
            result = llm.rewrite_fragment(_dummy_slice_context())
            assert result is None
