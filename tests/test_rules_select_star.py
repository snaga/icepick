"""Unit tests for SNOW-012 (SelectStarRule).

Verifies detection of wildcard SELECT * in high-risk contexts
(intermediate CTEs, JOIN statements, and SELECT DISTINCT),
while safely ignoring simple root queries and aggregate expressions like COUNT(*).
"""

from __future__ import annotations

from icepick.linter import Severity
from icepick.linter.rules.snow_012_select_star import SelectStarRule
from icepick.parser import parse_snowflake_sql


def test_detect_star_in_intermediate_cte() -> None:
    """Test that SELECT * inside a CTE definition is detected as SNOW-012."""
    sql = """
    WITH raw_orders AS (
        SELECT * FROM stage_orders
    )
    SELECT id, amount FROM raw_orders
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-012"
    assert issue.rule_name == "Wildcard SELECT * in Pipeline or Join"
    assert issue.severity == Severity.LOW
    assert "intermediate CTE 'raw_orders'" in issue.description
    assert issue.suggested_replacement is None
    assert issue.requires_llm is True


def test_detect_star_in_query_with_join() -> None:
    """Test that SELECT * in a query with JOIN is detected as SNOW-012."""
    sql = """
    SELECT *
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-012"
    assert "query with JOIN" in issues[0].description


def test_detect_table_qualified_star_with_join() -> None:
    """Test that table-qualified wildcard (e.g. o.*) in a JOIN query is detected."""
    sql = """
    SELECT o.*, c.name
    FROM orders o
    LEFT JOIN customers c ON o.customer_id = c.id
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-012"
    assert "o.*" in issues[0].snippet


def test_detect_multiple_stars_with_join() -> None:
    """Test that multiple wildcards (e.g. a.*, b.*) in a JOIN query are each detected."""
    sql = """
    SELECT a.*, b.*
    FROM table_a a
    INNER JOIN table_b b ON a.key = b.key
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 2
    assert all(issue.rule_id == "SNOW-012" for issue in issues)


def test_detect_star_with_distinct() -> None:
    """Test that SELECT DISTINCT * is detected as a high-risk deduplication pattern."""
    sql = "SELECT DISTINCT * FROM uncurated_events"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-012"
    assert "DISTINCT" in issues[0].description


def test_ignore_count_star_and_aggregates() -> None:
    """Test that COUNT(*) and other aggregate functions containing Star are safely ignored."""
    sql = """
    SELECT COUNT(*), COUNT_IF(status = 'OK')
    FROM orders
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_ignore_simple_root_select_star() -> None:
    """Test that simple root query without CTE, JOIN, or DISTINCT is ignored to prevent noise."""
    sql = "SELECT * FROM users WHERE active = TRUE"
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_detect_star_in_nested_ctes() -> None:
    """Test that SELECT * in multiple CTEs generates separate issues for each CTE."""
    sql = """
    WITH
    step1 AS (
        SELECT * FROM raw1
    ),
    step2 AS (
        SELECT * FROM raw2
    )
    SELECT step1.id, step2.val
    FROM step1
    JOIN step2 ON step1.id = step2.id
    """
    ast = parse_snowflake_sql(sql)
    rule = SelectStarRule()
    issues = rule.check(ast)

    assert len(issues) == 2
    assert any("step1" in issue.description for issue in issues)
    assert any("step2" in issue.description for issue in issues)


def test_rule_attributes() -> None:
    """Test standard rule metadata attributes."""
    rule = SelectStarRule()
    assert rule.rule_id == "SNOW-012"
    assert rule.rule_name == "Wildcard SELECT * in Pipeline or Join"
    assert rule.severity == Severity.LOW
    assert "Wildcard 'SELECT *'" in rule.description
