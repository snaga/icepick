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
from icepick.exceptions import IcepickError, ParseError
from icepick.linter import BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.parser import SQLParser, parse_snowflake_sql
from icepick.patcher import ASTPatcher, SubqueryToCTE
from icepick.prescription import (
    Prescription,
    PrescriptionAction,
    PrescriptionEngine,
    PrescriptionPlan,
    PrescriptionTarget,
)
from icepick.verifier import (
    EquivalenceVerifier,
    VerificationResult,
    generate_verification_sql,
)

__all__ = [
    "ASTPatcher",
    "BaseRule",
    "Config",
    "ConfigResolver",
    "ConfigSource",
    "DiagnosticIssue",
    "EquivalenceVerifier",
    "IcepickError",
    "LinterEngine",
    "OptimizerConfig",
    "ParseError",
    "Prescription",
    "PrescriptionAction",
    "PrescriptionEngine",
    "PrescriptionPlan",
    "PrescriptionTarget",
    "RuntimeConfigItem",
    "RuntimeConfigSummary",
    "SQLParser",
    "Severity",
    "SubqueryToCTE",
    "VerificationResult",
    "__version__",
    "app",
    "generate_verification_sql",
    "get_agent_context",
    "mask_sensitive",
    "parse_snowflake_sql",
]
