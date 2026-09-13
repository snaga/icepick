"""Vertex AI REST API provider using Google Cloud ADC OAuth2 Bearer token authentication."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any

import httpx
from google.auth import exceptions as auth_exceptions
from google.auth import transport as auth_transport

from icepick.llm.providers.base import BaseLLMProvider

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


class VertexAIProvider(BaseLLMProvider):
    """LLM provider for Google Cloud Vertex AI REST API.

    Authenticates using Application Default Credentials (ADC) or gcloud CLI token fallback.
    """

    def __init__(
        self,
        model: str,
        options: dict[str, Any] | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize VertexAIProvider with model and cloud configuration options.

        Args:
            model: Vertex AI model name (e.g., 'gemini-2.5-flash').
            options: Optional configuration dictionary containing 'project' and/or 'location'.
            timeout: Request timeout in seconds (default: 30.0).
            http_client: Optional injected httpx.Client.
        """
        super().__init__(model=model, options=options, timeout=timeout, http_client=http_client)

        self.project: str | None = (
            self.options.get("project")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
        )
        self.location: str = (
            self.options.get("location")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or os.getenv("GCP_LOCATION")
            or "us-central1"
        )

    @property
    def name(self) -> str:
        """Canonical provider name."""
        return "vertex"

    def _get_vertex_token_from_gcloud(self) -> str | None:
        """Acquire an ADC token using gcloud CLI subprocess as a fallback.

        In corporate environments using service account impersonation or SSO,
        google.auth.default() might fail to find or refresh credentials directly.
        Running the gcloud CLI invokes the native authentication helper.

        Returns:
            str | None: The access token string if successful, or None otherwise.
        """
        if sys.platform == "win32":
            cmd = shutil.which("gcloud.cmd") or shutil.which("gcloud") or "gcloud.cmd"
        else:
            cmd = shutil.which("gcloud") or "gcloud"

        commands = [
            [cmd, "auth", "application-default", "print-access-token"],
            [cmd, "auth", "print-access-token"],
        ]

        for cmd_args in commands:
            try:
                result = subprocess.run(
                    cmd_args,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0:
                    token = result.stdout.strip()
                    if token:
                        return token
                logger.debug(
                    "Command %s failed with code %d: %s",
                    " ".join(cmd_args),
                    result.returncode,
                    result.stderr.strip(),
                )
            except FileNotFoundError:
                logger.debug("gcloud command not found: %s", cmd)
                return None
            except subprocess.TimeoutExpired:
                logger.warning("gcloud token command timed out: %s", " ".join(cmd_args))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to acquire token via gcloud (%s): %s", " ".join(cmd_args), exc)

        return None

    def _get_token(self) -> str:
        """Acquire an OAuth2 Bearer token using google-auth credentials or gcloud CLI fallback.

        Returns:
            str: Valid OAuth2 access token.

        Raises:
            ValueError: If token acquisition fails via both google-auth and gcloud CLI fallback.
        """
        token: str | None = None

        try:
            import google.auth

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not credentials.valid:
                credentials.refresh(_HttpxAuthRequest(self._http_client))  # type: ignore[no-untyped-call]
            raw_token = getattr(credentials, "token", None)
            if raw_token:
                token = str(raw_token)
        except Exception as exc:  # noqa: BLE001
            logger.debug("google.auth.default token acquisition failed: %s", exc)

        if not token:
            logger.info("Attempting fallback to gcloud CLI for ADC token acquisition.")
            token = self._get_vertex_token_from_gcloud()

        if not token:
            msg = (
                "Failed to acquire Google Cloud ADC token for Vertex AI. "
                "Please run 'gcloud auth application-default login' in your terminal."
            )
            raise ValueError(msg)

        return token

    def _ensure_project(self) -> None:
        """Raise ValueError with actionable advice if project is not configured."""
        if not self.project:
            msg = (
                "Google Cloud project ID is required for Vertex AI. "
                "Provide it via provider_options['project'], set gcp_project in Config, "
                "or set the GOOGLE_CLOUD_PROJECT environment variable."
            )
            raise ValueError(msg)

    def generate_text(self, prompt: str) -> str:
        """Generate text using Google Cloud Vertex AI generateContent REST endpoint.

        Args:
            prompt: Text prompt to submit.

        Returns:
            str: Generated text from the first candidate.

        Raises:
            ValueError: If project ID is missing, token acquisition fails, or response is invalid.
            httpx.HTTPError: If HTTP communication fails.
        """
        self._ensure_project()

        host = (
            "aiplatform.googleapis.com"
            if self.location == "global"
            else f"{self.location}-aiplatform.googleapis.com"
        )
        url = (
            f"https://{host}/v1/projects/{self.project}"
            f"/locations/{self.location}/publishers/google/models/{self.model}:generateContent"
        )
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
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

        text = parts[0].get("text", "")
        return str(text)

    def health_check(self) -> dict[str, Any]:
        """Perform round-trip health check ping to verify Vertex AI connectivity and ADC token.

        Returns:
            dict[str, Any]: Health status dictionary with latency and actionable error advice.
        """
        start = time.perf_counter()
        try:
            self._ensure_project()

            response_text = self.generate_text("ping")
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            return {
                "success": True,
                "duration_ms": duration_ms,
                "message": f"Successfully connected to Vertex AI ({self.model}).",
                "details": {
                    "provider": self.name,
                    "model": self.model,
                    "project": self.project,
                    "location": self.location,
                    "response_snippet": response_text[:100].strip() if response_text else "",
                },
                "actionable_advice": None,
            }
        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            err_msg = str(exc)
            err_lower = err_msg.lower()

            advice = None
            if "project" in err_lower and not self.project:
                advice = (
                    "Please configure Google Cloud project via 'project' option "
                    "or set the 'GOOGLE_CLOUD_PROJECT' environment variable."
                )
            elif "adc" in err_lower or "gcloud auth" in err_lower or "token" in err_lower:
                advice = (
                    "Please run 'gcloud auth application-default login' to authenticate with "
                    "Google Cloud ADC."
                )

            return {
                "success": False,
                "duration_ms": duration_ms,
                "message": err_msg,
                "details": {
                    "provider": self.name,
                    "model": self.model,
                    "project": self.project,
                    "location": self.location,
                },
                "actionable_advice": advice,
            }
