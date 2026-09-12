"""REST API client for Gemini and Vertex AI with fail-safe AST rewriting.

Supports Google AI Studio (Gemini) API key authentication and Google Cloud
Vertex AI OAuth2 Bearer token authentication, parsing response SQL into sqlglot AST nodes.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

import httpx
import sqlglot
from google.auth import exceptions as auth_exceptions
from google.auth import transport as auth_transport
from sqlglot import exp

from icepick.config import Config
from icepick.exceptions import AuthenticationError
from icepick.security.credentials import resolve_credential

if TYPE_CHECKING:
    from icepick.llm.slicer import SliceContext

logger = logging.getLogger(__name__)


class _HttpxAuthResponse(auth_transport.Response):
    """Adapter bridging httpx.Response to google.auth.transport.Response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._response.headers)

    @property
    def data(self) -> bytes:
        return self._response.content


class _HttpxAuthRequest(auth_transport.Request):
    """Adapter allowing google-auth to refresh credentials via httpx without requests."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> auth_transport.Response:
        client = self._client or httpx.Client(timeout=timeout)
        try:
            resp = client.request(
                method=method,
                url=url,
                content=body,
                headers=headers,
                timeout=timeout,
            )
            return _HttpxAuthResponse(resp)
        except Exception as exc:
            raise auth_exceptions.TransportError(exc) from exc  # type: ignore[no-untyped-call]
        finally:
            if self._client is None:
                client.close()


class LLMClient:
    """Client for generating SQL refactorings using Gemini or Vertex AI REST APIs.

    Attributes:
        provider: "gemini" or "vertex".
        model: LLM model identifier.
        api_key: Gemini API key (for Gemini provider).
        project: GCP project ID (for Vertex AI provider).
        location: GCP region (for Vertex AI provider).
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        project: str | None = None,
        location: str | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize the LLMClient with configuration or explicit overrides.

        Args:
            config: Optional Config instance to pull defaults from.
            provider: LLM service provider ("gemini" or "vertex").
            model: Model name (e.g. "gemini-2.5-flash").
            api_key: API key for Google AI Studio Gemini API.
            project: Google Cloud project ID for Vertex AI.
            location: Google Cloud region for Vertex AI.
            timeout: Request timeout in seconds (default: 30.0).
            http_client: Optional custom httpx.Client for testing and dependency injection.
        """
        cfg = config or Config()

        raw_provider = provider or cfg.llm_provider
        self.provider = self._normalize_provider(raw_provider)
        self.model = model or cfg.llm_model

        self._auth_error: AuthenticationError | None = None
        resolved_api_key = api_key or cfg.gemini_api_key
        if not resolved_api_key:
            try:
                resolved_api_key, _ = resolve_credential("gemini_api_key")
            except AuthenticationError as exc:
                self._auth_error = exc
                resolved_api_key = None

        self.api_key = resolved_api_key
        self.project = (
            project
            or cfg.gcp_project
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
        )
        self.location = (
            location
            or cfg.gcp_location
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or os.getenv("GCP_LOCATION")
            or "us-central1"
        )
        self.timeout = timeout
        self._http_client = http_client

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        """Normalize provider string to 'gemini' or 'vertex'."""
        normalized = provider.strip().lower()
        if normalized in {"vertex", "vertex_ai", "vertexai"}:
            return "vertex"
        if normalized in {"gemini", "google"}:
            return "gemini"
        msg = f"Unsupported LLM provider: '{provider}'. Must be 'gemini' or 'vertex'."
        raise ValueError(msg)

    def _get_vertex_token(self) -> str:
        """Acquire an OAuth2 Bearer token using google-auth credentials."""
        import google.auth

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(_HttpxAuthRequest(self._http_client))  # type: ignore[no-untyped-call]
        token = getattr(credentials, "token", None)
        if not token:
            msg = "Failed to obtain OAuth2 token from Google Cloud credentials"
            raise ValueError(msg)
        return str(token)

    def _build_request_params(self, prompt: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Construct the URL, headers, and JSON body for the REST request."""
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0},
        }

        if self.provider == "gemini":
            if not self.api_key:
                if self._auth_error is not None:
                    raise self._auth_error
                # If api_key was cleared dynamically, attempt resolution or raise AuthenticationError
                resolved_key, _ = resolve_credential("gemini_api_key")
                self.api_key = resolved_key
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            headers = {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            }
            return url, headers, payload

        # Vertex AI provider
        if not self.project:
            msg = (
                "Google Cloud project ID is required for Vertex AI. Provide project argument, "
                "set gcp_project in Config, or set GOOGLE_CLOUD_PROJECT env var."
            )
            raise ValueError(msg)
        url = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project}"
            f"/locations/{self.location}/publishers/google/models/{self.model}:generateContent"
        )
        token = self._get_vertex_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return url, headers, payload

    def generate_text(self, prompt: str) -> str:
        """Call the configured Gemini or Vertex AI REST API to generate text.

        Args:
            prompt: The text prompt to send to the model.

        Returns:
            str: Generated text response from the model.

        Raises:
            AuthenticationError: If Gemini API key cannot be resolved from secure credential sources.
            ValueError: If required credentials/project are missing or response format is unexpected.
            httpx.HTTPError: If the HTTP request fails.
        """
        url, headers, payload = self._build_request_params(prompt)

        client = self._http_client or httpx.Client(timeout=self.timeout)
        try:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        finally:
            if self._http_client is None:
                client.close()

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            msg = f"LLM returned no candidates. Full response: {data}"
            raise ValueError(msg)

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            msg = f"LLM candidate content contains no parts. Candidate: {candidate}"
            raise ValueError(msg)

        text = parts[0].get("text", "")
        return str(text)

    def rewrite_fragment(
        self, slice_ctx: SliceContext, dialect: str = "snowflake"
    ) -> exp.Expression | None:
        """Generate and parse an AST replacement node with guaranteed fail-safe fallback.

        Attempts to call the LLM API using the prompt in `slice_ctx`, extracts the SQL fragment
        from the response, validates syntax using sqlglot, and returns the replacement AST.
        If any network error, API failure, or SQL syntax error occurs, safely returns None.

        Args:
            slice_ctx: The extracted context containing the prompt and target metadata.
            dialect: Target SQL dialect for parsing (default: "snowflake").

        Returns:
            exp.Expression | None: The validated replacement AST node, or None on failure.
        """
        try:
            raw_text = self.generate_text(slice_ctx.prompt)
            extracted_sql = self._extract_sql(raw_text)
            if not extracted_sql:
                logger.warning("No valid SQL found in LLM response: %s", raw_text)
                return None

            replacement_ast = sqlglot.parse_one(extracted_sql, read=dialect)
            if not isinstance(replacement_ast, exp.Expression):
                logger.warning("sqlglot parsed non-Expression node for SQL: %s", extracted_sql)
                return None

            return replacement_ast

        except Exception as exc:  # noqa: BLE001
            logger.warning("Fail-Safe: LLM rewrite failed, falling back safely. Reason: %s", exc)
            return None

    @staticmethod
    def _extract_sql(text: str) -> str:
        """Extract SQL code from raw text, stripping markdown fences if present."""
        if not text:
            return ""

        # Check for ```sql ... ```
        sql_block_match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if sql_block_match:
            return sql_block_match.group(1).strip()

        # Check for generic ``` ... ```
        generic_block_match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if generic_block_match:
            return generic_block_match.group(1).strip()

        # Return stripped text directly if no code fence is found
        return text.strip()
