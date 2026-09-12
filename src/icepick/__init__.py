"""Icepick for Snowflake.

Deterministic AST-based query optimizer and anti-pattern linter for Snowflake SQL.
"""

__version__ = "0.1.0"

from icepick.cli import app
from icepick.config import Config, OptimizerConfig
from icepick.exceptions import IcepickError, ParseError
from icepick.linter import BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.llm import ContextSlicer, LLMClient, SliceContext
from icepick.parser import SQLParser, parse_snowflake_sql
from icepick.patcher import ASTPatcher, SubqueryToCTE
from icepick.verifier import EquivalenceVerifier, VerificationResult

__all__ = [
    "ASTPatcher",
    "BaseRule",
    "Config",
    "ContextSlicer",
    "DiagnosticIssue",
    "EquivalenceVerifier",
    "IcepickError",
    "LLMClient",
    "LinterEngine",
    "OptimizerConfig",
    "ParseError",
    "SQLParser",
    "Severity",
    "SliceContext",
    "SubqueryToCTE",
    "VerificationResult",
    "__version__",
    "app",
    "parse_snowflake_sql",
]
