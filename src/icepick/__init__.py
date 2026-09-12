"""Icepick for Snowflake.

Deterministic AST-based query optimizer and anti-pattern linter for Snowflake SQL.
"""

from icepick.config import Config, OptimizerConfig
from icepick.exceptions import IcepickError, ParseError
from icepick.linter import BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.llm import ContextSlicer, LLMClient, SliceContext
from icepick.parser import SQLParser, parse_snowflake_sql
from icepick.patcher import ASTPatcher, SubqueryToCTE

__version__ = "0.1.0"
__all__ = [
    "ASTPatcher",
    "BaseRule",
    "Config",
    "ContextSlicer",
    "DiagnosticIssue",
    "IcepickError",
    "LLMClient",
    "LinterEngine",
    "OptimizerConfig",
    "ParseError",
    "SQLParser",
    "Severity",
    "SliceContext",
    "SubqueryToCTE",
    "__version__",
    "parse_snowflake_sql",
]
