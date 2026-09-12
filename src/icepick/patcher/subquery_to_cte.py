"""Subquery to CTE flattening engine.

Extracts inline derived tables from FROM and JOIN clauses, converts them
into top-level named Common Table Expressions (CTEs), and replaces original
references with clean table references, preserving dependency order.
"""

from __future__ import annotations

from sqlglot import exp


def _get_ast_depth(node: exp.Expression) -> int:
    """Calculate the nesting depth of an AST node from the root."""
    depth = 0
    curr: exp.Expr | None = node
    while curr is not None and curr.parent is not None:
        depth += 1
        curr = curr.parent
    return depth


def _is_from_or_join_subquery(node: exp.Subquery) -> bool:
    """Determine if a subquery is a derived table located in a FROM or JOIN clause."""
    return isinstance(node.parent, (exp.From, exp.Join))


def _get_existing_cte_names(ast: exp.Expression) -> set[str]:
    """Retrieve lowercase names of all existing top-level CTEs in the query."""
    names: set[str] = set()
    with_clause = ast.args.get("with_")
    if isinstance(with_clause, exp.With):
        for cte in with_clause.expressions:
            if isinstance(cte, exp.CTE) and cte.alias:
                names.add(cte.alias.lower())
    return names


class SubqueryToCTE:
    """Extracts inline subqueries (derived tables) into top-level CTEs.

    Attributes:
        dialect: SQL dialect used for parsing and code generation (default: "snowflake").
    """

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize SubqueryToCTE engine.

        Args:
            dialect: SQL dialect name (default: "snowflake").
        """
        self.dialect = dialect

    def extract_subquery_to_cte(
        self,
        ast: exp.Expression,
        subquery_node: exp.Subquery,
        cte_name: str | None = None,
        prefix: str = "cte_",
    ) -> tuple[exp.Expression, str]:
        """Extract a single inline subquery into a top-level CTE.

        Args:
            ast: The root query AST.
            subquery_node: The exp.Subquery node to promote.
            cte_name: Optional explicit name for the new CTE.
            prefix: Prefix to prepend to alias if generating a name (default: "cte_").

        Returns:
            tuple[exp.Expression, str]: The updated root AST and the confirmed unique CTE name.

        Raises:
            TypeError: If subquery_node has no valid inner expression.
        """
        inner_query = subquery_node.this
        if not isinstance(inner_query, exp.Expression):
            raise TypeError("Target subquery does not contain a valid inner query expression.")

        existing_names = _get_existing_cte_names(ast)

        # 1. Determine base name
        if cte_name and cte_name.strip():
            base_name = cte_name.strip()
        elif subquery_node.alias:
            alias = subquery_node.alias
            base_name = alias if alias.lower().startswith(prefix.lower()) else f"{prefix}{alias}"
        else:
            base_name = f"{prefix}subquery_1"

        # 2. Avoid name collisions with existing CTEs
        final_name = base_name
        counter = 1
        while final_name.lower() in existing_names:
            final_name = f"{base_name}_{counter}"
            counter += 1

        # 3. Construct new CTE node
        cte_node = exp.CTE(
            this=inner_query.copy(),
            alias=exp.TableAlias(this=exp.to_identifier(final_name)),
        )

        # 4. Attach CTE to top-level WITH clause
        existing_with = ast.args.get("with_")
        if isinstance(existing_with, exp.With):
            existing_with.append("expressions", cte_node)
        else:
            ast.set("with_", exp.With(expressions=[cte_node]))

        # 5. Replace original subquery with a simple table reference
        orig_alias = subquery_node.alias
        if orig_alias:
            table_ref = exp.Table(
                this=exp.to_identifier(final_name),
                alias=exp.TableAlias(this=exp.to_identifier(orig_alias)),
            )
        else:
            table_ref = exp.Table(
                this=exp.to_identifier(final_name),
            )

        subquery_node.replace(table_ref)

        return ast, final_name

    def flatten_all_subqueries(
        self,
        ast: exp.Expression,
        prefix: str = "cte_",
    ) -> tuple[exp.Expression, list[str]]:
        """Extract and flatten all inline derived tables in FROM and JOIN clauses into CTEs.

        Preserves dependency order by promoting deeper (nested) subqueries first,
        followed by left-to-right appearance order for independent queries.

        Args:
            ast: The root query AST to transform.
            prefix: Prefix to prepend to generated CTE names (default: "cte_").

        Returns:
            tuple[exp.Expression, list[str]]: The modified root AST and the ordered
                list of newly created CTE names.
        """
        created_ctes: list[str] = []

        while True:
            # Re-collect candidate subqueries on each iteration to reflect current AST state
            candidates: list[tuple[int, int, exp.Subquery]] = []
            for i, sq in enumerate(ast.find_all(exp.Subquery)):
                if _is_from_or_join_subquery(sq):
                    depth = _get_ast_depth(sq)
                    candidates.append((depth, i, sq))

            if not candidates:
                break

            # Deepest first (reverse depth), then earliest index
            candidates.sort(key=lambda x: (-x[0], x[1]))
            _, _, next_subquery = candidates[0]

            ast, promoted_name = self.extract_subquery_to_cte(ast, next_subquery, prefix=prefix)
            created_ctes.append(promoted_name)

        return ast, created_ctes
