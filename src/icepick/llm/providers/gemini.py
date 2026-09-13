"""Gemini REST API provider using Google AI Studio API key authentication."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from icepick.exceptions import AuthenticationError
from icepick.llm.providers.base import BaseLLMProvider
from icepick.security.credentials import (
    format_actionable_provider_guidance,
    resolve_credential,
)

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """LLM provider for Google AI Studio (Gemini) REST API.

    Resolves credentials following the strict priority pyramid:
    1. Explicit 'api_key' in the options dictionary.
    2. Debug environment variable (DEBUG_ICEPICK_GEMINI_API_KEY) or
       Windows Credential Manager (Target: icepick:gemini_api_key).
    """

    def __init__(
        self,
        model: str,
        options: dict[str, Any] | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize GeminiProvider with model name and configuration options.

        Args:
            model: Gemini model identifier (e.g., 'gemini-2.5-flash').
            options: Optional configuration dictionary. Can contain 'api_key'.
            timeout: HTTP request timeout in seconds (default: 30.0).
            http_client: Optional injected httpx.Client for testing and pooling.
        """
        super().__init__(model=model, options=options, timeout=timeout, http_client=http_client)

        self._auth_error: AuthenticationError | None = None
        resolved_api_key = self.options.get("api_key")
        if not resolved_api_key:
            try:
                resolved_api_key, _ = resolve_credential("gemini_api_key")
            except AuthenticationError as exc:
                self._auth_error = exc
                resolved_api_key = None

        self.api_key = resolved_api_key

    @property
    def name(self) -> str:
        """Canonical provider name."""
        return "gemini"

    def _ensure_api_key(self) -> str:
        """Ensure an API key is present or attempt dynamic resolution.

        Returns:
            str: Resolved Gemini API key.

        Raises:
            AuthenticationError: If the API key is not configured and cannot be resolved.
        """
        if not self.api_key:
            if self._auth_error is not None:
                raise self._auth_error
            resolved_key, _ = resolve_credential("gemini_api_key")
            self.api_key = resolved_key
        return self.api_key

    def generate_text(self, prompt: str) -> str:
        """Generate text using Google AI Studio Gemini generateContent REST endpoint.

        Args:
            prompt: Text prompt to submit.

        Returns:
            str: Generated text from the first candidate.

        Raises:
            AuthenticationError: If the API key is missing or invalid.
            ValueError: If the response is malformed or has no candidate parts.
            httpx.HTTPError: If the HTTP request fails.
        """
        api_key = self._ensure_api_key()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
        }

        client, should_close = self._get_client()
        try:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        finally:
            if should_close:
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

        # Aggregate all text blocks from parts to support thinking models and multi-part responses.
        text = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict) and "text" in part
        )
        if not text.strip():
            raise ValueError("Gemini returned an empty response or unexpected content format.")

        return str(text)

    def health_check(self) -> dict[str, Any]:
        """Perform round-trip health check ping to verify Gemini API connectivity and credentials.

        Returns:
            dict[str, Any]: Health status dictionary with latency and actionable error advice.
        """
        start = time.perf_counter()
        try:
            # Force verification of API key before network request
            _ = self._ensure_api_key()
            response_text = self.generate_text("ping")
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            return {
                "success": True,
                "duration_ms": duration_ms,
                "message": f"Successfully connected to Gemini ({self.model}).",
                "details": {
                    "provider": self.name,
                    "model": self.model,
                    "response_snippet": response_text[:100].strip() if response_text else "",
                },
                "actionable_advice": None,
            }
        except AuthenticationError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            return {
                "success": False,
                "duration_ms": duration_ms,
                "message": str(exc),
                "details": {"provider": self.name, "model": self.model},
                "actionable_advice": format_actionable_provider_guidance("gemini"),
            }
        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            err_str = str(exc).lower()
            advice = None
            if (
                "401" in err_str
                or "403" in err_str
                or "api_key" in err_str
                or "key not valid" in err_str
            ):
                advice = format_actionable_provider_guidance("gemini")
            return {
                "success": False,
                "duration_ms": duration_ms,
                "message": str(exc),
                "details": {"provider": self.name, "model": self.model},
                "actionable_advice": advice,
            }
