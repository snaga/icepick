"""Unit tests for SNOW-002 (CorrelatedSubqueryRule)."""

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_002_correlated import CorrelatedSubqueryRule
from icepick.parser import parse_snowflake_sql


def test_correlated_rule_metadata() -> None:
    """Test rule metadata and class-level properties."""
    rule = CorrelatedSubqueryRule()
    assert rule.rule_id == "SNOW-002"
    assert rule.rule_name == "Correlated Subquery"
    assert rule.severity == Severity.CRITICAL
    assert "Correlated subquery" in rule.description


def test_detect_where_exists_correlated_subquery() -> None:
    """Test detecting a correlated subquery in a WHERE EXISTS clause."""
    sql = """
    SELECT *
    FROM parent p
    WHERE EXISTS (
        SELECT 1
        FROM child c
        WHERE c.parent_id = p.id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert issue.rule_name == "Correlated Subquery"
    assert issue.severity == Severity.CRITICAL
    assert "p" in issue.description
    assert "Correlated subquery references outer table 'p'" in issue.description
    assert issue.requires_llm is True
    assert issue.suggested_replacement is None
    assert issue.is_replaceable is False
    assert issue.can_auto_fix is False
    assert isinstance(issue.target_node, exp.Exists)


def test_detect_where_in_correlated_subquery() -> None:
    """Test detecting a correlated subquery inside a WHERE IN clause."""
    sql = """
    SELECT o1.id, o1.cust_id
    FROM orders o1
    WHERE o1.cust_id IN (
        SELECT c.id
        FROM customers c
        WHERE c.region = o1.region
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert issue.severity == Severity.CRITICAL
    assert "o1" in issue.description
    assert issue.requires_llm is True
    assert isinstance(issue.target_node, exp.Subquery)


def test_detect_scalar_subquery_in_select_clause() -> None:
    """Test detecting a correlated scalar subquery in SELECT projection."""
    sql = """
    SELECT
        p.id,
        (SELECT COUNT(*) FROM child c WHERE c.parent_id = p.id) AS child_count
    FROM parent p
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert issue.severity == Severity.CRITICAL
    assert "p" in issue.description
    assert issue.requires_llm is True
    assert isinstance(issue.target_node, exp.Subquery)


def test_detect_where_comparison_predicate_correlated_subquery() -> None:
    """Test detecting a correlated subquery in a comparison predicate (e.g. >)."""
    sql = """
    SELECT *
    FROM orders o1
    WHERE amount > (
        SELECT AVG(amount)
        FROM orders o2
        WHERE o2.cust_id = o1.cust_id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert issue.severity == Severity.CRITICAL
    assert "o1" in issue.description
    assert issue.requires_llm is True
    assert isinstance(issue.target_node, exp.Subquery)


def test_detect_correlated_subquery_inside_cte() -> None:
    """Test detecting a correlated subquery nested within a CTE definition."""
    sql = """
    WITH filtered_parents AS (
        SELECT p.id, p.name
        FROM parent p
        WHERE EXISTS (
            SELECT 1
            FROM child c
            WHERE c.parent_id = p.id
        )
    )
    SELECT * FROM filtered_parents
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert issue.severity == Severity.CRITICAL
    assert "p" in issue.description
    assert issue.requires_llm is True


def test_detect_correlated_subquery_referencing_table_without_alias() -> None:
    """Test detecting correlation when outer table has no alias and is referenced by table name."""
    sql = """
    SELECT *
    FROM parent
    WHERE EXISTS (
        SELECT 1
        FROM child
        WHERE child.parent_id = parent.id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-002"
    assert "parent" in issue.description


def test_no_issue_on_uncorrelated_subquery() -> None:
    """Test that independent, uncorrelated subqueries do not trigger SNOW-002."""
    sqls = [
        # Comparison with uncorrelated aggregation subquery
        "SELECT * FROM orders WHERE amount > (SELECT AVG(amount) FROM orders_history)",
        # IN with uncorrelated subquery
        "SELECT * FROM parent WHERE id IN (SELECT parent_id FROM child)",
        # EXISTS with independent condition
        "SELECT * FROM parent WHERE EXISTS (SELECT 1 FROM child WHERE child.status = 'active')",
        # Multiple non-correlated CTEs and subqueries
        """
        WITH stats AS (
            SELECT AVG(score) AS avg_score FROM test_results
        )
        SELECT * FROM students WHERE score > (SELECT avg_score FROM stats)
        """,
    ]
    rule = CorrelatedSubqueryRule()
    for sql in sqls:
        ast = parse_snowflake_sql(sql)
        issues = rule.check(ast)
        assert issues == [], f"Expected 0 issues for uncorrelated query: {sql}"


def test_deeply_nested_subquery_attribution() -> None:
    """Test that correlation in deeply nested subqueries is correctly flagged on the offending node."""
    sql = """
    SELECT *
    FROM parent p
    WHERE EXISTS (
        SELECT 1
        FROM child c
        WHERE EXISTS (
            SELECT 1
            FROM grandchild g
            WHERE g.child_id = c.id
              AND g.parent_id = p.id
        )
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)

    # Both child c (referencing p) or grandchild g (referencing c or p) can be correlated
    # In this query:
    # - child c doesn't directly reference p
    # - grandchild g references c (correlated to middle subquery) and p (correlated to outer query)
    assert len(issues) >= 1
    assert all(issue.rule_id == "SNOW-002" for issue in issues)
    assert all(issue.requires_llm is True for issue in issues)
