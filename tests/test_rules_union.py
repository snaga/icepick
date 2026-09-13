"""Tests for SNOW-006: UNION to UNION ALL rule and ASTPatcher integration.

Covers:
- Detection of ``UNION`` (distinct mode) in a simple two-branch query.
- Non-detection of ``UNION ALL`` queries.
- Detection of ``UNION`` nested inside a CTE.
- ASTPatcher replacement: patched SQL must contain ``UNION ALL`` instead of plain ``UNION``.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from icepick.linter.base import Severity
from icepick.linter.rules.snow_006_union import UnionToUnionAllRule
from icepick.patcher.in_place import ASTPatcher

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

RULE = UnionToUnionAllRule()
PATCHER = ASTPatcher(dialect="snowflake")


def _parse(sql: str) -> exp.Expression:
    """Parse *sql* and return its root AST node (asserts non-None)."""
    result = sqlglot.parse_one(sql, dialect="snowflake")
    assert isinstance(result, exp.Expression), f"sqlglot failed to parse: {sql!r}"
    return result


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestUnionToUnionAllRuleDetection:
    """Unit tests for UnionToUnionAllRule.check()."""

    def test_detects_union_distinct(self) -> None:
        """``SELECT 1 UNION SELECT 2`` must produce exactly one issue."""
        ast = _parse("SELECT 1 UNION SELECT 2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.rule_id == "SNOW-006"
        assert issue.rule_name == "UNION to UNION ALL"
        assert issue.severity == Severity.LOW
        assert issue.requires_llm is False
        assert issue.suggested_replacement is not None

    def test_no_detection_for_union_all(self) -> None:
        """``SELECT 1 UNION ALL SELECT 2`` must not trigger any issue."""
        ast = _parse("SELECT 1 UNION ALL SELECT 2")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_detects_union_in_cte(self) -> None:
        """UNION inside a CTE body must be detected."""
        sql = "WITH cte AS (SELECT 1 UNION SELECT 2) SELECT * FROM cte"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].rule_id == "SNOW-006"

    def test_no_detection_for_union_all_in_cte(self) -> None:
        """UNION ALL inside a CTE body must not produce issues."""
        sql = "WITH cte AS (SELECT 1 UNION ALL SELECT 2) SELECT * FROM cte"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == []

    def test_multiple_unions_all_detected(self) -> None:
        """Three-branch UNION should detect two UNION (distinct) nodes."""
        ast = _parse("SELECT 1 UNION SELECT 2 UNION SELECT 3")
        issues = RULE.check(ast)

        # sqlglot represents this as nested Union nodes; both should be detected.
        assert len(issues) == 2

    def test_mixed_union_and_union_all(self) -> None:
        """Only the UNION (distinct) branch is detected, not the UNION ALL branch."""
        # SELECT 1 UNION ALL SELECT 2 UNION SELECT 3
        # AST: Union(Union(1, ALL, 2), DISTINCT, 3)
        ast = _parse("SELECT 1 UNION ALL SELECT 2 UNION SELECT 3")
        issues = RULE.check(ast)

        # Only the outer UNION (distinct) should be reported.
        assert len(issues) == 1

    def test_issue_snippet_contains_union_keyword(self) -> None:
        """The snippet stored in the issue must include the UNION keyword."""
        ast = _parse("SELECT 1 UNION SELECT 2")
        issues = RULE.check(ast)

        assert "UNION" in issues[0].snippet


# ---------------------------------------------------------------------------
# ASTPatcher integration tests
# ---------------------------------------------------------------------------


class TestUnionToUnionAllPatcherIntegration:
    """Integration tests verifying that ASTPatcher correctly rewrites UNION → UNION ALL."""

    def test_patch_produces_union_all(self) -> None:
        """After patching, the generated SQL must contain UNION ALL and not bare UNION."""
        ast = _parse("SELECT 1 UNION SELECT 2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        patched_ast, applied = PATCHER.apply_all(ast, issues)

        assert len(applied) == 1, "Expected one applied fix"
        patched_sql = patched_ast.sql(dialect="snowflake")

        # Must contain UNION ALL
        assert "UNION ALL" in patched_sql, f"Expected UNION ALL in: {patched_sql!r}"

    def test_patched_sql_is_semantically_valid(self) -> None:
        """The patched SQL must re-parse without errors."""
        ast = _parse("SELECT 1 UNION SELECT 2")
        issues = RULE.check(ast)
        patched_ast, _ = PATCHER.apply_all(ast, issues)
        patched_sql = patched_ast.sql(dialect="snowflake")

        re_parsed = sqlglot.parse_one(patched_sql, dialect="snowflake")
        assert re_parsed is not None

    def test_patch_cte_union_to_union_all(self) -> None:
        """UNION inside a CTE is correctly rewritten to UNION ALL."""
        sql = "WITH cte AS (SELECT 1 UNION SELECT 2) SELECT * FROM cte"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        patched_ast, applied = PATCHER.apply_all(ast, issues)
        patched_sql = patched_ast.sql(dialect="snowflake")

        assert len(applied) == 1
        assert "UNION ALL" in patched_sql, f"Expected UNION ALL in: {patched_sql!r}"

    def test_union_all_not_modified(self) -> None:
        """A UNION ALL query must pass through the patcher unchanged."""
        original_sql = "SELECT 1 UNION ALL SELECT 2"
        ast = _parse(original_sql)
        issues = RULE.check(ast)

        assert issues == []
        # No issues → patcher applies nothing; SQL should be equivalent.
        _, applied = PATCHER.apply_all(ast, issues)
        assert applied == []

    def test_patch_multiple_unions(self) -> None:
        """Chained UNION (3 branches) must have all UNIONs rewritten to UNION ALL."""
        ast = _parse("SELECT 1 UNION SELECT 2 UNION SELECT 3")
        issues = RULE.check(ast)

        assert len(issues) == 2
        patched_ast, applied = PATCHER.apply_all(ast, issues)
        patched_sql = patched_ast.sql(dialect="snowflake")

        assert len(applied) == 2
        assert patched_sql == "SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3"
        assert "UNION" in patched_sql
        assert "UNION ALL" in patched_sql
        # Ensure no bare 'UNION' remains that is not 'UNION ALL'
        assert patched_sql.count("UNION ALL") == 2
        assert patched_sql.count("UNION") == 2
