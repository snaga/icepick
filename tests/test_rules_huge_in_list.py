"""Unit tests for SNOW-011 (HugeInListRule).

Verifies detection of IN clauses with excessive items (500+ literals),
recommending ARRAY_CONSTRUCT/ARRAY_CONTAINS or temporary tables.
"""

from __future__ import annotations

from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_011_huge_in_list import HugeInListRule
from icepick.parser import parse_snowflake_sql


def test_small_in_list_does_not_trigger() -> None:
    """Test that an IN list with a few items (below threshold) does not trigger."""
    sql = "SELECT id, name FROM users WHERE status IN ('ACTIVE', 'PENDING', 'SUSPENDED')"
    ast = parse_snowflake_sql(sql)
    rule = HugeInListRule()
    issues = rule.check(ast)

    assert len(issues) == 0


def test_huge_in_list_at_default_threshold_triggers_warning() -> None:
    """Test that an IN list with exactly 500 items triggers SNOW-011 warning."""
    items = ", ".join(str(i) for i in range(500))
    sql = f"SELECT * FROM orders WHERE customer_id IN ({items})"
    ast = parse_snowflake_sql(sql)

    rule = HugeInListRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-011"
    assert issue.rule_name == "Huge IN-List Optimizer Overload"
    assert issue.severity == Severity.MEDIUM
    assert issue.suggested_replacement is None
    assert issue.requires_llm is True
    assert issue.can_auto_fix is False
    assert isinstance(issue.target_node, exp.In)
    assert "Huge IN-list with 500 items" in issue.description
    assert "ARRAY_CONSTRUCT/ARRAY_CONTAINS" in issue.description


def test_custom_threshold_triggers() -> None:
    """Test that custom threshold allows testing smaller IN lists."""
    sql = "SELECT * FROM products WHERE category_id IN (1, 2, 3, 4, 5)"
    ast = parse_snowflake_sql(sql)

    # Default threshold 500: no issue
    assert len(HugeInListRule().check(ast)) == 0

    # Custom threshold 5: triggers on 5 items
    rule_5 = HugeInListRule(threshold=5)
    issues = rule_5.check(ast)
    assert len(issues) == 1
    assert "Huge IN-list with 5 items" in issues[0].description


def test_subquery_in_does_not_trigger() -> None:
    """Test that subquery IN (e.g. IN (SELECT id FROM t)) is ignored."""
    sql = "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM active_customers)"
    ast = parse_snowflake_sql(sql)

    rule = HugeInListRule(threshold=1)
    issues = rule.check(ast)

    assert len(issues) == 0


def test_not_in_clause_triggers() -> None:
    """Test that NOT IN with items exceeding threshold also triggers."""
    sql = "SELECT * FROM accounts WHERE status NOT IN ('A', 'B', 'C', 'D')"
    ast = parse_snowflake_sql(sql)

    rule = HugeInListRule(threshold=4)
    issues = rule.check(ast)

    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-011"
    assert "Huge IN-list with 4 items" in issues[0].description


def test_multiple_in_clauses_only_flags_qualifying() -> None:
    """Test that among multiple IN expressions, only those meeting threshold are flagged."""
    sql = """
    SELECT * FROM records
    WHERE status IN ('A', 'B')
      AND code IN (10, 20, 30, 40, 50, 60)
    """
    ast = parse_snowflake_sql(sql)

    rule = HugeInListRule(threshold=5)
    issues = rule.check(ast)

    assert len(issues) == 1
    assert "Huge IN-list with 6 items" in issues[0].description
