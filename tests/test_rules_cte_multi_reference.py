"""Unit tests for SNOW-010 (CteMultiReferenceRule).

Verifies detection of CTEs referenced 3 or more times within the query,
recommending materialization into a TEMPORARY TABLE.
"""

from __future__ import annotations

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_010_cte_multi_reference import CteMultiReferenceRule
from icepick.parser import parse_snowflake_sql


def test_cte_below_threshold_does_not_trigger() -> None:
    """Test that CTE referenced only 2 times (below threshold 3) does not trigger."""
    sql = """
    WITH regional_sales AS (
        SELECT region, SUM(amount) AS total
        FROM sales
        GROUP BY region
    )
    SELECT * FROM regional_sales
    UNION ALL
    SELECT * FROM regional_sales
    """
    ast = parse_snowflake_sql(sql)
    rule = CteMultiReferenceRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_cte_at_threshold_triggers_warning() -> None:
    """Test that CTE referenced exactly 3 times triggers SNOW-010 warning."""
    sql = """
    WITH regional_sales AS (
        SELECT region, SUM(amount) AS total
        FROM sales
        GROUP BY region
    )
    SELECT * FROM regional_sales WHERE total > 100
    UNION ALL
    SELECT * FROM regional_sales WHERE total > 200
    UNION ALL
    SELECT * FROM regional_sales WHERE total > 300
    """
    ast = parse_snowflake_sql(sql)
    rule = CteMultiReferenceRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-010"
    assert issue.rule_name == "CTE Multiple References (Materialization Warning)"
    assert issue.severity == Severity.LOW
    assert issue.suggested_replacement is None
    assert issue.requires_llm is True
    assert issue.can_auto_fix is False
    assert isinstance(issue.target_node, exp.CTE)
    assert issue.target_node.alias.lower() == "regional_sales"
    assert "CTE 'regional_sales' is referenced 3 times." in issue.description
    assert "TEMPORARY TABLE" in issue.description


def test_multiple_ctes_only_flags_qualifying_cte() -> None:
    """Test that among multiple CTEs, only the one referenced >= 3 times is flagged."""
    sql = """
    WITH heavy_calc AS (
        SELECT id, complex_func(val) AS res FROM base_data
    ),
    light_meta AS (
        SELECT id, name FROM metadata
    )
    SELECT * FROM heavy_calc h1
    JOIN heavy_calc h2 ON h1.id = h2.id
    JOIN heavy_calc h3 ON h2.id = h3.id
    JOIN light_meta lm ON h1.id = lm.id
    """
    ast = parse_snowflake_sql(sql)
    rule = CteMultiReferenceRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].target_node.alias.lower() == "heavy_calc"
    assert "referenced 3 times" in issues[0].description


def test_no_cte_query_returns_empty_list() -> None:
    """Test that queries without CTEs safely return an empty issue list."""
    sql = "SELECT id, name FROM users WHERE active = TRUE"
    ast = parse_snowflake_sql(sql)
    rule = CteMultiReferenceRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_cte_referenced_in_another_cte_and_main_query() -> None:
    """Test that CTE referenced within sibling CTEs and main query are accumulated correctly."""
    sql = """
    WITH step1 AS (
        SELECT x, y FROM raw_events
    ),
    step2 AS (
        SELECT x, AVG(y) FROM step1 GROUP BY x
    ),
    step3 AS (
        SELECT s1.x FROM step1 s1 JOIN step2 s2 ON s1.x = s2.x
    )
    SELECT * FROM step1
    """
    # step1 is referenced in step2 (1), step3 (2), and main SELECT (3) = 3 references
    # step2 is referenced in step3 (1) = 1 reference
    # step3 is not referenced (0)
    ast = parse_snowflake_sql(sql)
    rule = CteMultiReferenceRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].target_node.alias.lower() == "step1"
    assert "referenced 3 times" in issues[0].description


def test_custom_threshold() -> None:
    """Test that a customized threshold triggers on fewer references."""
    sql = """
    WITH temp_cte AS (
        SELECT 1 AS val
    )
    SELECT * FROM temp_cte
    UNION ALL
    SELECT * FROM temp_cte
    """
    ast = parse_snowflake_sql(sql)

    # Default threshold 3: no issues
    assert len(CteMultiReferenceRule().check(ast)) == 0

    # Custom threshold 2: 1 issue
    rule_custom = CteMultiReferenceRule(threshold=2)
    issues = rule_custom.check(ast)
    assert len(issues) == 1
    assert "referenced 2 times" in issues[0].description
