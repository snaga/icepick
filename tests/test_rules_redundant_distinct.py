"""Unit tests for SNOW-008 (RedundantDistinctRule).

Verifies detection of redundant DISTINCT clauses in SELECT queries containing
GROUP BY clauses or aggregate functions, as well as safe node deletion (pop)
via ASTPatcher and TextSplicer.
"""

from __future__ import annotations

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_008_redundant_distinct import RedundantDistinctRule
from icepick.parser import parse_snowflake_sql
from icepick.patcher.in_place import ASTPatcher
from icepick.patcher.splicer import TextSplicer


def test_detect_distinct_with_group_by() -> None:
    """Test detecting redundant DISTINCT when GROUP BY is present."""
    sql = """
    SELECT DISTINCT department, COUNT(*) AS emp_count
    FROM emp
    GROUP BY department
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-008"
    assert issue.rule_name == "Redundant DISTINCT with GROUP BY or Aggregation"
    assert issue.severity == Severity.LOW
    assert issue.is_deletable is True
    assert issue.is_replaceable is False
    assert issue.can_auto_fix is True
    assert issue.suggested_replacement is None
    assert isinstance(issue.target_node, exp.Distinct)
    assert "GROUP BY or aggregate functions" in issue.description


def test_detect_distinct_with_agg_func_only() -> None:
    """Test detecting redundant DISTINCT when only an aggregate function is present."""
    sql = "SELECT DISTINCT COUNT(id) FROM users"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-008"
    assert issue.is_deletable is True
    assert isinstance(issue.target_node, exp.Distinct)


def test_detect_distinct_with_nested_agg() -> None:
    """Test detecting redundant DISTINCT when aggregate function is nested in expression."""
    sql = "SELECT DISTINCT COALESCE(SUM(amount), 0) AS total FROM sales"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].is_deletable is True


def test_ignore_normal_distinct() -> None:
    """Test that standard DISTINCT on non-aggregated projection is not flagged."""
    sql = "SELECT DISTINCT id, name FROM users"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_distinct_inside_agg() -> None:
    """Test that DISTINCT inside aggregate function arguments is not flagged."""
    sql = "SELECT COUNT(DISTINCT user_id) FROM users"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_scalar_subquery_agg() -> None:
    """Test that aggregate functions inside nested scalar subqueries do not trigger the rule."""
    sql = """
    SELECT DISTINCT
        u.id,
        (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS order_count
    FROM users u
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert issues == []


def test_ignore_window_function_without_group_by() -> None:
    """Test that window functions without GROUP BY preserve DISTINCT and are not flagged."""
    sql = "SELECT DISTINCT user_id, COUNT(*) OVER (PARTITION BY user_id) FROM orders"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert issues == []


def test_detect_window_function_with_group_by() -> None:
    """Test that window functions with GROUP BY are flagged because GROUP BY already aggregates."""
    sql = "SELECT DISTINCT department, COUNT(*) OVER () FROM emp GROUP BY department"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-008"
    assert issues[0].is_deletable is True


def test_cte_with_distinct_and_group_by() -> None:
    """Test detecting redundant DISTINCT inside CTE with GROUP BY."""
    sql = """
    WITH dept_summary AS (
        SELECT DISTINCT department, AVG(salary) AS avg_sal
        FROM employees
        GROUP BY department
    )
    SELECT * FROM dept_summary
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-008"
    assert issue.is_deletable is True


def test_patch_removes_distinct_ast_patcher() -> None:
    """Test that ASTPatcher cleanly removes redundant DISTINCT node."""
    sql = "SELECT DISTINCT department, COUNT(*) FROM emp GROUP BY department"
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)
    assert len(issues) == 1

    patcher = ASTPatcher()
    modified_ast = patcher.apply_issue(ast, issues[0])

    result_sql = modified_ast.sql(dialect="snowflake")
    assert "DISTINCT" not in result_sql
    assert "SELECT department, COUNT(*) FROM emp GROUP BY department" == result_sql


def test_patch_removes_distinct_text_splicer() -> None:
    """Test that TextSplicer surgically removes DISTINCT while preserving formatting."""
    sql = """SELECT DISTINCT department, COUNT(*)
FROM emp
GROUP BY department"""

    ast = parse_snowflake_sql(sql)
    rule = RedundantDistinctRule()
    issues = rule.check(ast)
    assert len(issues) == 1

    splicer = TextSplicer()
    spliced_sql, success = splicer.splice_issue(sql, issues[0])

    assert success is True
    assert "DISTINCT" not in spliced_sql
    expected = """SELECT department, COUNT(*)
FROM emp
GROUP BY department"""
    assert spliced_sql == expected


def test_multiple_selects_redundant_distinct() -> None:
    """Test query containing redundant DISTINCT in both CTE and main query."""
    sql = """
    WITH agg_cte AS (
        SELECT DISTINCT category, MAX(price) AS max_p
        FROM products
        GROUP BY category
    )
    SELECT DISTINCT category, max_p
    FROM agg_cte
    GROUP BY category, max_p
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantDistinctRule()
    issues = rule.check(ast)

    assert len(issues) == 2
    for issue in issues:
        assert issue.rule_id == "SNOW-008"
        assert issue.is_deletable is True

    # Verify apply_all with ASTPatcher
    patcher = ASTPatcher()
    patched_ast, applied = patcher.apply_all(ast, issues)
    assert len(applied) == 2
    assert "DISTINCT" not in patched_ast.sql(dialect="snowflake")
