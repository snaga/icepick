"""SNOW-007: Inline Subquery in FROM/JOIN Rule.

Detects derived tables (inline subqueries) within FROM and JOIN clauses.
Recommends flattening nested subqueries into named top-level CTEs to improve
pipeline readability, modularity, and Snowflake optimizer plan transparency.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class NestedSubqueryRule(BaseRule):
    """Rule SNOW-007: Detects inline subqueries (derived tables) in FROM/JOIN clauses."""

    rule_id: str = "SNOW-007"
    rule_name: str = "Inline Subquery in FROM/JOIN"
    severity: Severity = Severity.LOW
    description: str = (
        "Inline subquery (derived table) in FROM or JOIN clause; "
        "extract to top-level CTE for readability and optimization."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for inline subqueries in FROM and JOIN clauses.

        Args:
            ast: Root or subtree AST expression.

        Returns:
            list[DiagnosticIssue]: List of detected inline subqueries.
        """
        issues: list[DiagnosticIssue] = []
        seen_subqueries: set[int] = set()

        for from_node in ast.find_all(exp.From):
            candidates = [from_node.this, *from_node.expressions]
            for target in candidates:
                if isinstance(target, exp.Subquery) and id(target) not in seen_subqueries:
                    seen_subqueries.add(id(target))
                    issues.append(self._create_issue(target, "FROM"))

        for join_node in ast.find_all(exp.Join):
            target = join_node.this
            if isinstance(target, exp.Subquery) and id(target) not in seen_subqueries:
                seen_subqueries.add(id(target))
                issues.append(self._create_issue(target, "JOIN"))

        return issues

    def _create_issue(self, subquery: exp.Subquery, clause_type: str) -> DiagnosticIssue:
        alias = subquery.alias
        alias_str = f" '{alias}'" if alias else ""

        line_num: int | None = None
        meta_dict: Any = getattr(subquery, "meta", None)
        if isinstance(meta_dict, dict):
            line_num = meta_dict.get("line")

        return DiagnosticIssue(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            severity=self.severity,
            description=(
                f"Inline subquery (derived table){alias_str} found in {clause_type} clause. "
                f"Extract to top-level CTE for improved readability and optimizer pipeline flattening."
            ),
            target_node=subquery,
            snippet=subquery.sql(dialect="snowflake"),
            line_number=line_num,
            suggested_replacement=None,
            requires_llm=False,
        )
