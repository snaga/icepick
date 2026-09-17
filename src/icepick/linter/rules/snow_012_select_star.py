"""SNOW-012: Wildcard SELECT * in Pipeline or Join Rule.

Detects wildcard SELECT * or table.* projections in intermediate CTEs, queries with
JOINs, or SELECT DISTINCT. Projecting all columns through pipelines or joins prevents
Snowflake micro-partition column pruning, increases network transfer and memory footprint,
and significantly elevates memory spill risk during hash joins or sorting.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class SelectStarRule(BaseRule):
    """Rule SNOW-012: Detects wildcard SELECT * in intermediate CTEs, JOINs, or DISTINCT."""

    rule_id: str = "SNOW-012"
    rule_name: str = "Wildcard SELECT * in Pipeline or Join"
    severity: Severity = Severity.LOW
    description: str = (
        "Wildcard 'SELECT *' in intermediate CTE, JOIN, or DISTINCT projects unnecessary columns; "
        "prevents column pruning and increases memory spill risk."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for wildcard SELECT * or table.* in CTEs, JOINs, or DISTINCT.

        Args:
            ast: Root AST expression to inspect.

        Returns:
            list[DiagnosticIssue]: List of detected wildcard projection issues.
        """
        issues: list[DiagnosticIssue] = []

        for select in ast.find_all(exp.Select):
            # Extract wildcard projections directly under this SELECT
            star_nodes: list[exp.Expression] = []
            for expr in select.expressions:
                # Exclusion guard: aggregate functions like COUNT(*) or COUNT_IF(*)
                if isinstance(expr, exp.AggFunc) or expr.find_ancestor(exp.AggFunc) is not None:
                    continue

                if isinstance(expr, exp.Star):
                    star_nodes.append(expr)
                elif isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star):
                    star_nodes.append(expr)

            if not star_nodes:
                continue

            # High-risk trigger conditions:
            # 1. Inside an intermediate CTE definition
            # 2. Query with JOIN clause
            # 3. SELECT DISTINCT
            # Standalone root SELECT * without JOIN/CTE/DISTINCT is excluded to avoid noise.
            cte = select.find_ancestor(exp.CTE)
            has_join = bool(select.args.get("joins"))
            has_distinct = select.args.get("distinct") is not None

            if cte is None and not has_join and not has_distinct:
                continue

            # Construct context-specific advice message
            if cte is not None:
                message = (
                    f"Wildcard 'SELECT *' in CTE '{cte.alias}' projects unnecessary columns; "
                    "prevents column pruning and increases memory spill risk. Consider specifying only required columns."
                )
            elif has_distinct:
                message = (
                    "Wildcard 'SELECT DISTINCT *' forces deduplication sort over all columns; "
                    "causes heavy memory spills. Consider specifying only required columns."
                )
            else:
                message = (
                    "Wildcard 'SELECT *' in query with JOIN projects all columns from multiple tables; "
                    "increases memory footprint and network transfer. Consider specifying only required columns."
                )

            for star_node in star_nodes:
                line_num: int | None = None
                star_meta: Any = getattr(star_node, "meta", None)
                if isinstance(star_meta, dict):
                    line_num = star_meta.get("line")
                if line_num is None:
                    select_meta: Any = getattr(select, "meta", None)
                    if isinstance(select_meta, dict):
                        line_num = select_meta.get("line")

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=message,
                        target_node=star_node,
                        snippet=star_node.sql(dialect="snowflake"),
                        line_number=line_num,
                        suggested_replacement=None,
                        requires_llm=True,
                    )
                )

        return issues
