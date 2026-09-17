"""Unit tests for SNOW-009 (QualifyFlatteningRule).

Verifies detection of subqueries calculating window functions filtered in outer
WHERE clauses, safe flattening into Snowflake native QUALIFY clauses, and
in-place replacement via ASTPatcher.
"""

from __future__ import annotations

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_009_qualify_flattening import QualifyFlatteningRule
from icepick.parser import parse_snowflake_sql
from icepick.patcher.in_place import ASTPatcher


def test_detect_and_flatten_row_number() -> None:
    """Test detecting ROW_NUMBER() subquery and generating QUALIFY replacement."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-009"
    assert issue.rule_name == "Window Function Flattening to QUALIFY"
    assert issue.severity == Severity.MEDIUM
    assert issue.is_replaceable is True
    assert issue.is_deletable is False
    assert issue.can_auto_fix is True
    assert issue.suggested_replacement is not None
    assert isinstance(issue.target_node, exp.Select)

    # Replacement check
    replacement = issue.suggested_replacement
    replacement_sql = replacement.sql("snowflake")
    assert "QUALIFY" in replacement_sql
    assert "ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) = 1" in replacement_sql
    assert "FROM emp" in replacement_sql
    assert "rn" not in replacement_sql.split("QUALIFY")[0]  # rn is removed from SELECT list


def test_detect_with_inner_where_and_outer_where() -> None:
    """Test preserving inner WHERE clause while adding outer window filter as QUALIFY."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp WHERE dept = 'HR') sub WHERE rn <= 3"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    replacement = issue.suggested_replacement
    assert replacement is not None
    replacement_sql = replacement.sql("snowflake")

    assert "WHERE dept = 'HR'" in replacement_sql
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) <= 3" in replacement_sql


def test_detect_with_outer_non_window_where() -> None:
    """Test that non-window predicates in outer WHERE are merged into inner WHERE."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, DENSE_RANK() OVER (PARTITION BY cat ORDER BY price DESC) AS dr "
        "FROM products WHERE status = 'ACTIVE') sub "
        "WHERE dr = 1 AND sub.price > 100"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    replacement = issues[0].suggested_replacement
    assert replacement is not None
    replacement_sql = replacement.sql("snowflake")

    assert "status = 'ACTIVE'" in replacement_sql
    assert "price > 100" in replacement_sql
    assert "QUALIFY DENSE_RANK() OVER (PARTITION BY cat ORDER BY price DESC) = 1" in replacement_sql


def test_detect_with_star_projection() -> None:
    """Test outer SELECT * drops intermediate window alias and keeps other columns."""
    sql = (
        "SELECT * FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn "
        "FROM users) sub WHERE rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    replacement = issues[0].suggested_replacement
    assert replacement is not None
    replacement_sql = replacement.sql("snowflake")

    assert "SELECT id, name FROM users" in replacement_sql
    assert "QUALIFY ROW_NUMBER() OVER (ORDER BY created_at DESC) = 1" in replacement_sql


def test_ignore_when_outer_selects_window_alias() -> None:
    """Test skipping when outer SELECT outputs the window alias as a column."""
    sql = (
        "SELECT id, rn FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_when_outer_selects_qualified_window_alias() -> None:
    """Test skipping when outer SELECT outputs table-qualified window alias."""
    sql = (
        "SELECT sub.id, sub.rn FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE sub.rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_normal_subquery_without_window() -> None:
    """Test that subqueries without window functions are not flagged."""
    sql = "SELECT id, name FROM (SELECT id, name FROM emp WHERE dept = 'HR') sub WHERE id > 10"
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_window_without_outer_filter() -> None:
    """Test that window function subquery not filtered in outer WHERE is ignored."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE id > 10"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_subquery_in_join() -> None:
    """Test that subqueries participating in JOIN are ignored."""
    sql = (
        "SELECT e.id FROM emp e "
        "JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM t) sub ON e.id = sub.id "
        "WHERE sub.rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_inner_limit() -> None:
    """Test that inner subqueries with LIMIT clause cannot be safely flattened."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (ORDER BY sal DESC) AS rn "
        "FROM emp LIMIT 100) sub WHERE rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert issues == []


def test_qualify_with_outer_order_and_limit() -> None:
    """Test that outer ORDER BY and LIMIT clauses are preserved on replacement."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE rn = 1 ORDER BY id DESC LIMIT 50"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    replacement = issues[0].suggested_replacement
    assert replacement is not None
    replacement_sql = replacement.sql("snowflake")

    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) = 1" in replacement_sql
    assert "ORDER BY id DESC" in replacement_sql
    assert "LIMIT 50" in replacement_sql


def test_patcher_in_place_flattening() -> None:
    """Test that ASTPatcher successfully replaces the AST in-place with a valid Snowflake SQL."""
    sql = (
        "SELECT id, name FROM ("
        "SELECT id, name, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp WHERE dept = 'HR') sub WHERE rn = 1"
    )
    ast = parse_snowflake_sql(sql)

    rule = QualifyFlatteningRule()
    issues = rule.check(ast)
    assert len(issues) == 1

    patcher = ASTPatcher(dialect="snowflake")
    patched_ast = patcher.apply_issue(ast, issues[0])

    expected_sql = (
        "SELECT id, name FROM emp WHERE dept = 'HR' "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) = 1"
    )
    # Parse both to compare AST equivalence
    expected_ast = parse_snowflake_sql(expected_sql)

    assert patched_ast.sql("snowflake") == expected_ast.sql("snowflake")


def test_ignore_outer_group_by() -> None:
    """Test that subquery is ignored when outer SELECT has GROUP BY."""
    sql = (
        "SELECT id, COUNT(*) FROM ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE rn = 1 GROUP BY id"
    )
    ast = parse_snowflake_sql(sql)
    rule = QualifyFlatteningRule()
    issues = rule.check(ast)
    assert issues == []


def test_ignore_outer_having() -> None:
    """Test that subquery is ignored when outer SELECT has HAVING."""
    sql = (
        "SELECT id FROM ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY sal DESC) AS rn "
        "FROM emp) sub WHERE rn = 1 HAVING id > 10"
    )
    ast = parse_snowflake_sql(sql)
    rule = QualifyFlatteningRule()
    issues = rule.check(ast)
    assert issues == []
