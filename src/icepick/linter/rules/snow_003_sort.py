"""SNOW-003: Redundant Sort in Subquery/CTE Rule.

Detects unnecessary ORDER BY clauses in subqueries or CTEs that do not specify
a LIMIT or FETCH clause. Subquery ordering without LIMIT is not guaranteed in SQL
and wastes sort memory and compute.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class RedundantSortRule(BaseRule):
    """Rule SNOW-003: Detects redundant ORDER BY in subquery or CTE without LIMIT/FETCH."""

    rule_id: str = "SNOW-003"
    rule_name: str = "Redundant Sort in Subquery/CTE"
    severity: Severity = Severity.MEDIUM
    description: str = (
        "Redundant ORDER BY in subquery or CTE without LIMIT causes "
        "unnecessary compute and spill overhead."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for redundant ORDER BY inside Subqueries and CTEs.

        Args:
            ast: Root or subtree AST expression.

        Returns:
            list[DiagnosticIssue]: List of detected redundant sort issues.
        """
        issues: list[DiagnosticIssue] = []
        for container in ast.find_all(exp.CTE, exp.Subquery):
            query = container.this
            if not isinstance(query, exp.Expression):
                continue

            # Check the direct query, and branches if query is a Union
            candidates: list[exp.Expression] = [query]
            if isinstance(query, exp.Union):
                candidates.extend(
                    [q for q in (query.this, query.expression) if isinstance(q, exp.Expression)]
                )

            for candidate in candidates:
                order_node = candidate.args.get("order")
                if not isinstance(order_node, exp.Order):
                    continue

                limit_node = candidate.args.get("limit") or candidate.args.get("fetch")
                if limit_node is not None:
                    # Legitimate top-N sort
                    continue

                container_type = "CTE" if isinstance(container, exp.CTE) else "subquery"
                alias = container.alias
                label = f" '{alias}'" if alias else ""

                line_num: int | None = None
                meta_dict: Any = getattr(order_node, "meta", None)
                if isinstance(meta_dict, dict):
                    line_num = meta_dict.get("line")

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=(
                            f"Redundant ORDER BY found in {container_type}{label} without LIMIT or FETCH. "
                            f"Ordering in subqueries without LIMIT is ignored by relational semantics "
                            f"and incurs unnecessary sorting overhead."
                        ),
                        target_node=order_node,
                        snippet=order_node.sql(dialect="snowflake"),
                        line_number=line_num,
                        suggested_replacement=None,  # Indicates deletion (pop)
                        requires_llm=False,
                    )
                )

        return issues
