"""SNOW-008: Redundant DISTINCT Rule.

Detects unnecessary DISTINCT clauses in SELECT statements that already perform
grouping (GROUP BY) or aggregation (e.g. COUNT, SUM, AVG). In relational
semantics, queries with GROUP BY or top-level aggregate expressions already
guarantee unique grouping rows, making DISTINCT entirely redundant while
triggering an expensive duplicate-elimination sort or hash operation.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class RedundantDistinctRule(BaseRule):
    """Rule SNOW-008: Detects redundant DISTINCT in SELECT with GROUP BY or aggregation."""

    rule_id: str = "SNOW-008"
    rule_name: str = "Redundant DISTINCT with GROUP BY or Aggregation"
    severity: Severity = Severity.LOW
    description: str = (
        "Redundant DISTINCT in SELECT with GROUP BY or aggregate functions; "
        "triggers unnecessary sort for deduplication."
    )

    def _has_aggregate(self, select: exp.Select) -> bool:
        """Check if the SELECT projection contains top-level aggregate functions.

        Aggregates nested within subqueries (such as scalar subqueries in the
        SELECT list) do not aggregate the current SELECT scope and are excluded.
        Aggregate functions inside window functions (e.g. COUNT(*) OVER (...))
        also do not collapse rows without GROUP BY and are excluded.

        Args:
            select: exp.Select expression node.

        Returns:
            bool: True if any projection expression contains an aggregate function
                  belonging directly to this SELECT block.
        """
        for expr in select.expressions:
            for agg in expr.find_all(exp.AggFunc):
                if agg.find_ancestor(exp.Select) != select:
                    continue
                # ウィンドウ関数内の集計は行を集約しないため除外
                window = agg.find_ancestor(exp.Window)
                if window is not None and window.find_ancestor(exp.Select) == select:
                    continue
                return True
        return False

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for SELECT statements with redundant DISTINCT clauses.

        Args:
            ast: Root or subtree AST expression.

        Returns:
            list[DiagnosticIssue]: List of detected redundant DISTINCT issues.
        """
        issues: list[DiagnosticIssue] = []

        for select in ast.find_all(exp.Select):
            distinct_node = select.args.get("distinct")
            if not isinstance(distinct_node, exp.Distinct):
                continue

            # Condition a: GROUP BY clause exists
            has_group_by = select.args.get("group") is not None

            # Condition b: Projection contains aggregate functions
            has_agg = self._has_aggregate(select)

            if not (has_group_by or has_agg):
                continue

            line_num: int | None = None
            meta_dict: Any = getattr(distinct_node, "meta", None)
            if isinstance(meta_dict, dict):
                line_num = meta_dict.get("line")
            if line_num is None:
                meta_select: Any = getattr(select, "meta", None)
                if isinstance(meta_select, dict):
                    line_num = meta_select.get("line")

            snippet = distinct_node.sql(dialect="snowflake") or "DISTINCT"

            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=self.description,
                    target_node=distinct_node,
                    snippet=snippet,
                    line_number=line_num,
                    suggested_replacement=None,  # Indicates deletion (pop)
                    requires_llm=False,
                )
            )

        return issues
