"""Unit tests for SubqueryToCTE engine."""

import pytest
from sqlglot import exp

from icepick.diff import format_diff
from icepick.parser import parse_snowflake_sql
from icepick.patcher import SubqueryToCTE


def test_extract_single_subquery_in_from() -> None:
    """Test extracting a single inline subquery from FROM clause when no WITH clause exists."""
    sql = """
    SELECT sub.id, sub.name
    FROM (
        SELECT id, name FROM users WHERE active = true
    ) AS sub
    """
    ast = parse_snowflake_sql(sql)
    subq = ast.find(exp.Subquery)
    assert subq is not None

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, subq)

    assert cte_name == "cte_sub"
    with_clause = updated_ast.args.get("with_")
    assert with_clause is not None
    assert len(with_clause.expressions) == 1
    assert with_clause.expressions[0].alias == "cte_sub"

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "WITH cte_sub AS" in updated_sql
    assert "FROM cte_sub AS sub" in updated_sql


def test_extract_subquery_in_join() -> None:
    """Test extracting an inline subquery in a JOIN clause."""
    sql = """
    SELECT u.id, o.total
    FROM users u
    JOIN (
        SELECT user_id, SUM(amount) AS total FROM orders GROUP BY user_id
    ) AS o ON u.id = o.user_id
    """
    ast = parse_snowflake_sql(sql)
    join_node = ast.find(exp.Join)
    assert join_node is not None
    assert isinstance(join_node.this, exp.Subquery)

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, join_node.this)

    assert cte_name == "cte_o"
    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "WITH cte_o AS" in updated_sql
    assert "JOIN cte_o AS o ON u.id = o.user_id" in updated_sql


def test_extract_subquery_with_existing_with_clause() -> None:
    """Test appending newly promoted CTE to an existing WITH clause in order."""
    sql = """
    WITH existing_cte AS (
        SELECT id FROM base_table
    )
    SELECT sub.id
    FROM (SELECT id FROM another_table) AS sub
    JOIN existing_cte e ON sub.id = e.id
    """
    ast = parse_snowflake_sql(sql)
    subq = ast.find(exp.Subquery)
    assert subq is not None

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, subq)

    assert cte_name == "cte_sub"
    with_clause = updated_ast.args.get("with_")
    assert with_clause is not None
    assert len(with_clause.expressions) == 2
    assert with_clause.expressions[0].alias == "existing_cte"
    assert with_clause.expressions[1].alias == "cte_sub"


def test_extract_subquery_name_collision_deduplication() -> None:
    """Test automatic resolution of CTE name collisions by adding numerical suffix."""
    sql = """
    WITH cte_sub AS (
        SELECT 1 AS n
    )
    SELECT *
    FROM (SELECT 2 AS n) AS sub
    """
    ast = parse_snowflake_sql(sql)
    subq = ast.find(exp.Subquery)
    assert subq is not None

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, subq)

    # Collision with existing cte_sub -> should become cte_sub_1
    assert cte_name == "cte_sub_1"
    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "cte_sub AS (" in updated_sql
    assert "cte_sub_1 AS (" in updated_sql
    assert "FROM cte_sub_1 AS sub" in updated_sql


def test_flatten_all_subqueries_multiple() -> None:
    """Test flattening multiple subqueries across FROM and JOIN clauses."""
    sql = """
    SELECT m.store_id, s.total_sales, i.stock_count
    FROM (
        SELECT store_id FROM stores WHERE region = 'APAC'
    ) AS m
    JOIN (
        SELECT store_id, SUM(amount) AS total_sales FROM sales GROUP BY store_id
    ) AS s ON m.store_id = s.store_id
    LEFT JOIN (
        SELECT store_id, COUNT(*) AS stock_count FROM inventory GROUP BY store_id
    ) AS i ON m.store_id = i.store_id
    """
    ast = parse_snowflake_sql(sql)

    engine = SubqueryToCTE()
    updated_ast, created_ctes = engine.flatten_all_subqueries(ast)

    assert len(created_ctes) == 3
    assert created_ctes == ["cte_m", "cte_s", "cte_i"]

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "WITH cte_m AS" in updated_sql
    assert "cte_s AS" in updated_sql
    assert "cte_i AS" in updated_sql
    assert "FROM cte_m AS m" in updated_sql
    assert "JOIN cte_s AS s ON m.store_id = s.store_id" in updated_sql
    assert "LEFT JOIN cte_i AS i ON m.store_id = i.store_id" in updated_sql
    # No inline subqueries remaining in the main query
    assert updated_ast.find(exp.Subquery) is None


