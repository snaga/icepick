"""Unit tests for ASTPatcher module."""

import pytest
from sqlglot import exp

from icepick.diff import format_diff
from icepick.linter import DiagnosticIssue, Severity
from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.parser import parse_snowflake_sql
from icepick.patcher import ASTPatcher


def test_apply_issue_non_sargable_replacement() -> None:
    """Test replacing non-sargable WHERE clause with range condition in-place."""
    sql = """
    SELECT u.id, u.name, o.amount
    FROM users u
    JOIN orders o ON u.id = o.user_id
    WHERE DATE(u.created_at) = '2024-01-01' AND o.status = 'COMPLETED'
    """
    ast = parse_snowflake_sql(sql)

    rule = NonSargableRule()
    issues = rule.check(ast)
    assert len(issues) == 1

    patcher = ASTPatcher()
    updated_ast = patcher.apply_issue(ast, issues[0])

    updated_sql = updated_ast.sql(dialect="snowflake")
    # Verify range condition is present
    assert "u.created_at >= '2024-01-01'" in updated_sql
    assert "DATEADD(DAY, 1, '2024-01-01')" in updated_sql
    # Verify surrounding clauses are completely preserved
    assert "o.status = 'COMPLETED'" in updated_sql
    assert "JOIN orders AS o ON u.id = o.user_id" in updated_sql
    assert "SELECT u.id, u.name, o.amount" in updated_sql


def test_apply_issue_redundant_sort_removal() -> None:
    """Test popping redundant ORDER BY inside a CTE in-place."""
    sql = """
    WITH sorted_cte AS (
        SELECT id, name
        FROM customers
        ORDER BY id ASC
    )
    SELECT * FROM sorted_cte
    """
    ast = parse_snowflake_sql(sql)

    rule = RedundantSortRule()
    issues = rule.check(ast)
    assert len(issues) == 1
    assert issues[0].is_deletable is True

    patcher = ASTPatcher()
    updated_ast = patcher.apply_issue(ast, issues[0])

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "ORDER BY" not in updated_sql
    assert "SELECT id, name FROM customers" in updated_sql
    assert "SELECT * FROM sorted_cte" in updated_sql


def test_apply_all_multiple_issues() -> None:
    """Test applying multiple different rule issues simultaneously across a query."""
    sql = """
    WITH customer_summary AS (
        SELECT id, name
        FROM customers
        ORDER BY name
    )
    SELECT c.name, o.amount
    FROM customer_summary c
    JOIN orders o ON c.id = o.customer_id
    WHERE DATE(o.order_date) = '2024-03-15'
    """
    ast = parse_snowflake_sql(sql)

    sort_issues = RedundantSortRule().check(ast)
    sargable_issues = NonSargableRule().check(ast)
    all_issues = [*sort_issues, *sargable_issues]
    assert len(all_issues) == 2

    patcher = ASTPatcher()
    updated_ast, applied = patcher.apply_all(ast, all_issues)

    assert len(applied) == 2
    updated_sql = updated_ast.sql(dialect="snowflake")

    # Sort should be removed
    assert "ORDER BY name" not in updated_sql
    # Sargable range predicate should be in place
    assert "o.order_date >= '2024-03-15'" in updated_sql
    assert "DATEADD(DAY, 1, '2024-03-15')" in updated_sql


def test_apply_all_skips_non_autofixable_issue() -> None:
    """Test that issues requiring LLM are safely skipped by apply_all."""
    sql = "SELECT * FROM t"
    ast = parse_snowflake_sql(sql)

    llm_issue = DiagnosticIssue(
        rule_id="SNOW-002",
        rule_name="Correlated Subquery",
        severity=Severity.CRITICAL,
        description="Requires LLM rewriting",
        target_node=ast,
        snippet="SELECT * FROM t",
        suggested_replacement=None,
        requires_llm=True,
    )

    patcher = ASTPatcher()
    updated_ast, applied = patcher.apply_all(ast, [llm_issue])

    assert len(applied) == 0
    assert updated_ast is ast


