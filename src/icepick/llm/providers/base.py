"""Base class for pluggable LLM providers."""

from __future__ import annotations

import abc
from typing import Any

import httpx


class BaseLLMProvider(abc.ABC):
    """Abstract base class defining the pluggable LLM provider interface.

    Attributes:
        model: LLM model identifier (e.g., 'gemini-2.5-flash').
        options: Provider-specific configuration dictionary.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        options: dict[str, Any] | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize the base LLM provider.

        Args:
            model: Model identifier.
            options: Optional dictionary of provider-specific options.
            timeout: Request timeout in seconds (default: 30.0).
            http_client: Optional injected httpx.Client for testing or connection pooling.
        """
        self.model = model
        self.options: dict[str, Any] = options.copy() if options else {}
        self.timeout = timeout
        self._http_client = http_client

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the canonical provider name (e.g. 'gemini', 'vertex')."""
        ...

    @abc.abstractmethod
    def generate_text(self, prompt: str) -> str:
        """Generate text completion from the provider REST API.

        Args:
            prompt: Text prompt to submit to the model.

        Returns:
            str: Generated text response.

        Raises:
            AuthenticationError: If authentication credentials cannot be resolved or are invalid.
            httpx.HTTPError: If HTTP communication fails.
            ValueError: If the response is malformed or missing expected content.
        """
        ...

    @abc.abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Perform a proactive health check ping to verify connectivity and credentials.

        Returns:
            dict[str, Any]: Health status dictionary containing keys:
                - success (bool): True if health check passed, False otherwise.
                - duration_ms (float): Round-trip latency in milliseconds.
                - message (str): Summary or error description.
                - details (dict[str, Any]): Metadata about the connection.
                - actionable_advice (str | None): Guidance to fix any detected issue.
        """
        ...

    def _get_client(self) -> tuple[httpx.Client, bool]:
        """Return an HTTP client and a boolean flag indicating if the caller should close it.

        Returns:
            tuple[httpx.Client, bool]: Injected client (False) or freshly created client (True).
        """
        if self._http_client is not None:
            return self._http_client, False
        return httpx.Client(timeout=self.timeout), True
