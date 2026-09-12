"""Icepick for Snowflake.

Deterministic AST-based query optimizer and anti-pattern linter for Snowflake SQL.
"""

from icepick.config import Config, OptimizerConfig
from icepick.exceptions import IcepickError, ParseError
from icepick.linter import BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.parser import SQLParser, parse_snowflake_sql

__version__ = "0.1.0"
__all__ = [
    "BaseRule",
    "Config",
    "DiagnosticIssue",
    "IcepickError",
    "LinterEngine",
    "OptimizerConfig",
    "ParseError",
    "SQLParser",
    "Severity",
    "__version__",
    "parse_snowflake_sql",
]
