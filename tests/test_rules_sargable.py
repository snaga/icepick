"""Unit tests for SNOW-001 (NonSargableRule)."""

import pytest
from sqlglot import exp

from icepick.linter import Severity
from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.parser import parse_snowflake_sql


def test_detect_date_function_equality() -> None:
    """Test detecting DATE(col) = '2024-01-01' and suggested range replacement."""
    sql = "SELECT * FROM events WHERE DATE(event_time) = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-001"
    assert issue.severity == Severity.HIGH
    assert "event_time" in issue.description
    assert issue.can_auto_fix is True
    assert issue.suggested_replacement is not None

    replacement_sql = issue.suggested_replacement.sql(dialect="snowflake")
    assert "event_time >= '2024-01-01'" in replacement_sql
    assert "event_time < DATEADD(DAY, 1, '2024-01-01')" in replacement_sql


def test_detect_reversed_equality() -> None:
    """Test detecting '2024-01-01' = DATE(event_time) (literal on left)."""
    sql = "SELECT * FROM events WHERE '2024-01-01' = DATE(event_time)"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.rule_id == "SNOW-001"
    assert issue.suggested_replacement is not None
    replacement_sql = issue.suggested_replacement.sql(dialect="snowflake")
    assert "event_time >= '2024-01-01'" in replacement_sql


def test_detect_to_date_equality() -> None:
    """Test detecting TO_DATE(created_at) = '2023-12-31'."""
    sql = "SELECT id FROM orders WHERE TO_DATE(created_at) = '2023-12-31'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert "created_at" in issue.description
    assert issue.suggested_replacement is not None
    replacement_sql = issue.suggested_replacement.sql(dialect="snowflake")
    assert "created_at >= '2023-12-31'" in replacement_sql


def test_detect_cast_as_date() -> None:
    """Test detecting CAST(ts AS DATE) = '2024-05-01'."""
    sql = "SELECT id FROM logs WHERE CAST(ts AS DATE) = '2024-05-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert "ts" in issue.description
    assert issue.suggested_replacement is not None
    replacement_sql = issue.suggested_replacement.sql(dialect="snowflake")
    assert "ts >= '2024-05-01'" in replacement_sql


def test_detect_table_qualified_column() -> None:
    """Test detecting DATE(t.event_time) = '2024-01-01' preserves table alias."""
    sql = "SELECT t.id FROM events t WHERE DATE(t.event_time) = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.suggested_replacement is not None
    replacement_sql = issue.suggested_replacement.sql(dialect="snowflake")
    assert "t.event_time >= '2024-01-01'" in replacement_sql


def test_detect_date_trunc() -> None:
    """Test detecting DATE_TRUNC('day', col) = '2024-01-01'."""
    sql = "SELECT id FROM metrics WHERE DATE_TRUNC('day', recorded_at) = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert "recorded_at" in issues[0].description


def test_detect_try_to_date() -> None:
    """Test detecting TRY_TO_DATE(str_date) = '2024-01-01'."""
    sql = "SELECT id FROM raw_logs WHERE TRY_TO_DATE(str_date) = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)

    assert len(issues) == 1
    assert "str_date" in issues[0].description


def test_no_issue_on_sargable_range_predicate() -> None:
    """Test that existing valid sargable range predicates produce no issues."""
    sql = """
    SELECT id, name
    FROM events
    WHERE event_time >= '2024-01-01' AND event_time < '2024-01-02'
    """
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_on_non_date_literals() -> None:
    """Test that comparisons with non-date literals do not trigger false positives."""
    sql = """
    SELECT *
    FROM users
    WHERE id = 100
      AND status = 'ACTIVE'
      AND role = 'ADMIN'
    """
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)
    assert issues == []


def test_no_issue_on_non_column_function() -> None:
    """Test that function calls not wrapping a column do not trigger issues."""
    sql = "SELECT * FROM users WHERE DATE('2024-01-01') = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)
    assert issues == []


def test_nested_expression_inside_date_function() -> None:
    """Test extracting column from nested function inside DATE or CAST."""
    # Nested expression inside DATE
    sql_date = "SELECT * FROM t WHERE DATE(COALESCE(col1, '2024-01-01')) = '2024-01-01'"
    ast_date = parse_snowflake_sql(sql_date)
    rule = NonSargableRule()
    issues_date = rule.check(ast_date)
    assert len(issues_date) == 1
    assert "col1" in issues_date[0].description

    # Nested expression inside CAST
    sql_cast = "SELECT * FROM t WHERE CAST(COALESCE(col2, '2024-01-01') AS DATE) = '2024-01-01'"
    ast_cast = parse_snowflake_sql(sql_cast)
    issues_cast = rule.check(ast_cast)
    assert len(issues_cast) == 1
    assert "col2" in issues_cast[0].description


def test_anonymous_date_function() -> None:
    """Test extracting column from exp.Anonymous node."""
    from icepick.linter.rules.snow_001_sargable import _extract_column_from_func

    col = exp.Column(this=exp.to_identifier("custom_col"))
    anon_node = exp.Anonymous(this="DATE", expressions=[col])
    extracted = _extract_column_from_func(anon_node)
    assert extracted is not None
    assert extracted.sql() == "custom_col"

    # Non-date anonymous function
    non_date_anon = exp.Anonymous(this="LOWER", expressions=[col])
    assert _extract_column_from_func(non_date_anon) is None


def test_parse_replacement_failure_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that if parsing the suggested replacement fails, it safely falls back."""
    import sqlglot

    sql = "SELECT * FROM events WHERE DATE(event_time) = '2024-01-01'"
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    # Mock parse_one to return None when generating replacement
    original_parse = sqlglot.parse_one

    from typing import Any

    def _mock_parse(s: str, *args: Any, **kwargs: Any) -> Any:
        if "DATEADD" in s:
            return None
        return original_parse(s, *args, **kwargs)

    monkeypatch.setattr(sqlglot, "parse_one", _mock_parse)
    issues = rule.check(ast)
    assert issues == []

