"""Detroit-style unit tests for the ContextSlicer module."""

from __future__ import annotations

import pytest
from sqlglot import exp

from icepick.linter.base import DiagnosticIssue, Severity
from icepick.llm.slicer import ContextSlicer, SliceContext
from icepick.parser import parse_snowflake_sql


class TestContextSlicer:
    """Tests for ContextSlicer and SliceContext."""

    def test_slice_correlated_subquery_inside_cte(self) -> None:
        """Test slicing a correlated subquery inside a CTE with surrounding context."""
        sql = """
        WITH filtered_orders AS (
            SELECT
                o.order_id,
                o.customer_id,
                (
                    SELECT MAX(p.payment_date)
                    FROM payments p
                    WHERE p.order_id = o.order_id
                ) AS last_payment_date
            FROM orders o
            WHERE o.status = 'COMPLETED'
        )
        SELECT * FROM filtered_orders
        """
        ast = parse_snowflake_sql(sql)
        subqueries = list(ast.find_all(exp.Subquery))
        assert len(subqueries) >= 1
        target_subquery = subqueries[0]

        issue = DiagnosticIssue(
            rule_id="SNOW-002",
            rule_name="CorrelatedSubqueryRule",
            severity=Severity.CRITICAL,
            description="Correlated scalar subquery detected in projection. May cause row-by-row iteration.",
            target_node=target_subquery,
            snippet=target_subquery.sql(dialect="snowflake"),
            requires_llm=True,
        )

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, target_subquery, issue=issue)

        assert isinstance(ctx, SliceContext)
        # target_sql verification
        assert "SELECT" in ctx.target_sql
        assert "MAX(p.payment_date)" in ctx.target_sql
        assert "FROM payments" in ctx.target_sql

        # parent_info verification
        assert ctx.parent_info == "CTE: filtered_orders"

        # referenced_tables verification
        assert "payments" in ctx.referenced_tables
        # referenced_columns verification (both inner and correlated outer columns)
        assert "order_id" in ctx.referenced_columns
        assert "payment_date" in ctx.referenced_columns

        # prompt content verification
        assert "You are an expert Snowflake SQL optimization engineer." in ctx.prompt
        assert "Snowflake SQL (`snowflake`)" in ctx.prompt
        assert "CTE: filtered_orders" in ctx.prompt
        assert "SNOW-002" in ctx.prompt
        assert "CRITICAL" in ctx.prompt
        assert "Correlated scalar subquery detected" in ctx.prompt
        assert "```sql" in ctx.prompt
        assert "Output ONLY the replacement Snowflake SQL fragment" in ctx.prompt
        assert "```sql ... ```" in ctx.prompt

    def test_slice_node_without_issue(self) -> None:
        """Test slicing when no DiagnosticIssue is provided."""
        sql = "SELECT id, name FROM users WHERE id > 100"
        ast = parse_snowflake_sql(sql)
        where_clause = ast.find(exp.Where)
        assert where_clause is not None

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, where_clause)

        assert ctx.parent_info == "Top-level SELECT"
        assert "users" not in ctx.referenced_tables  # WHERE clause has no Table, only Column
        assert "id" in ctx.referenced_columns
        assert "No specific static diagnostic rule attached." in ctx.prompt
        assert "```sql\nWHERE\n  id > 100\n```" in ctx.prompt or "WHERE id > 100" in ctx.prompt

    def test_slice_root_node(self) -> None:
        """Test slicing when the target node is the root AST node itself."""
        sql = "SELECT a, b FROM tbl"
        ast = parse_snowflake_sql(sql)

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, ast)

        assert ctx.parent_info == "Root query"
        assert ctx.referenced_tables == ["tbl"]
        assert ctx.referenced_columns == ["a", "b"]
        assert "tbl" in ctx.target_sql

    def test_slice_top_level_select_subnode(self) -> None:
        """Test parent_info for subnodes in a top-level SELECT query (not inside CTE)."""
        sql = "SELECT col1 FROM (SELECT col1 FROM source_tbl) AS sub"
        ast = parse_snowflake_sql(sql)
        subquery = ast.find(exp.Subquery)
        assert subquery is not None

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, subquery)

        assert ctx.parent_info == "Top-level SELECT"
        assert ctx.referenced_tables == ["source_tbl"]
        assert ctx.referenced_columns == ["col1"]

    def test_slice_non_select_top_level_statement(self) -> None:
        """Test parent_info when root AST is an INSERT/CREATE/other statement."""
        sql = "INSERT INTO target_tbl SELECT id FROM source_tbl"
        ast = parse_snowflake_sql(sql)
        source_table = ast.find(exp.Table)
        assert source_table is not None

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, source_table)

        assert "Top-level INSERT" in ctx.parent_info

    def test_empty_tables_and_columns(self) -> None:
        """Test slicing an expression with no tables or column references (e.g. constant literal)."""
        sql = "SELECT 1 + 2"
        ast = parse_snowflake_sql(sql)
        add_op = ast.find(exp.Add)
        assert add_op is not None

        slicer = ContextSlicer()
        ctx = slicer.slice_node(ast, add_op)

        assert ctx.referenced_tables == []
        assert ctx.referenced_columns == []
        assert "- **Referenced Tables**: None" in ctx.prompt
        assert "- **Referenced Columns**: None" in ctx.prompt

    def test_custom_dialect(self) -> None:
        """Test ContextSlicer with custom dialect."""
        slicer = ContextSlicer(dialect="postgres")
        assert slicer.dialect == "postgres"

        sql = "SELECT current_timestamp"
        ast = parse_snowflake_sql(sql)
        ctx = slicer.slice_node(ast, ast)

        assert "Snowflake SQL (`postgres`)" in ctx.prompt

    def test_invalid_arguments_raise_type_error(self) -> None:
        """Test that invalid types for root_ast or target_node raise TypeError."""
        slicer = ContextSlicer()
        ast = parse_snowflake_sql("SELECT 1")

        with pytest.raises(TypeError, match="Expected root_ast to be an exp.Expression"):
            slicer.slice_node("NOT_AN_AST", ast)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="Expected target_node to be an exp.Expression"):
            slicer.slice_node(ast, "NOT_AN_AST")  # type: ignore[arg-type]
