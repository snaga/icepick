"""Unit tests for SNOW-003 (RedundantSortRule)."""

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.parser import parse_snowflake_sql


def test_detect_redundant_order_by_in_cte() -> None:
    """Test detecting redundant ORDER BY inside a CTE without LIMIT."""
    sql = """
    WITH sorted_data AS (
        SELECT id, name, created_at
        FROM users
        WHERE active = true
        ORDER BY created_at DESC
    )
    SELECT id, name FROM sorted_data
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-003"
    assert issue.severity == Severity.MEDIUM
    assert "sorted_data" in issue.description
    assert issue.is_deletable is True
    assert issue.is_replaceable is False
    assert issue.can_auto_fix is True
    assert issue.suggested_replacement is None
    assert isinstance(issue.target_node, exp.Order)

    # Pop operation should cleanly eliminate the ORDER BY
    issue.target_node.pop()
    cleaned_sql = ast.sql(dialect="snowflake")
    assert "ORDER BY" not in cleaned_sql


def test_detect_redundant_order_by_in_derived_table() -> None:
    """Test detecting redundant ORDER BY inside an inline subquery (FROM / JOIN)."""
    sql = """
    SELECT sub.id, sub.amount
    FROM (
        SELECT id, amount, customer_id
        FROM orders
        ORDER BY amount DESC
    ) sub
    JOIN customers c ON sub.customer_id = c.id
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert "subquery" in issue.description
    assert issue.is_deletable is True
    assert isinstance(issue.target_node, exp.Order)


def test_detect_redundant_order_by_in_where_subquery() -> None:
    """Test detecting redundant ORDER BY inside a WHERE IN subquery."""
    sql = """
    SELECT id, name
    FROM users
    WHERE id IN (
        SELECT user_id
        FROM audit_logs
        WHERE event = 'LOGIN'
        ORDER BY timestamp DESC
    )
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].is_deletable is True


def test_detect_redundant_order_by_in_union_inside_cte() -> None:
    """Test detecting redundant ORDER BY in a UNION query inside a CTE."""
    sql = """
    WITH union_cte AS (
        SELECT id FROM table_a
        UNION ALL
        SELECT id FROM table_b
        ORDER BY id
    )
    SELECT * FROM union_cte
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].is_deletable is True


def test_no_issue_when_cte_has_limit() -> None:
    """Test that ORDER BY accompanied by LIMIT in CTE is preserved."""
    sql = """
    WITH top_users AS (
        SELECT id, score
        FROM rankings
        ORDER BY score DESC
        LIMIT 10
    )
    SELECT * FROM top_users
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_when_subquery_has_fetch() -> None:
    """Test that ORDER BY accompanied by FETCH FIRST in subquery is preserved."""
    sql = """
    SELECT *
    FROM (
        SELECT id, score
        FROM rankings
        ORDER BY score DESC
        FETCH FIRST 5 ROWS ONLY
    ) sub
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_for_top_level_order_by() -> None:
    """Test that top-level query ORDER BY is never flagged."""
    sql = """
    WITH active_users AS (
        SELECT id, name FROM users WHERE active = true
    )
    SELECT id, name
    FROM active_users
    ORDER BY name ASC
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_for_window_function_order_by() -> None:
    """Test that ORDER BY inside window functions (OVER clause) is not flagged."""
    sql = """
    WITH ranked_events AS (
        SELECT
            user_id,
            timestamp,
            ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY timestamp DESC) AS rn,
            RANK() OVER (ORDER BY score ASC) AS rnk
        FROM events
    )
    SELECT user_id, timestamp
    FROM ranked_events
    WHERE rn = 1
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_when_no_order_by_present() -> None:
    """Test query with no ORDER BY produces no issues."""
    sql = "SELECT id, count(*) FROM logs GROUP BY id"
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    assert rule.check(ast) == []


def test_cte_without_this_expression() -> None:
    """Test robustness when CTE has no this expression."""
    malformed_cte = exp.CTE(this=None)
    rule = RedundantSortRule()
    assert rule.check(malformed_cte) == []
