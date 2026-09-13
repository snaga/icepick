"""Prescription diagnosis engine for Snowflake SQL.

Coordinates AST parsing, static diagnosis rule execution, and generation of
structured, actionable PrescriptionPlan objects according to ADR-0005.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from icepick.config import Config
from icepick.linter.base import DiagnosticIssue, Severity
from icepick.linter.engine import LinterEngine
from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.linter.rules.snow_002_correlated import CorrelatedSubqueryRule
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.linter.rules.snow_004_implicit_cross_join import ImplicitCrossJoinRule
from icepick.linter.rules.snow_005_duplicate_scan import DuplicateTableScanRule
from icepick.linter.rules.snow_006_union import UnionToUnionAllRule
from icepick.linter.rules.snow_007_nested_subquery import NestedSubqueryRule
from icepick.prescription.models import (
    Prescription,
    PrescriptionAction,
    PrescriptionPlan,
    PrescriptionTarget,
)

# Rule ID to expected performance impact explanation mapping
RULE_IMPACT_MAP: dict[str, str] = {
    "SNOW-001": "Avoids full table scan by enabling partition pruning",
    "SNOW-002": "Improves execution plan efficiency by unnesting subquery into a set-based join",
    "SNOW-003": "Reduces memory consumption and spill to storage by eliminating redundant sorting",
    "SNOW-004": "Prevents accidental Cartesian products and excessive row generation",
    "SNOW-005": "Minimizes I/O and remote storage reads by reusing scanned data via CTE",
    "SNOW-006": "Eliminates distinct sort overhead when duplicate rows are impossible or acceptable",
    "SNOW-007": "Flattens nested subqueries into CTEs for better readability and optimization",
}

DEFAULT_IMPACT = "Improves query execution performance and resource efficiency"


def create_default_linter_engine(config: Config | None = None) -> LinterEngine:
    """Create and configure a LinterEngine with all standard SNOW rules (001-007).

    Args:
        config: Optional configuration instance.

    Returns:
        LinterEngine: Populated linter engine instance.
    """
    engine = LinterEngine(config=config)
    engine.register_rule(NonSargableRule())
    engine.register_rule(CorrelatedSubqueryRule())
    engine.register_rule(RedundantSortRule())
    engine.register_rule(ImplicitCrossJoinRule())
    engine.register_rule(DuplicateTableScanRule())
    engine.register_rule(UnionToUnionAllRule())
    engine.register_rule(NestedSubqueryRule())
    return engine


class PrescriptionEngine:
    """Generates structured prescription plans from Snowflake SQL queries.

    Transforms diagnostic issues detected on the AST into discrete, actionable,
    and verifiable prescriptions (e.g., RX-001, RX-002).
    """

    def __init__(
        self,
        linter_engine: LinterEngine | None = None,
        dialect: str = "snowflake",
    ) -> None:
        """Initialize PrescriptionEngine.

        Args:
            linter_engine: Underlying LinterEngine for rule evaluation.
                If None, a default LinterEngine loaded with SNOW-001 through SNOW-007 is created.
            dialect: SQL dialect for parsing and generating code snippets (default: 'snowflake').
        """
        self.linter_engine: LinterEngine = (
            linter_engine if linter_engine is not None else create_default_linter_engine()
        )
        self.dialect = dialect

    def diagnose(self, sql_text: str, file_path: str = "") -> PrescriptionPlan:
        """Diagnose SQL text and construct a structured PrescriptionPlan.

        Args:
            sql_text: Raw SQL query string to diagnose.
            file_path: Optional path to the SQL file being diagnosed.

        Returns:
            PrescriptionPlan: Diagnostic plan containing all generated prescriptions.

        Raises:
            sqlglot.errors.ParseError: If the SQL text cannot be parsed by sqlglot.
        """
        # 1. Parse AST with dialect (raises sqlglot.errors.ParseError on syntax error)
        ast = sqlglot.parse_one(sql_text, dialect=self.dialect)
        if not isinstance(ast, exp.Expression):
            return PrescriptionPlan(file=file_path, issues_count=0, prescriptions=[])

        # 2. Run diagnostic rules
        issues: list[DiagnosticIssue] = self.linter_engine.diagnose(ast)

        # 3. Transform DiagnosticIssue items into Prescriptions
        prescriptions: list[Prescription] = []
        for i, issue in enumerate(issues):
            rx_id = f"RX-{i + 1:03d}"

            # CTE identification: look up ancestor CTE node
            cte_node = issue.target_node.find_ancestor(exp.CTE)
            cte_name: str | None = None
            if cte_node is not None:
                alias_val = cte_node.alias
                cte_name = str(alias_val) if alias_val else None

            # Node type identification
            node_type = issue.target_node.__class__.__name__

            # Line range identification
            line_range: tuple[int, int] | None = None
            if issue.line_number is not None:
                line_range = (issue.line_number, issue.line_number)

            target = PrescriptionTarget(
                cte=cte_name,
                node_type=node_type,
                line_range=line_range,
            )

            # Action and suggested replacement determination
            action: PrescriptionAction
            suggested_sql: str | None = None

            if issue.is_deletable:
                action = PrescriptionAction.DELETE
                suggested_sql = None
            elif issue.is_replaceable:
                action = PrescriptionAction.REPLACE
                if issue.suggested_replacement is not None:
                    suggested_sql = issue.suggested_replacement.sql(dialect=self.dialect)
            else:
                action = PrescriptionAction.REPLACE
                suggested_sql = None

            # Severity normalization
            severity = (
                issue.severity
                if isinstance(issue.severity, Severity)
                else Severity(str(issue.severity).upper())
            )

            # Expected impact resolution
            expected_impact = RULE_IMPACT_MAP.get(issue.rule_id, DEFAULT_IMPACT)

            # Original SQL snippet
            original_sql = issue.snippet or issue.target_node.sql(dialect=self.dialect)

            prescriptions.append(
                Prescription(
                    id=rx_id,
                    rule_id=issue.rule_id,
                    severity=severity,
                    target=target,
                    action=action,
                    original_sql=original_sql,
                    suggested_sql=suggested_sql,
                    rationale=issue.description,
                    expected_impact=expected_impact,
                )
            )

        return PrescriptionPlan(
            schema_version="1.0",
            file=file_path,
            issues_count=len(prescriptions),
            prescriptions=prescriptions,
        )
