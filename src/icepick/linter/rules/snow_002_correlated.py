"""SNOW-002: Correlated Subquery Detection Rule.

Detects correlated subqueries referencing outer query tables or aliases that
cause repetitive row-by-row evaluations and memory spill in Snowflake.
Because rewriting correlated subqueries generally requires significant query restructuring
(such as converting to window functions, joins, or aggregation CTEs), automated in-place
replacement is not deterministic, and resolution requires LLM assistance.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


def _extract_tables_and_aliases(select: exp.Select) -> set[str]:
    """Extract all table names and aliases defined directly in a SELECT scope.

    Inspects both the FROM clause (including comma joins) and explicit JOIN
    clauses of the given Select node.

    Args:
        select: The SELECT AST node to inspect.

    Returns:
        set[str]: Lowercased set of table identifiers and aliases directly
            available in this query block's scope.
    """
    tables: set[str] = set()

    from_clause = select.args.get("from_") or select.args.get("from")
    sources: list[exp.Expression] = []
    if from_clause:
        if from_clause.this:
            sources.append(from_clause.this)
        if hasattr(from_clause, "expressions") and from_clause.expressions:
            sources.extend(from_clause.expressions)

    for join in select.args.get("joins") or []:
        if join.this:
            sources.append(join.this)

    for src in sources:
        if isinstance(src, exp.Table):
            if src.name:
                tables.add(src.name.lower())
            if src.alias:
                tables.add(src.alias.lower())
        elif isinstance(src, exp.Subquery):
            if src.alias:
                tables.add(src.alias.lower())
        elif isinstance(src, exp.Alias):
            if src.alias:
                tables.add(src.alias.lower())
            if isinstance(src.this, exp.Table) and src.this.name:
                tables.add(src.this.name.lower())

    return tables


class CorrelatedSubqueryRule(BaseRule):
    """Rule SNOW-002: Detects correlated subqueries referencing outer tables."""

    rule_id: str = "SNOW-002"
    rule_name: str = "Correlated Subquery"
    severity: Severity = Severity.CRITICAL
    description: str = (
        "Correlated subquery references outer table; causing repetitive row-by-row "
        "evaluations and memory spill."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for correlated subqueries referencing outer table scopes.

        Args:
            ast: Root or subtree AST expression to analyze.

        Returns:
            list[DiagnosticIssue]: Issues detected for each correlated subquery.
        """
        issues: list[DiagnosticIssue] = []
        diagnosed_node_ids: set[int] = set()

        for select in ast.find_all(exp.Select):
            outer_tables = _extract_tables_and_aliases(select)
            if not outer_tables:
                continue

            for inner_select in select.find_all(exp.Select):
                if inner_select is select:
                    continue

                parent = inner_select.parent
                target_node: exp.Expression = (
                    parent if isinstance(parent, (exp.Subquery, exp.Exists)) else inner_select
                )

                if id(target_node) in diagnosed_node_ids:
                    continue

                inner_tables = _extract_tables_and_aliases(inner_select)

                for col in inner_select.find_all(exp.Column):
                    # Only consider columns belonging directly to inner_select scope
                    # to prevent nested subquery columns from misattributing correlation.
                    if col.find_ancestor(exp.Select) is not inner_select:
                        continue

                    if not col.table:
                        # カタログ情報を持たない静的AST解析では、修飾なしカラムが外部クエリのものか
                        # 決定論的に判別できないため、テーブル/エイリアス修飾済みカラムのみを対象とする
                        continue

                    table_ident = col.table.name if hasattr(col.table, "name") else str(col.table)
                    if not table_ident:
                        continue

                    col_table = table_ident.lower()
                    if col_table not in inner_tables and col_table in outer_tables:
                        diagnosed_node_ids.add(id(target_node))
                        issues.append(
                            self._create_issue(
                                target_node=target_node,
                                inner_select=inner_select,
                                outer_table=col_table,
                            )
                        )
                        break

        return issues

    def _create_issue(
        self,
        target_node: exp.Expression,
        inner_select: exp.Select,
        outer_table: str,
    ) -> DiagnosticIssue:
        """Construct a DiagnosticIssue for a correlated subquery.

        Args:
            target_node: Target AST expression (Subquery or Exists).
            inner_select: The inner SELECT expression.
            outer_table: The identifier of the referenced outer table or alias.

        Returns:
            DiagnosticIssue: Configured issue with CRITICAL severity and requires_llm=True.
        """
        line_num: int | None = None
        meta_dict: Any = getattr(target_node, "meta", None)
        if isinstance(meta_dict, dict):
            line_num = meta_dict.get("line")
        if line_num is None:
            meta_dict = getattr(inner_select, "meta", None)
            if isinstance(meta_dict, dict):
                line_num = meta_dict.get("line")

        return DiagnosticIssue(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            severity=self.severity,
            description=(
                f"Correlated subquery references outer table '{outer_table}', "
                f"causing repetitive row-by-row evaluations and memory spill."
            ),
            target_node=target_node,
            snippet=target_node.sql(dialect="snowflake"),
            line_number=line_num,
            suggested_replacement=None,
            requires_llm=True,
        )