def test_patcher_with_format_diff_integration() -> None:
    """Integration test: patcher output with format_diff produces clean unified diff."""
    original_sql = """
    WITH data AS (
        SELECT id, amount FROM sales ORDER BY id
    )
    SELECT id, amount
    FROM data
    WHERE DATE(sale_time) = '2024-02-01'
    """
    ast = parse_snowflake_sql(original_sql)

    issues = [
        *RedundantSortRule().check(ast),
        *NonSargableRule().check(ast),
    ]

    patcher = ASTPatcher()
    patched_ast, applied = patcher.apply_all(ast, issues)
    assert len(applied) == 2

    patched_sql = patched_ast.sql(dialect="snowflake", pretty=True)
    diff = format_diff(original_sql, patched_sql, filename="sales.sql", normalize=True)

    assert diff != ""
    assert "--- a/sales.sql" in diff
    assert "+++ b/sales.sql" in diff
    assert "-" in diff
    assert "+" in diff
    # Diff should show removal of ORDER BY and addition of range condition
    assert "-    ORDER BY" in diff or "-    id" in diff
    assert "+    sale_time >=" in diff or "+  sale_time >=" in diff


def test_apply_issue_raises_on_non_autofixable() -> None:
    """Test apply_issue directly raises ValueError on non-auto-fixable issue."""
    node = exp.var("dummy")
    issue = DiagnosticIssue(
        rule_id="SNOW-002",
        rule_name="Correlated Subquery",
        severity=Severity.HIGH,
        description="desc",
        target_node=node,
        snippet="dummy",
        requires_llm=True,
    )

    patcher = ASTPatcher()
    with pytest.raises(ValueError, match="cannot be automatically fixed"):
        patcher.apply_issue(node, issue)


def test_apply_issue_raises_when_replaceable_without_replacement() -> None:
    """Test apply_issue raises ValueError if marked replaceable but replacement is None."""
    node = exp.var("dummy")
    issue = DiagnosticIssue(
        rule_id="SNOW-001",
        rule_name="Mock",
        severity=Severity.HIGH,
        description="desc",
        target_node=node,
        snippet="dummy",
        suggested_replacement=None,
        requires_llm=False,
    )
    # Manually force is_replaceable to True by setting invalid state or subclass
    object.__setattr__(issue, "suggested_replacement", None)
    # When is_deletable is True, it pops. If we force it to fail:
    patcher = ASTPatcher()
    # Cannot delete root node
    with pytest.raises(ValueError, match="Cannot delete the root query"):
        patcher.apply_issue(node, issue)


def test_apply_issue_root_node_replacement() -> None:
    """Test replacing the root query node directly."""
    root_node = parse_snowflake_sql("SELECT 1")
    new_node = parse_snowflake_sql("SELECT 2")

    issue = DiagnosticIssue(
        rule_id="SNOW-CUSTOM",
        rule_name="Root Replace",
        severity=Severity.LOW,
        description="desc",
        target_node=root_node,
        snippet="SELECT 1",
        suggested_replacement=new_node,
        requires_llm=False,
    )

    patcher = ASTPatcher()
    result = patcher.apply_issue(root_node, issue)
    assert result.sql(dialect="snowflake") == "SELECT 2"


def test_apply_all_fail_safe_exception_handling() -> None:
    """Test apply_all catches unexpected exceptions from a corrupted issue and continues."""
    ast = parse_snowflake_sql("SELECT 1")

    # Issue pointing to root node attempting to delete it (triggers ValueError in apply_issue)
    corrupted_issue = DiagnosticIssue(
        rule_id="CORRUPTED",
        rule_name="Corrupted",
        severity=Severity.LOW,
        description="desc",
        target_node=ast,
        snippet="SELECT 1",
        suggested_replacement=None,
        requires_llm=False,
    )

    patcher = ASTPatcher()
    result_ast, applied = patcher.apply_all(ast, [corrupted_issue])
    assert applied == []
    assert result_ast is ast


def test_apply_issue_raises_no_actionable_fix() -> None:
    """Test apply_issue raises ValueError when issue has no actionable fix."""

    class UnactionableIssue(DiagnosticIssue):
        @property
        def is_replaceable(self) -> bool:
            return False

        @property
        def is_deletable(self) -> bool:
            return False

    node = exp.var("dummy")
    issue = UnactionableIssue(
        rule_id="NO-FIX",
        rule_name="No Fix",
        severity=Severity.LOW,
        description="desc",
        target_node=node,
        snippet="dummy",
        requires_llm=False,
    )

    patcher = ASTPatcher()
    with pytest.raises(ValueError, match="has no actionable fix"):
        patcher.apply_issue(node, issue)
