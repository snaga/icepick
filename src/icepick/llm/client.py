"""REST API client for Gemini and Vertex AI with fail-safe AST rewriting.

Supports Google AI Studio (Gemini) API key authentication and Google Cloud
Vertex AI OAuth2 Bearer token authentication, parsing response SQL into sqlglot AST nodes.

Architecture note
-----------------
Since 16-2 refactoring, ``LLMClient`` delegates provider-specific logic (auth, URL construction,
HTTP requests) to a :class:`~icepick.llm.providers.base.BaseLLMProvider` instance created via
:func:`~icepick.llm.providers.create_provider`.  The high-level orchestration methods
(:meth:`rewrite_fragment`, :meth:`_extract_sql`) remain in this class unchanged.

Backward-compatibility surface maintained
-----------------------------------------
* ``LLMClient.api_key`` – property (Gemini) / None (Vertex)
* ``LLMClient.project`` – property (Vertex) / None (Gemini)
* ``LLMClient.location`` – property (Vertex) / "us-central1" (Gemini)
* ``LLMClient.provider`` – canonical provider name string
* ``LLMClient.model`` – model name string
* ``LLMClient.timeout`` – timeout float
* ``LLMClient._build_request_params(prompt)`` – proxies to VertexAIProvider / GeminiProvider
* ``LLMClient._get_vertex_token()`` – proxies to VertexAIProvider
* ``LLMClient._get_vertex_token_from_gcloud()`` – proxies to VertexAIProvider
* ``_HttpxAuthRequest``, ``_HttpxAuthResponse`` – re-exported from providers.vertex
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

import httpx
import sqlglot
from sqlglot import exp

from icepick.config import Config
from icepick.llm.providers import create_provider
from icepick.llm.providers.gemini import GeminiProvider
from icepick.llm.providers.vertex import VertexAIProvider, _HttpxAuthRequest, _HttpxAuthResponse

if TYPE_CHECKING:
    from icepick.llm.providers.base import BaseLLMProvider
    from icepick.llm.slicer import SliceContext

logger = logging.getLogger(__name__)

__all__ = ["LLMClient", "_HttpxAuthRequest", "_HttpxAuthResponse"]


class LLMClient:
    """Client for generating SQL refactorings using Gemini or Vertex AI REST APIs.

    Internally delegates provider-specific logic (authentication, URL construction,
    HTTP requests) to a :class:`~icepick.llm.providers.base.BaseLLMProvider` instance.
    All legacy attributes and helper methods are maintained for backward compatibility.

    Attributes:
        provider: Canonical provider name – "gemini" or "vertex".
        model: LLM model identifier.
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
        provider_options: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the LLMClient with configuration or explicit overrides.

        All explicit keyword arguments take precedence over ``Config`` values and
        ``provider_options``.  The merge order (lowest → highest priority) is::

            Config.llm_options < provider_options < {api_key, project, location}

        Args:
            config: Optional Config instance to pull defaults from.
            provider: LLM service provider ("gemini" or "vertex").
            model: Model name (e.g. "gemini-3.8-flash").
            api_key: API key for Google AI Studio Gemini API.
            project: Google Cloud project ID for Vertex AI.
            location: Google Cloud region for Vertex AI.
            timeout: Request timeout in seconds (default: 30.0).
            http_client: Optional custom httpx.Client for testing and dependency injection.
            provider_options: Generic provider-specific options dictionary.  Keys ``api_key``,
                ``project``, and ``location`` are recognised and transparently merged so that
                callers can pass all options in a single dict without breaking existing call sites.
        """
        cfg = config or Config()

        # ── Resolve provider / model ──────────────────────────────────────────
        raw_provider = provider or cfg.llm_provider
        self.provider: str = self._normalize_provider(raw_provider)
        self.model: str = model or cfg.llm_model
        self.timeout: float = timeout

        # ── Build merged options dict (lowest → highest priority) ─────────────
        # 1. Config.llm_options (file/env-level generic options)
        merged: dict[str, Any] = dict(cfg.llm_options)

        # 2. Explicit provider_options argument
        if provider_options:
            merged.update(provider_options)

        # 3. Explicit keyword arguments (highest priority, backward-compat surface)
        #    api_key: Gemini API key
        resolved_api_key = api_key or cfg.gemini_api_key
        if resolved_api_key:
            merged["api_key"] = resolved_api_key

        #    project / location: Vertex AI (also exposed as backward-compat instance attrs)
        resolved_project: str | None = (
            project
            or cfg.gcp_project
            or merged.get("project")
        )
        if resolved_project:
            merged["project"] = resolved_project

        resolved_location: str = (
            location
            or cfg.gcp_location
            or merged.get("location")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or os.getenv("GCP_LOCATION")
            or "us-central1"
        )
        merged["location"] = resolved_location

        # ── Instantiate the pluggable provider ────────────────────────────────
        self._provider: BaseLLMProvider = create_provider(
            name=self.provider,
            model=self.model,
            options=merged,
            timeout=timeout,
            http_client=http_client,
        )

        # Keep a direct reference for close-lifecycle management compatibility
        self._http_client = http_client

        # ── Store project / location as direct attributes (backward compat) ───
        # These are kept on the LLMClient regardless of provider type so that
        # callers reading llm.project / llm.location always get meaningful values
        # even when the provider is Gemini (where they may not be used).
        self._project: str | None = resolved_project
        self._location: str = resolved_location

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        """Normalize provider string to 'gemini' or 'vertex'.

        Args:
            provider: Raw provider name string (case-insensitive).

        Returns:
            str: Canonical provider name.

        Raises:
            ValueError: If the provider is not a recognised alias.
        """
        normalized = provider.strip().lower()
        if normalized in {"vertex", "vertex_ai", "vertexai"}:
            return "vertex"
        if normalized in {"gemini", "google"}:
            return "gemini"
        msg = f"Unsupported LLM provider: '{provider}'. Must be 'gemini' or 'vertex'."
        raise ValueError(msg)

    # ── Backward-compatible attribute properties ──────────────────────────────

    @property
    def api_key(self) -> str | None:
        """Gemini API key resolved by the underlying GeminiProvider, or None for Vertex.

        Supports both read and write access to preserve test-level mutation patterns.
        """
        if isinstance(self._provider, GeminiProvider):
            return self._provider.api_key
        return None

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        """Set the API key on the underlying GeminiProvider (backward compatibility)."""
        if isinstance(self._provider, GeminiProvider):
            self._provider.api_key = value

    @property
    def project(self) -> str | None:
        """GCP project ID.  Readable for any provider; writable and synced to VertexAIProvider."""
        return self._project

    @project.setter
    def project(self, value: str | None) -> None:
        """Set project and sync to VertexAIProvider when active (backward compatibility)."""
        self._project = value
        if isinstance(self._provider, VertexAIProvider):
            self._provider.project = value

    @property
    def location(self) -> str:
        """GCP location/region.  Readable for any provider; writable and synced to VertexAIProvider."""
        return self._location

    @location.setter
    def location(self, value: str) -> None:
        """Set location and sync to VertexAIProvider when active (backward compatibility)."""
        self._location = value
        if isinstance(self._provider, VertexAIProvider):
            self._provider.location = value

    # ── Backward-compatible proxy methods (provider-specific helpers) ─────────

    def _get_vertex_token_from_gcloud(self) -> str | None:
        """Proxy to :meth:`VertexAIProvider._get_vertex_token_from_gcloud` (backward compat).

        In corporate environments using service account impersonation or SSO,
        google.auth.default() might fail to find or refresh credentials directly.
        Running the gcloud CLI invokes the native authentication helper.

        Returns:
            str | None: The access token string if successful, or None otherwise.

        Raises:
            TypeError: If the current provider is not VertexAIProvider.
        """
        if not isinstance(self._provider, VertexAIProvider):
            return None  # No-op for non-Vertex providers
        return self._provider._get_vertex_token_from_gcloud()

    def _get_vertex_token(self) -> str:
        """Proxy to :meth:`VertexAIProvider._get_token` (backward compat).

        Returns:
            str: Valid OAuth2 access token.

        Raises:
            ValueError: If token acquisition fails via both google-auth and gcloud CLI fallback.
            TypeError: If called on a non-Vertex provider.
        """
        if not isinstance(self._provider, VertexAIProvider):
            msg = "_get_vertex_token() is only available for the 'vertex' provider."
            raise TypeError(msg)
        return self._provider._get_token()

    def _build_request_params(self, prompt: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Construct the URL, headers, and JSON body for the REST request (backward compat).

        Delegates to the underlying provider's internal build logic so that existing
        test patches (e.g. ``patch.object(llm, "_get_vertex_token", ...)``) continue
        to intercept calls correctly.

        Args:
            prompt: The text prompt to include in the request body.

        Returns:
            tuple[str, dict[str, str], dict[str, Any]]: (url, headers, payload)

        Raises:
            AuthenticationError: For Gemini when api_key is missing.
            ValueError: For Vertex when project is missing or token acquisition fails.
        """
        from icepick.security.credentials import resolve_credential

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
        }

        if self.provider == "gemini":
            assert isinstance(self._provider, GeminiProvider)
            if not self._provider.api_key:
                if self._provider._auth_error is not None:
                    raise self._provider._auth_error
                resolved_key, _ = resolve_credential("gemini_api_key")
                self._provider.api_key = resolved_key
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            api_key_str = self._provider.api_key
            assert api_key_str is not None  # guaranteed by the guard above
            headers: dict[str, str] = {
                "x-goog-api-key": api_key_str,
                "Content-Type": "application/json",
            }
            return url, headers, payload

        # Vertex AI provider
        assert isinstance(self._provider, VertexAIProvider)
        if not self._project:
            msg = (
                "Google Cloud project ID is required for Vertex AI. Provide project argument, "
                "set gcp_project in Config, or set GOOGLE_CLOUD_PROJECT env var."
            )
            raise ValueError(msg)
        host = (
            "aiplatform.googleapis.com"
            if self._location == "global"
            else f"{self._location}-aiplatform.googleapis.com"
        )
        url = (
            f"https://{host}/v1/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{self.model}:generateContent"
        )
        # Call self._get_vertex_token() so that patch.object(llm, "_get_vertex_token", ...)
        # is intercepted correctly in tests.
        token = self._get_vertex_token()
        vertex_headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return url, vertex_headers, payload

    # ── Core public API ───────────────────────────────────────────────────────

    def generate_text(self, prompt: str) -> str:
        """Call the configured Gemini or Vertex AI REST API to generate text.

        Delegates to the underlying :class:`~icepick.llm.providers.base.BaseLLMProvider`
        instance, which handles authentication, URL construction, and HTTP communication.

        Args:
            prompt: The text prompt to send to the model.

        Returns:
            str: Generated text response from the model.

        Raises:
            AuthenticationError: If Gemini API key cannot be resolved from secure credential sources.
            ValueError: If required credentials/project are missing or response format is unexpected.
            httpx.HTTPError: If the HTTP request fails.
        """
        return self._provider.generate_text(prompt)

    def health_check(self) -> dict[str, Any]:
        """Perform a proactive health check ping using the underlying provider.

        Delegates directly to the configured
        :class:`~icepick.llm.providers.base.BaseLLMProvider` instance.

        Returns:
            dict[str, Any]: Health status dictionary containing success, duration_ms,
                message, details, and actionable_advice.
        """
        return self._provider.health_check()

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
        """Extract SQL code from raw text, stripping markdown fences if present.

        Args:
            text: Raw text that may contain markdown code fences around SQL.

        Returns:
            str: Extracted SQL string, or empty string if input is empty.
        """
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
