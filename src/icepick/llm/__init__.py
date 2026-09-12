"""LLM slicing and client integration module.

Provides context slicing for minimal prompt generation and LLM client interfaces.
"""

from icepick.llm.client import LLMClient
from icepick.llm.slicer import ContextSlicer, SliceContext

__all__ = [
    "ContextSlicer",
    "LLMClient",
    "SliceContext",
]
