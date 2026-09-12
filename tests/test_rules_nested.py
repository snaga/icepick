"""Unit tests for SNOW-007 (NestedSubqueryRule)."""

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_007_nested_subquery import NestedSubqueryRule
from icepick.parser import parse_snowflake_sql


def test_detect_subquery_in_from_clause() -> None:
    """Test detecting an inline subquery in FROM clause."""
    sql = """
    SELECT sub.id, sub.name
    FROM (
        SELECT id, name, created_at
        FROM raw_users
        WHERE active = true
    ) AS sub
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-007"
    assert issue.severity == Severity.LOW
    assert "FROM" in issue.description
    assert "sub" in issue.description
    assert isinstance(issue.target_node, exp.Subquery)
    assert issue.suggested_replacement is None
    assert issue.requires_llm is False


def test_detect_subquery_in_join_clause() -> None:
    """Test detecting an inline subquery in JOIN clause."""
    sql = """
    SELECT u.id, o.total
    FROM users u
    JOIN (
        SELECT user_id, SUM(amount) AS total
        FROM orders
        GROUP BY user_id
    ) AS o ON u.id = o.user_id
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-007"
    assert "JOIN" in issue.description
    assert "o" in issue.description
    assert isinstance(issue.target_node, exp.Subquery)


def test_detect_multiple_subqueries_in_from_and_join() -> None:
    """Test detecting multiple inline subqueries in both FROM and JOIN clauses."""
    sql = """
    SELECT a.id, b.score
    FROM (SELECT id FROM accounts) AS a
    LEFT JOIN (SELECT account_id, score FROM scores) AS b
        ON a.id = b.account_id
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 2
    descriptions = [i.description for i in issues]
    assert any("FROM" in d and "a" in d for d in descriptions)
    assert any("JOIN" in d and "b" in d for d in descriptions)


def test_detect_nested_subquery_inside_cte() -> None:
    """Test detecting inline subqueries nested inside a CTE's query."""
    sql = """
    WITH summary AS (
        SELECT sub.id, sub.val
        FROM (SELECT id, val FROM metrics) AS sub
    )
    SELECT * FROM summary
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)

    # Only the subquery inside the CTE should be flagged, not summary itself
    assert len(issues) == 1
    assert "sub" in issues[0].description
    assert "summary" not in issues[0].description


def test_no_issue_on_standard_tables() -> None:
    """Test standard table references in FROM/JOIN do not trigger issues."""
    sql = """
    SELECT u.id, o.amount
    FROM users u
    JOIN orders o ON u.id = o.user_id
    WHERE u.active = true
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_on_top_level_cte_definitions() -> None:
    """Test that top-level CTE definitions are not falsely identified as subqueries."""
    sql = """
    WITH user_data AS (
        SELECT id, name FROM users
    ),
    order_data AS (
        SELECT user_id, amount FROM orders
    )
    SELECT u.name, o.amount
    FROM user_data u
    JOIN order_data o ON u.id = o.user_id
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_on_where_subqueries() -> None:
    """Test that scalar/predicate subqueries in WHERE (e.g. IN/EXISTS) are not flagged."""
    sql = """
    SELECT id, name
    FROM users
    WHERE id IN (SELECT user_id FROM bad_users)
      AND EXISTS (SELECT 1 FROM logs WHERE logs.user_id = users.id)
    """
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)
    assert issues == []


def test_detect_subquery_without_alias() -> None:
    """Test detecting an inline subquery when no alias is provided."""
    sql = "SELECT * FROM (SELECT 1 AS num)"
    ast = parse_snowflake_sql(sql)

    rule = NestedSubqueryRule()
    issues = rule.check(ast)
    assert len(issues) == 1
    assert "FROM" in issues[0].description
