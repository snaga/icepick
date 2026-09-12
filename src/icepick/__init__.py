"""Icepick for Snowflake.

Deterministic AST-based query optimizer and anti-pattern linter for Snowflake SQL.
"""

from icepick.config import Config, OptimizerConfig
from icepick.exceptions import IcepickError, ParseError
from icepick.parser import SQLParser, parse_snowflake_sql

__version__ = "0.1.0"
__all__ = [
    "Config",
    "IcepickError",
    "OptimizerConfig",
    "ParseError",
    "SQLParser",
    "__version__",
    "parse_snowflake_sql",
]