def test_flatten_nested_subqueries_dependency_order() -> None:
    """Test that deeply nested derived tables are promoted in bottom-up dependency order."""
    sql = """
    SELECT *
    FROM (
        SELECT *
        FROM (
            SELECT 1 AS n
        ) AS inner_sub
    ) AS outer_sub
    """
    ast = parse_snowflake_sql(sql)

    engine = SubqueryToCTE()
    updated_ast, created_ctes = engine.flatten_all_subqueries(ast)

    # Innermost subquery must be extracted and declared BEFORE outer subquery
    assert created_ctes == ["cte_inner_sub", "cte_outer_sub"]

    with_clause = updated_ast.args.get("with_")
    assert with_clause is not None
    assert with_clause.expressions[0].alias == "cte_inner_sub"
    assert with_clause.expressions[1].alias == "cte_outer_sub"

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "FROM cte_inner_sub AS inner_sub" in updated_sql
    assert "FROM cte_outer_sub AS outer_sub" in updated_sql


def test_extract_subquery_without_alias() -> None:
    """Test safely naming and promoting a subquery without an explicit alias."""
    sql = "SELECT * FROM (SELECT 42 AS val)"
    ast = parse_snowflake_sql(sql)
    subq = ast.find(exp.Subquery)
    assert subq is not None

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, subq)

    assert cte_name == "cte_subquery_1"
    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "WITH cte_subquery_1 AS" in updated_sql
    assert "FROM cte_subquery_1" in updated_sql


def test_flatten_all_on_query_without_subqueries() -> None:
    """Test flatten_all_subqueries on a clean query returns unmodified AST."""
    sql = "SELECT id, name FROM users WHERE active = true"
    ast = parse_snowflake_sql(sql)

    engine = SubqueryToCTE()
    updated_ast, created_ctes = engine.flatten_all_subqueries(ast)

    assert created_ctes == []
    assert updated_ast.sql(dialect="snowflake") == ast.sql(dialect="snowflake")


def test_flatten_with_format_diff_integration() -> None:
    """Test that flattening creates a clean, understandable unified diff."""
    original_sql = """
    SELECT s.id, s.name
    FROM (
        SELECT id, name FROM users WHERE active = true
    ) AS s
    """
    ast = parse_snowflake_sql(original_sql)

    engine = SubqueryToCTE()
    flattened_ast, created = engine.flatten_all_subqueries(ast)
    assert created == ["cte_s"]

    flattened_sql = flattened_ast.sql(dialect="snowflake", pretty=True)
    diff = format_diff(original_sql, flattened_sql, filename="users.sql")

    assert diff != ""
    assert "--- a/users.sql" in diff
    assert "+++ b/users.sql" in diff
    assert "+WITH cte_s AS (" in diff
    assert "+FROM cte_s AS s" in diff


def test_extract_subquery_raises_on_invalid_inner() -> None:
    """Test extract_subquery_to_cte raises TypeError if subquery has no inner query."""
    subq = exp.Subquery(this=None)
    engine = SubqueryToCTE()
    with pytest.raises(TypeError, match="does not contain a valid inner query"):
        engine.extract_subquery_to_cte(subq, subq)


def test_extract_subquery_with_explicit_cte_name() -> None:
    """Test providing an explicit CTE name to extract_subquery_to_cte."""
    sql = "SELECT * FROM (SELECT 1 AS num) AS s"
    ast = parse_snowflake_sql(sql)
    subq = ast.find(exp.Subquery)
    assert subq is not None

    engine = SubqueryToCTE()
    updated_ast, cte_name = engine.extract_subquery_to_cte(ast, subq, cte_name="custom_cte_name")

    assert cte_name == "custom_cte_name"
    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "WITH custom_cte_name AS" in updated_sql
    assert "FROM custom_cte_name AS s" in updated_sql
