"""SNOW-009: Window Function Flattening to QUALIFY Rule.

Detects nested subqueries wrapping window functions solely to filter by the
resulting alias in an outer WHERE clause (e.g. `WHERE rn = 1`), and suggests
flattening them into Snowflake's native QUALIFY clause.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


def _split_and(expression: exp.Expression) -> list[exp.Expression]:
    """Recursively split chained AND expressions into a flat list of conjuncts.

    Args:
        expression: Expression node representing a predicate or conjunctive tree.

    Returns:
        list[exp.Expression]: List of individual conjunct expressions.
    """
    if isinstance(expression, exp.And):
        left = expression.left
        right = expression.right
        res: list[exp.Expression] = []
        if isinstance(left, exp.Expression):
            res.extend(_split_and(left))
        if isinstance(right, exp.Expression):
            res.extend(_split_and(right))
        return res
    return [expression]


class QualifyFlatteningRule(BaseRule):
    """Rule SNOW-009: Detects window function subqueries filterable via QUALIFY clause."""

    rule_id: str = "SNOW-009"
    rule_name: str = "Window Function Flattening to QUALIFY"
    severity: Severity = Severity.MEDIUM
    description: str = (
        "Window function in subquery filtered in outer WHERE clause can be flattened "
        "using Snowflake QUALIFY clause."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for SELECT statements candidate for QUALIFY flattening.

        Args:
            ast: Root or subtree AST expression.

        Returns:
            list[DiagnosticIssue]: List of issues with suggested QUALIFY rewrites.
        """
        issues: list[DiagnosticIssue] = []

        for outer_select in ast.find_all(exp.Select):
            # 1. Outer SELECT must not have JOINs, GROUP BY, or HAVING
            if (
                outer_select.args.get("joins")
                or outer_select.args.get("group")
                or outer_select.args.get("having")
            ):
                continue

            # 2. Outer SELECT must have a single FROM item which is a Subquery
            from_clause = outer_select.args.get("from_")
            if from_clause is None or not isinstance(from_clause, exp.From):
                continue
            if from_clause.expressions:
                # Comma-separated multiple tables in FROM
                continue
            subquery = from_clause.this
            if not isinstance(subquery, exp.Subquery):
                continue

            # 3. Inner query of the subquery must be an exp.Select
            inner_select = subquery.this
            if not isinstance(inner_select, exp.Select):
                continue

            # Inner query with LIMIT cannot be safely flattened with QUALIFY
            if inner_select.args.get("limit"):
                continue

            # Subquery alias (e.g. 'sub' in `FROM (...) sub`)
            subquery_alias = subquery.alias.lower() if subquery.alias else ""

            # 4. Find window functions in inner SELECT projections
            # Mapping: alias_name_lower -> (alias_node, window_expr)
            window_funcs: dict[str, tuple[exp.Alias, exp.Expression]] = {}
            for expr in inner_select.expressions:
                if isinstance(expr, exp.Alias):
                    win_node = expr.find(exp.Window)
                    if win_node is not None:
                        alias_name = expr.alias.lower()
                        window_funcs[alias_name] = (expr, expr.this)

            if not window_funcs:
                continue

            # 5. Outer SELECT must have a WHERE clause
            outer_where = outer_select.args.get("where")
            if outer_where is None or outer_where.this is None:
                continue

            # 6. Check if outer SELECT projections reference any window function alias directly.
            # If the outer SELECT outputs the window column, flattening could drop required columns.
            outer_references_window_col = False
            for expr in outer_select.expressions:
                for col in expr.find_all(exp.Column):
                    col_name = col.name.lower()
                    if col_name in window_funcs and (
                        not col.table or col.table.lower() == subquery_alias
                    ):
                        outer_references_window_col = True
                        break
                if outer_references_window_col:
                    break

            if outer_references_window_col:
                continue

            # 7. Split outer WHERE predicates into window-filtering conjuncts (for QUALIFY)
            # and non-window conjuncts (to merge into inner WHERE).
            conjuncts = _split_and(outer_where.this)
            qualify_candidates: list[exp.Expression] = []
            where_candidates: list[exp.Expression] = []

            for conjunct in conjuncts:
                refs_window = False
                for col in conjunct.find_all(exp.Column):
                    col_name = col.name.lower()
                    if col_name in window_funcs and (
                        not col.table or col.table.lower() == subquery_alias
                    ):
                        refs_window = True
                        break
                if refs_window:
                    qualify_candidates.append(conjunct)
                else:
                    where_candidates.append(conjunct)

            if not qualify_candidates:
                continue

            # 8. Build suggested replacement AST
            inner_clone = inner_select.copy()

            # Transform qualify predicates: replace window alias columns with the window expression itself
            transformed_qualify_preds: list[exp.Expression] = []
            for pred in qualify_candidates:
                pred_clone = pred.copy()
                for col in list(pred_clone.find_all(exp.Column)):
                    col_name = col.name.lower()
                    if col_name in window_funcs:
                        if not col.table or col.table.lower() == subquery_alias:
                            win_expr = window_funcs[col_name][1]
                            col.replace(win_expr.copy())
                    elif col.table and col.table.lower() == subquery_alias:
                        col.set("table", None)
                transformed_qualify_preds.append(pred_clone)

            qualify_expr = (
                exp.and_(*transformed_qualify_preds)
                if len(transformed_qualify_preds) > 1
                else transformed_qualify_preds[0]
            )

            # Integrate into inner_clone QUALIFY clause
            existing_qualify = inner_clone.args.get("qualify")
            if existing_qualify is not None and isinstance(existing_qualify, exp.Qualify):
                combined_qualify = exp.and_(existing_qualify.this, qualify_expr)
                inner_clone.set("qualify", exp.Qualify(this=combined_qualify))
            else:
                inner_clone.set("qualify", exp.Qualify(this=qualify_expr))

            # Transform and merge outer non-window WHERE predicates into inner WHERE clause
            if where_candidates:
                transformed_where_preds: list[exp.Expression] = []
                for pred in where_candidates:
                    pred_clone = pred.copy()
                    for col in list(pred_clone.find_all(exp.Column)):
                        if col.table and col.table.lower() == subquery_alias:
                            col.set("table", None)
                    transformed_where_preds.append(pred_clone)

                outer_where_merged = (
                    exp.and_(*transformed_where_preds)
                    if len(transformed_where_preds) > 1
                    else transformed_where_preds[0]
                )

                existing_where = inner_clone.args.get("where")
                if existing_where is not None and isinstance(existing_where, exp.Where):
                    combined_where = exp.and_(existing_where.this, outer_where_merged)
                    inner_clone.set("where", exp.Where(this=combined_where))
                else:
                    inner_clone.set("where", exp.Where(this=outer_where_merged))

            # Adjust expressions:
            # If outer SELECT uses SELECT *, keep inner projections minus the intermediate window function aliases
            has_star = any(
                isinstance(e, exp.Star) or e.find(exp.Star) is not None
                for e in outer_select.expressions
            )
            if has_star:
                new_expressions = [
                    e.copy()
                    for e in inner_clone.expressions
                    if not (isinstance(e, exp.Alias) and e.alias.lower() in window_funcs)
                ]
                if not new_expressions:
                    new_expressions = [exp.Star()]
                inner_clone.set("expressions", new_expressions)
            else:
                # Use outer SELECT projection list, stripping subquery alias table qualifications
                new_expressions = []
                for e in outer_select.expressions:
                    e_clone = e.copy()
                    for col in list(e_clone.find_all(exp.Column)):
                        if col.table and col.table.lower() == subquery_alias:
                            col.set("table", None)
                    new_expressions.append(e_clone)
                inner_clone.set("expressions", new_expressions)

            # Inherit order by, limit, distinct from outer SELECT if present
            if outer_select.args.get("order"):
                order_clone = outer_select.args["order"].copy()
                for col in list(order_clone.find_all(exp.Column)):
                    col_name = col.name.lower()
                    if col_name in window_funcs:
                        if not col.table or col.table.lower() == subquery_alias:
                            win_expr = window_funcs[col_name][1]
                            col.replace(win_expr.copy())
                    elif col.table and col.table.lower() == subquery_alias:
                        col.set("table", None)
                inner_clone.set("order", order_clone)

            if outer_select.args.get("limit"):
                inner_clone.set("limit", outer_select.args["limit"].copy())

            if outer_select.args.get("distinct"):
                inner_clone.set("distinct", outer_select.args["distinct"].copy())

            line_num: int | None = None
            meta_select: Any = getattr(outer_select, "meta", None)
            if isinstance(meta_select, dict):
                line_num = meta_select.get("line")

            snippet = outer_select.sql(dialect="snowflake")

            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=self.description,
                    target_node=outer_select,
                    snippet=snippet,
                    line_number=line_num,
                    suggested_replacement=inner_clone,
                )
            )

        return issues
