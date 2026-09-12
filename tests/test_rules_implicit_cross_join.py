"""Tests for SNOW-004: Implicit Cross Join Detection rule.

Covers:
- Detection of comma-separated FROM clause with two tables (``FROM t1, t2``).
- Non-detection of explicit ``CROSS JOIN``.
- Non-detection of explicit ``JOIN … ON`` (inner join with predicate).
- Detection of three comma-separated tables (``FROM t1, t2, t3``).
- Detection of implicit cross join inside a CTE body.
- Non-detection of ``LEFT JOIN``, ``NATURAL JOIN`` (other explicit join forms).
- Verification of DiagnosticIssue metadata (rule_id, severity, requires_llm).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from icepick.linter.base import Severity
from icepick.linter.rules.snow_004_implicit_cross_join import ImplicitCrossJoinRule

# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------

RULE = ImplicitCrossJoinRule()


def _parse(sql: str) -> exp.Expression:
    """Parse *sql* and return its root AST node (asserts non-None)."""
    result = sqlglot.parse_one(sql, dialect="snowflake")
    assert isinstance(result, exp.Expression), f"sqlglot failed to parse: {sql!r}"
    return result


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestImplicitCrossJoinRuleDetection:
    """Unit tests for ImplicitCrossJoinRule.check()."""

    def test_detects_two_table_comma_from(self) -> None:
        """``SELECT a, b FROM t1, t2`` must produce exactly one issue."""
        ast = _parse("SELECT a, b FROM t1, t2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.rule_id == "SNOW-004"
        assert issue.rule_name == "Implicit Cross Join"
        assert issue.severity == Severity.HIGH
        assert issue.requires_llm is True
        # No automated replacement — user/LLM must determine the predicate.
        assert issue.suggested_replacement is None

    def test_no_detection_for_explicit_cross_join(self) -> None:
        """``FROM t1 CROSS JOIN t2`` must not trigger any issue."""
        ast = _parse("SELECT a FROM t1 CROSS JOIN t2")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_no_detection_for_join_with_on(self) -> None:
        """``FROM t1 JOIN t2 ON t1.id = t2.id`` must not trigger any issue."""
        ast = _parse("SELECT a FROM t1 JOIN t2 ON t1.id = t2.id")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_detects_three_table_comma_from(self) -> None:
        """``FROM t1, t2, t3`` must produce two issues (one per implicit join)."""
        ast = _parse("SELECT a, b, c FROM t1, t2, t3")
        issues = RULE.check(ast)

        # sqlglot generates two implicit Join nodes: t2 and t3.
        assert len(issues) == 2
        assert all(i.rule_id == "SNOW-004" for i in issues)

    def test_detects_implicit_cross_join_in_cte(self) -> None:
        """An implicit cross join inside a CTE body must be detected."""
        sql = "WITH cte AS (SELECT a, b FROM t1, t2) SELECT * FROM cte"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].rule_id == "SNOW-004"

    def test_no_detection_for_left_join(self) -> None:
        """``LEFT JOIN … ON`` must not be flagged as implicit cross join."""
        ast = _parse("SELECT a FROM t1 LEFT JOIN t2 ON t1.id = t2.id")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_no_detection_for_natural_join(self) -> None:
        """``NATURAL JOIN`` must not be flagged as implicit cross join."""
        ast = _parse("SELECT a FROM t1 NATURAL JOIN t2")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_no_detection_for_join_using(self) -> None:
        """``JOIN … USING (col)`` must not be flagged as implicit cross join."""
        ast = _parse("SELECT a FROM t1 JOIN t2 USING (id)")
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_no_detection_single_table(self) -> None:
        """A query with a single table in FROM must not produce any issue."""
        ast = _parse("SELECT a FROM t1")
        issues = RULE.check(ast)

        assert issues == []

    def test_snippet_contains_table_name(self) -> None:
        """The ``snippet`` field must reference the right-hand table."""
        ast = _parse("SELECT a, b FROM t1, t2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        # The Join node's SQL representation is ", t2" for a comma join.
        assert "t2" in issues[0].snippet

    def test_description_mentions_both_tables(self) -> None:
        """The issue description should mention both tables involved in the join."""
        ast = _parse("SELECT a, b FROM t1, t2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        desc = issues[0].description
        assert "t1" in desc
        assert "t2" in desc

    def test_target_node_is_join(self) -> None:
        """The ``target_node`` must be a ``exp.Join`` instance."""
        ast = _parse("SELECT a, b FROM t1, t2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert isinstance(issues[0].target_node, exp.Join)

    def test_is_not_replaceable(self) -> None:
        """Issue must not be auto-replaceable (``is_replaceable`` is False)."""
        ast = _parse("SELECT a, b FROM t1, t2")
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].is_replaceable is False

    def test_detects_implicit_cross_join_in_subquery(self) -> None:
        """Implicit cross join nested inside a subquery must be detected."""
        sql = "SELECT * FROM (SELECT a, b FROM t1, t2) AS sub"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].rule_id == "SNOW-004"

    def test_no_false_positive_mixed_joins(self) -> None:
        """A query with one explicit join and no comma join must not be flagged."""
        sql = "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id JOIN t3 ON t2.id = t3.id"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_rule_metadata(self) -> None:
        """Class-level metadata must match the spec."""
        assert RULE.rule_id == "SNOW-004"
        assert RULE.rule_name == "Implicit Cross Join"
        assert RULE.severity == Severity.HIGH

    def test_no_detection_for_lateral_flatten(self) -> None:
        """Snowflake TABLE(FLATTEN(...)) and LATERAL FLATTEN(...) must not be detected as cross joins."""
        queries = [
            "SELECT * FROM t1, TABLE(FLATTEN(input => t1.arr)) f",
            "SELECT * FROM t1, LATERAL FLATTEN(input => t1.arr) f",
        ]
        for sql in queries:
            ast = _parse(sql)
            issues = RULE.check(ast)
            assert issues == [], f"Expected no issues for {sql!r} but got: {issues}"

