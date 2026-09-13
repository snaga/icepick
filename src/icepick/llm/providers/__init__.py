"""Pluggable LLM provider infrastructure and factory functions."""

from __future__ import annotations

from typing import Any

import httpx

from icepick.llm.providers.base import BaseLLMProvider
from icepick.llm.providers.gemini import GeminiProvider
from icepick.llm.providers.vertex import VertexAIProvider

__all__ = [
    "PROVIDER_REGISTRY",
    "BaseLLMProvider",
    "GeminiProvider",
    "VertexAIProvider",
    "create_provider",
    "get_provider_class",
    "register_provider",
]

PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "vertex": VertexAIProvider,
    "vertex_ai": VertexAIProvider,
    "vertexai": VertexAIProvider,
}


def register_provider(name: str, provider_cls: type[BaseLLMProvider]) -> None:
    """Register a custom LLM provider class under the given name.

    Args:
        name: Provider identifier (case-insensitive).
        provider_cls: Provider class implementing BaseLLMProvider.
    """
    key = name.strip().lower()
    PROVIDER_REGISTRY[key] = provider_cls


def get_provider_class(name: str) -> type[BaseLLMProvider]:
    """Retrieve the provider class for a given provider identifier.

    Args:
        name: Provider identifier (e.g. 'gemini', 'vertex').

    Returns:
        type[BaseLLMProvider]: The corresponding provider class.

    Raises:
        ValueError: If provider is unknown, includes a list of available providers.
    """
    key = name.strip().lower()
    if key in PROVIDER_REGISTRY:
        return PROVIDER_REGISTRY[key]

    available = sorted(set(PROVIDER_REGISTRY.keys()))
    available_str = ", ".join(repr(p) for p in available)
    msg = f"Unsupported LLM provider: '{name}'. Available providers are: {available_str}."
    raise ValueError(msg)


def create_provider(
    name: str,
    model: str,
    options: dict[str, Any] | None = None,
    timeout: float = 30.0,
    http_client: httpx.Client | None = None,
) -> BaseLLMProvider:
    """Instantiate a configured LLM provider.

    Args:
        name: Provider identifier (e.g., 'gemini', 'vertex').
        model: Model identifier.
        options: Provider-specific options dictionary.
        timeout: HTTP request timeout in seconds (default: 30.0).
        http_client: Optional injected httpx.Client.

    Returns:
        BaseLLMProvider: Initialized provider instance.

    Raises:
        ValueError: If provider name is unsupported.
    """
    provider_cls = get_provider_class(name)
    return provider_cls(
        model=model,
        options=options,
        timeout=timeout,
        http_client=http_client,
    )
