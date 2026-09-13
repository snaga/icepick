"""Icepick for Snowflake.

Deterministic AST-based query optimizer and anti-pattern linter for Snowflake SQL.
"""

__version__ = "0.1.0"

from icepick.agent_context import get_agent_context
from icepick.cli import app
from icepick.config import (
    Config,
    ConfigResolver,
    ConfigSource,
    OptimizerConfig,
    RuntimeConfigItem,
    RuntimeConfigSummary,
    mask_sensitive,
)
from icepick.exceptions import AuthenticationError, IcepickError, ParseError
from icepick.linter import BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.llm import ContextSlicer, LLMClient, SliceContext
from icepick.parser import SQLParser, parse_snowflake_sql
from icepick.patcher import ASTPatcher, SubqueryToCTE
from icepick.verifier import EquivalenceVerifier, VerificationResult

__all__ = [
    "ASTPatcher",
    "AuthenticationError",
    "BaseRule",
    "Config",
    "ConfigResolver",
    "ConfigSource",
    "ContextSlicer",
    "DiagnosticIssue",
    "EquivalenceVerifier",
    "IcepickError",
    "LLMClient",
    "LinterEngine",
    "OptimizerConfig",
    "ParseError",
    "RuntimeConfigItem",
    "RuntimeConfigSummary",
    "SQLParser",
    "Severity",
    "SliceContext",
    "SubqueryToCTE",
    "VerificationResult",
    "__version__",
    "app",
    "get_agent_context",
    "mask_sensitive",
    "parse_snowflake_sql",
]
