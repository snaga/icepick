"""SNOW-011: Huge IN-List Optimizer Overload Rule.

Detects IN clauses containing excessively large lists of literal values (500+ items).
Huge IN lists overload Snowflake query compilation, increase parse/compile time,
and consume execution memory. Recommends rewriting to ARRAY_CONSTRUCT/ARRAY_CONTAINS
or joining with a temporary table.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class HugeInListRule(BaseRule):
    """Rule SNOW-011: Detects huge IN lists overloading Snowflake query compilation."""

    rule_id: str = "SNOW-011"
    rule_name: str = "Huge IN-List Optimizer Overload"
    severity: Severity = Severity.MEDIUM
    description: str = (
        "Huge IN-list overloads Snowflake query compilation and memory; "
        "consider ARRAY_CONSTRUCT/ARRAY_CONTAINS or joining with a temporary table."
    )
    THRESHOLD: int = 500

    def __init__(self, threshold: int = THRESHOLD) -> None:
        """Initialize HugeInListRule.

        Args:
            threshold: Minimum number of items in IN-list required to trigger warning (default: 500).
        """
        self.threshold = threshold

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for IN expressions with excessively large argument lists.

        Args:
            ast: Root AST expression to inspect.

        Returns:
            list[DiagnosticIssue]: List of detected huge IN-list issues.
        """
        issues: list[DiagnosticIssue] = []

        for in_node in ast.find_all(exp.In):
            # Skip subquery IN clauses (e.g. IN (SELECT ...))
            if in_node.args.get("query") is not None:
                continue
            if not in_node.expressions:
                continue
            if any(isinstance(expr, (exp.Select, exp.Subquery)) for expr in in_node.expressions):
                continue

            count = len(in_node.expressions)
            if count >= self.threshold:
                line_num: int | None = None
                meta_dict: Any = getattr(in_node, "meta", None)
                if isinstance(meta_dict, dict):
                    line_num = meta_dict.get("line")

                message = (
                    f"Huge IN-list with {count} items overloads Snowflake query compilation and "
                    "memory; consider ARRAY_CONSTRUCT/ARRAY_CONTAINS or joining with a temporary table."
                )

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=message,
                        target_node=in_node,
                        snippet=in_node.sql(dialect="snowflake"),
                        line_number=line_num,
                        suggested_replacement=None,
                        requires_llm=True,
                    )
                )

        return issues
