"""SNOW-010: CTE Multiple References (Materialization Warning) Rule.

Detects Common Table Expressions (CTEs) referenced 3 or more times within the same query.
In Snowflake, CTEs are non-materialized views by default and are inlined at each reference
point. Multiple references can cause redundant scans, re-computations, and severe memory spills.
Recommends materializing heavily reused CTEs into a TEMPORARY TABLE.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class CteMultiReferenceRule(BaseRule):
    """Rule SNOW-010: Detects CTEs referenced 3 or more times, warning of inlining overhead."""

    rule_id: str = "SNOW-010"
    rule_name: str = "CTE Multiple References (Materialization Warning)"
    severity: Severity = Severity.LOW
    description: str = (
        "CTE is referenced multiple times; Snowflake inlines CTEs as views, "
        "which may cause redundant re-computations and memory spills; "
        "consider materializing into a TEMPORARY TABLE."
    )
    THRESHOLD: int = 3

    def __init__(self, threshold: int = THRESHOLD) -> None:
        """Initialize CteMultiReferenceRule.

        Args:
            threshold: Minimum number of references required to trigger warning (default: 3).
        """
        self.threshold = threshold

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST and detect CTEs referenced at or above the threshold.

        Args:
            ast: Root AST expression to inspect.

        Returns:
            list[DiagnosticIssue]: List of detected issues for over-referenced CTEs.
        """
        with_clause = ast.args.get("with_")
        if not with_clause or not hasattr(with_clause, "expressions"):
            return []

        all_tables = list(ast.find_all(exp.Table))
        issues: list[DiagnosticIssue] = []

        for cte in with_clause.expressions:
            if not isinstance(cte, exp.CTE):
                continue

            cte_alias = cte.alias
            if not cte_alias:
                continue

            cte_name = cte_alias.lower()
            alias_node = cte.args.get("alias")

            # Count Table nodes referencing this CTE name, excluding the CTE's own alias definition
            ref_count = sum(
                1
                for table in all_tables
                if table.name
                and table.name.lower() == cte_name
                and (alias_node is None or table.find_ancestor(exp.TableAlias) != alias_node)
            )

            if ref_count >= self.threshold:
                line_num: int | None = None
                meta_dict: Any = getattr(cte, "meta", None)
                if isinstance(meta_dict, dict):
                    line_num = meta_dict.get("line")

                message = (
                    f"CTE '{cte_alias}' is referenced {ref_count} times. "
                    "Snowflake inlines CTEs as views, which may cause redundant re-computations and "
                    "memory spills; consider materializing into a TEMPORARY TABLE."
                )

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=message,
                        target_node=cte,
                        snippet=cte.sql(dialect="snowflake"),
                        line_number=line_num,
                        suggested_replacement=None,
                        requires_llm=True,
                    )
                )

        return issues
