"""Unit tests for SNOW-012 (SelectStarRule).

Verifies detection of wildcard SELECT * or table.* in intermediate CTEs,
queries with JOIN clauses, or SELECT DISTINCT, and confirms exclusion of
safe count aggregates and simple root single-table SELECTs.
"""

from __future__ import annotations

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_012_select_star import SelectStarRule
from icepick.parser import parse_snowflake_sql


def test_rule_attributes() -> None:
    """Test rule metadata attributes."""
    rule = SelectStarRule()
    assert rule.rule_id == "SNOW-012"
    assert rule.rule_name == "Wildcard SELECT * in Pipeline or Join"
    assert rule.severity == Severity.LOW
    assert "Wildcard 'SELECT *'" in rule.description


def test_detect_star_in_cte() -> None:
    """Test that wildcard SELECT * in an intermediate CTE is detected."""
    sql = "WITH t AS (SELECT * FROM raw) SELECT id FROM t"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-012"
    assert issue.rule_name == "Wildcard SELECT * in Pipeline or Join"
    assert issue.severity == Severity.LOW
    assert issue.suggested_replacement is None
    assert issue.requires_llm is True
    assert issue.can_auto_fix is False
    assert isinstance(issue.target_node, exp.Star)
    assert issue.snippet == "*"
    assert "Wildcard 'SELECT *' in CTE 't'" in issue.description
    assert "prevents column pruning" in issue.description


def test_detect_star_with_join() -> None:
    """Test that wildcard SELECT * in a query with JOIN is detected."""
    sql = "SELECT * FROM orders JOIN users ON orders.user_id = users.id"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-012"
    assert issue.snippet == "*"
    assert "Wildcard 'SELECT *' in query with JOIN" in issue.description
    assert "multiple tables" in issue.description


def test_detect_table_star_with_join() -> None:
    """Test that qualified table wildcard o.* with JOIN is detected."""
    sql = "SELECT o.*, u.name FROM orders o JOIN users u ON o.user_id = u.id"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-012"
    assert issue.snippet == "o.*"
    assert "Wildcard 'SELECT *' in query with JOIN" in issue.description


def test_detect_multiple_table_stars_with_join() -> None:
    """Test that multiple table wildcards in a JOIN query are each flagged."""
    sql = "SELECT o.*, u.* FROM orders o JOIN users u ON o.user_id = u.id"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 2
    snippets = [issue.snippet for issue in issues]
    assert "o.*" in snippets
    assert "u.*" in snippets


def test_detect_star_with_distinct() -> None:
    """Test that SELECT DISTINCT * is detected due to heavy deduplication sort risk."""
    sql = "SELECT DISTINCT * FROM raw_data"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-012"
    assert issue.snippet == "*"
    assert "Wildcard 'SELECT DISTINCT *'" in issue.description
    assert "forces deduplication sort over all columns" in issue.description


def test_ignore_count_star() -> None:
    """Test that COUNT(*) and COUNT(1) or aggregate functions are not flagged."""
    sql = "SELECT COUNT(*), COUNT(1), COUNT_IF(active = TRUE) FROM orders"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_ignore_simple_root_select_star() -> None:
    """Test that simple root SELECT * without JOIN, CTE, or DISTINCT is excluded."""
    sql = "SELECT * FROM users"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_ignore_simple_root_select_star_with_where_order() -> None:
    """Test that single-table exploratory query with WHERE/ORDER BY is also excluded."""
    sql = "SELECT * FROM users WHERE status = 'ACTIVE' ORDER BY created_at DESC LIMIT 10"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 0
