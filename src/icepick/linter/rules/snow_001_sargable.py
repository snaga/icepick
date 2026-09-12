"""SNOW-001: Non-Sargable Predicate Rule.

Detects columns wrapped in date/cast functions within equality comparisons
(e.g. DATE(col) = '2024-01-01'), which disables Snowflake partition pruning.
Provides suggested range condition replacements.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity

DATE_LITERAL_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?$"
)


def _is_date_literal(node: exp.Expr | None) -> bool:
    """Check if node is a string literal representing a calendar date or timestamp."""
    if isinstance(node, exp.Literal) and node.is_string:
        val = str(node.this).strip()
        return bool(DATE_LITERAL_REGEX.match(val))
    return False


def _extract_column_from_func(node: exp.Expr | None) -> exp.Column | None:
    """Extract the column expression if node wraps a column in a date function or cast.

    Supported patterns:
        - DATE(col), TO_DATE(col) (parsed as TsOrDsToDate, Date, or ToDate)
        - CAST(col AS DATE) (parsed as Cast)
        - DATE_TRUNC('day', col) (parsed as DateTrunc or Anonymous)
        - Anonymous date functions (e.g. TRY_TO_DATE(col))
    """
    if isinstance(node, (exp.TsOrDsToDate, exp.Date, exp.DateTrunc, exp.TimestampTrunc)):
        if isinstance(node.this, exp.Column):
            return node.this
        return node.find(exp.Column)

    if isinstance(node, exp.Cast):
        to_type = node.to
        is_date_cast = (
            isinstance(to_type, exp.DataType) and to_type.this == exp.DataType.Type.DATE
        ) or str(to_type).upper() == "DATE"

        if is_date_cast:
            if isinstance(node.this, exp.Column):
                return node.this
            return node.find(exp.Column)

    if isinstance(node, exp.Anonymous):
        func_name = node.name.upper()
        if func_name in {"DATE", "TO_DATE", "TRY_TO_DATE", "DATE_TRUNC"}:
            return node.find(exp.Column)

    return None


class NonSargableRule(BaseRule):
    """Rule SNOW-001: Detects non-sargable function calls on columns in equality predicates."""

    rule_id: str = "SNOW-001"
    rule_name: str = "Non-Sargable Predicate"
    severity: Severity = Severity.HIGH
    description: str = (
        "Column wrapped in date/cast function disables partition pruning; "
        "rewrite to range predicate."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for non-sargable equality comparisons against date literals.

        Args:
            ast: Root or subtree AST expression.

        Returns:
            list[DiagnosticIssue]: List of issues with suggested range rewrites.
        """
        issues: list[DiagnosticIssue] = []

        for eq in ast.find_all(exp.EQ):
            col_node: exp.Column | None = None
            lit_node: exp.Literal | None = None

            # Pattern 1: FUNC(col) = '2024-01-01'
            if _is_date_literal(eq.right):
                col_node = _extract_column_from_func(eq.left)
                if col_node is not None and isinstance(eq.right, exp.Literal):
                    lit_node = eq.right

            # Pattern 2: '2024-01-01' = FUNC(col) (reversed)
            elif _is_date_literal(eq.left):
                col_node = _extract_column_from_func(eq.right)
                if col_node is not None and isinstance(eq.left, exp.Literal):
                    lit_node = eq.left

            if col_node is not None and lit_node is not None:
                col_sql = col_node.sql(dialect="snowflake")
                date_str = str(lit_node.this).strip()

                replacement_sql = (
                    f"{col_sql} >= '{date_str}' AND {col_sql} < DATEADD(day, 1, '{date_str}')"
                )
                suggested_replacement = sqlglot.parse_one(replacement_sql, read="snowflake")
                if not isinstance(suggested_replacement, exp.Expression):
                    continue

                line_num: int | None = None
                meta_dict: Any = getattr(eq, "meta", None)
                if isinstance(meta_dict, dict):
                    line_num = meta_dict.get("line")

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=(
                            f"Column '{col_sql}' is wrapped in a date/cast function in an equality "
                            f"predicate, which disables partition pruning. "
                            f"Rewrite to a sargable range condition."
                        ),
                        target_node=eq,
                        snippet=eq.sql(dialect="snowflake"),
                        line_number=line_num,
                        suggested_replacement=suggested_replacement,
                        requires_llm=False,
                    )
                )

        return issues
