"""Prescription diagnosis engine for Snowflake SQL.

Coordinates AST parsing, static diagnosis rule execution, and generation of
structured, actionable PrescriptionPlan objects according to ADR-0005.
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp

from icepick.config import Config
from icepick.diff.formatter import format_diff
from icepick.linter.base import DiagnosticIssue, Severity
from icepick.linter.engine import LinterEngine
from icepick.patcher.splicer import TextSplicer
from icepick.prescription.models import (
    Prescription,
    PrescriptionAction,
    PrescriptionPlan,
    PrescriptionTarget,
)

logger = logging.getLogger(__name__)

# Rule ID to expected performance impact explanation mapping
RULE_IMPACT_MAP: dict[str, str] = {
    "SNOW-001": "Avoids full table scan by enabling partition pruning",
    "SNOW-002": "Improves execution plan efficiency by unnesting subquery into a set-based join",
    "SNOW-003": "Reduces memory consumption and spill to storage by eliminating redundant sorting",
    "SNOW-004": "Prevents accidental Cartesian products and excessive row generation",
    "SNOW-005": "Minimizes I/O and remote storage reads by reusing scanned data via CTE",
    "SNOW-006": "Eliminates distinct sort overhead when duplicate rows are impossible or acceptable",
    "SNOW-007": "Flattens nested subqueries into CTEs for better readability and optimization",
    "SNOW-008": "Eliminates redundant deduplication sorting in aggregation query; reduces query memory spill.",
    "SNOW-009": "Flattens nested subquery using native QUALIFY clause; improves optimizer efficiency and readability.",
    "SNOW-010": "Avoids redundant CTE inlining re-computations and memory spills; consider TEMPORARY TABLE materialization.",
    "SNOW-011": "Prevents Snowflake optimizer compilation overload from huge literal list; consider ARRAY_CONSTRUCT or temporary table.",
    "SNOW-012": "Avoids unnecessary column projection and memory spill; consider specifying only required columns.",
}

DEFAULT_IMPACT = "Improves query execution performance and resource efficiency"


def create_default_linter_engine(config: Config | None = None) -> LinterEngine:
    """Create and configure a LinterEngine with all standard SNOW rules (001-012).

    Args:
        config: Optional configuration instance.

    Returns:
        LinterEngine: Populated linter engine instance.
    """
    return LinterEngine(config=config)


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
                If None, a default LinterEngine loaded with SNOW-001 through SNOW-011 is created.
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
                    _issue=issue,
                )
            )

        return PrescriptionPlan(
            schema_version="1.0",
            file=file_path,
            issues_count=len(prescriptions),
            prescriptions=prescriptions,
        )

    def _resolve_target_prescriptions_and_issues(
        self,
        sql_text: str,
        plan: PrescriptionPlan | None = None,
        selected_ids: list[str] | None = None,
        filename: str = "",
    ) -> list[tuple[Prescription, DiagnosticIssue]]:
        """Validate selected IDs and pair each target Prescription with its DiagnosticIssue.

        If a prescription was deserialized without an active AST node reference (_issue is None),
        this reconstructs a synthetic DiagnosticIssue using sqlglot parsing with graceful fallback.

        Args:
            sql_text: Original raw SQL query text.
            plan: Pre-diagnosed PrescriptionPlan or None to diagnose dynamically.
            selected_ids: Optional list of prescription IDs to filter by.
            filename: Target filename label for diagnosis file path.

        Returns:
            list[tuple[Prescription, DiagnosticIssue]]: Pairs of target Prescription and DiagnosticIssue.

        Raises:
            ValueError: If selected_ids contains invalid prescription ID(s) not present in plan.
        """
        # 1. Resolve diagnostic plan
        if plan is None:
            plan = self.diagnose(sql_text, file_path=filename)

        valid_ids = [rx.id for rx in plan.prescriptions]
        target_rxs: list[Prescription]

        # 2. Validate selected_ids and filter target prescriptions
        if selected_ids is not None:
            unknown_ids = list(dict.fromkeys(rid for rid in selected_ids if rid not in valid_ids))
            if unknown_ids:
                avail_str = ", ".join(valid_ids) if valid_ids else "(none)"
                raise ValueError(
                    f"Invalid prescription ID(s): {', '.join(unknown_ids)}. Available IDs: {avail_str}"
                )

            selected_set = set(selected_ids)
            target_rxs = [rx for rx in plan.prescriptions if rx.id in selected_set]
        else:
            target_rxs = plan.prescriptions

        if not target_rxs:
            return []

        # 3. Collect DiagnosticIssue objects (with fallback if deserialized without AST reference)
        pairs: list[tuple[Prescription, DiagnosticIssue]] = []

        for rx in target_rxs:
            if rx._issue is not None:
                pairs.append((rx, rx._issue))
            else:
                # Fallback reconstruction when Prescription was deserialized from JSON
                target_node: exp.Expression
                try:
                    parsed_node = sqlglot.parse_one(rx.original_sql, dialect=self.dialect)
                    target_node = (
                        parsed_node
                        if isinstance(parsed_node, exp.Expression)
                        else exp.var(rx.original_sql)
                    )
                except (sqlglot.errors.ParseError, sqlglot.errors.SqlglotError, ValueError) as err:
                    logger.debug(
                        "Failed to parse fallback original_sql '%s': %s", rx.original_sql, err
                    )
                    target_node = exp.var(rx.original_sql)

                suggested_rep: exp.Expression | None = None
                if rx.suggested_sql is not None:
                    try:
                        parsed_rep = sqlglot.parse_one(rx.suggested_sql, dialect=self.dialect)
                        if isinstance(parsed_rep, exp.Expression):
                            suggested_rep = parsed_rep
                    except (
                        sqlglot.errors.ParseError,
                        sqlglot.errors.SqlglotError,
                        ValueError,
                    ) as err:
                        logger.debug(
                            "Failed to parse fallback suggested_sql '%s': %s", rx.suggested_sql, err
                        )

                line_num = rx.target.line_range[0] if rx.target.line_range else None
                issue = DiagnosticIssue(
                    rule_id=rx.rule_id,
                    rule_name=rx.rule_id,
                    severity=rx.severity,
                    description=rx.rationale,
                    target_node=target_node,
                    snippet=rx.original_sql,
                    line_number=line_num,
                    suggested_replacement=suggested_rep,
                    requires_llm=(
                        rx.suggested_sql is None and rx.action != PrescriptionAction.DELETE
                    ),
                )
                pairs.append((rx, issue))

        return pairs

    def generate_diff(
        self,
        sql_text: str,
        plan: PrescriptionPlan | None = None,
        selected_ids: list[str] | None = None,
        filename: str = "query.sql",
    ) -> str:
        """Generate a minimal Unified Diff applying selected (or all) prescriptions.

        Uses targeted text splicing (TextSplicer) to surgically modify only the code
        prescribed by selected_ids, strictly preserving all formatting, comments,
        and untouched parts of the raw SQL text.

        Args:
            sql_text: Original raw SQL query text.
            plan: Pre-diagnosed PrescriptionPlan. If None, diagnose() is invoked automatically.
            selected_ids: Optional list of prescription IDs to apply (e.g. ['RX-001', 'RX-003']).
                If None, all diagnosed prescriptions are applied.
            filename: Target filename label used in Unified Diff headers (default: 'query.sql').

        Returns:
            str: Standard Unified Diff string representing surgical modifications,
                or an empty string if there are no changes or no matching prescriptions.

        Raises:
            ValueError: If selected_ids contains invalid prescription ID(s) not present in plan.
        """
        pairs = self._resolve_target_prescriptions_and_issues(
            sql_text, plan=plan, selected_ids=selected_ids, filename=filename
        )
        if not pairs:
            return ""

        splicer = TextSplicer(dialect=self.dialect)
        issues_to_apply = [issue for _, issue in pairs]
        modified_sql, _ = splicer.splice_all(sql_text, issues_to_apply)
        return format_diff(sql_text, modified_sql, filename=filename, normalize=False)

    def apply_fixes(
        self,
        sql_text: str,
        plan: PrescriptionPlan | None = None,
        selected_ids: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Surgically apply selected (or all) prescriptions directly to raw SQL text.

        Uses TextSplicer to replace or delete diagnosed anti-patterns directly within
        the raw SQL text, strictly preserving comments, indentation, casing, and all
        unrelated code fragments according to ADR-0005.

        Args:
            sql_text: Original raw SQL query text to apply fixes to.
            plan: Pre-diagnosed PrescriptionPlan. If None, diagnose() is invoked automatically.
            selected_ids: Optional list of prescription IDs to apply (e.g. ['RX-001', 'RX-003']).
                If None, all diagnosed prescriptions are applied.

        Returns:
            tuple[str, list[str]]: A tuple containing:
                - modified_sql: The updated SQL text with fixes surgically applied.
                - applied_ids: List of prescription IDs that were successfully spliced.

        Raises:
            ValueError: If selected_ids contains invalid prescription ID(s) not present in plan.

        Why:
            Decoupling diff generation and actual in-place fix application allows
            flexible execution modes (e.g. CLI interactive prompting, dry-run, partial
            batch fixes) while guaranteeing that the exact same splicing engine and
            AST-level surgical precision are shared across both operations.
        """
        pairs = self._resolve_target_prescriptions_and_issues(
            sql_text, plan=plan, selected_ids=selected_ids
        )
        if not pairs:
            return (sql_text, [])

        splicer = TextSplicer(dialect=self.dialect)
        issues_to_apply = [issue for _, issue in pairs]
        modified_sql, applied_issues = splicer.splice_all(sql_text, issues_to_apply)

        applied_issue_ids = {id(issue) for issue in applied_issues}
        applied_ids = [rx.id for rx, issue in pairs if id(issue) in applied_issue_ids]

        return (modified_sql, applied_ids)
