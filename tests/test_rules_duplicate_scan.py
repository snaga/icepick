"""Tests for SNOW-005: Duplicate Table Scan Detection rule.

Covers:
- Detection when 2 CTEs reference the same base table.
- Non-detection when all CTE base tables are distinct.
- Detection when 3 CTEs share the same table → exactly 1 issue (per table, not per CTE).
- Non-detection for queries with no WITH clause.
- Correct handling of schema-qualified table names (``schema.table``).
- Correct handling of sibling CTE references (CTE A references CTE B — not a base table).
- Verification of DiagnosticIssue metadata (rule_id, rule_name, severity, requires_llm).
- Case-insensitive table name comparison (``ORDERS`` == ``orders``).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from icepick.linter.base import Severity
from icepick.linter.rules.snow_005_duplicate_scan import DuplicateTableScanRule

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

RULE = DuplicateTableScanRule()


def _parse(sql: str) -> exp.Expression:
    """Parse *sql* and return its root AST node (asserts non-None)."""
    result = sqlglot.parse_one(sql, dialect="snowflake")
    assert isinstance(result, exp.Expression), f"sqlglot failed to parse: {sql!r}"
    return result


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestDuplicateTableScanRuleDetection:
    """Unit tests for DuplicateTableScanRule.check() — detection cases."""

    def test_detects_two_ctes_same_table(self) -> None:
        """Two CTEs referencing the same table must produce exactly one issue."""
        sql = (
            "WITH cte1 AS (SELECT * FROM orders), "
            "cte2 AS (SELECT * FROM orders WHERE status = 'open') "
            "SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.rule_id == "SNOW-005"
        assert issue.rule_name == "Duplicate Table Scan"
        assert issue.severity == Severity.MEDIUM
        assert issue.requires_llm is True
        assert issue.suggested_replacement is None

    def test_no_detection_distinct_tables(self) -> None:
        """CTEs referencing different tables must not produce any issue."""
        sql = (
            "WITH cte1 AS (SELECT * FROM orders), "
            "cte2 AS (SELECT * FROM customers) "
            "SELECT * FROM cte1 JOIN cte2 ON cte1.customer_id = cte2.id"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_three_ctes_same_table_yields_one_issue(self) -> None:
        """Three CTEs referencing the same table must produce exactly 1 issue.

        Issues are reported per duplicated table, not per CTE occurrence.
        """
        sql = (
            "WITH "
            "cte1 AS (SELECT * FROM orders), "
            "cte2 AS (SELECT id FROM orders WHERE status = 'open'), "
            "cte3 AS (SELECT id FROM orders WHERE status = 'closed') "
            "SELECT * FROM cte1"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        # One issue for the duplicated 'orders' table, regardless of CTE count.
        assert len(issues) == 1
        assert issues[0].rule_id == "SNOW-005"

    def test_no_detection_no_with_clause(self) -> None:
        """A plain SELECT with no WITH clause must not produce any issue."""
        sql = "SELECT * FROM orders WHERE status = 'open'"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_no_detection_single_cte(self) -> None:
        """A WITH clause with a single CTE cannot have duplicates."""
        sql = "WITH cte1 AS (SELECT * FROM orders) SELECT * FROM cte1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == [], f"Expected no issues but got: {issues}"

    def test_schema_qualified_table_detected(self) -> None:
        """Two CTEs referencing ``schema.table`` must be detected."""
        sql = (
            "WITH cte1 AS (SELECT * FROM mydb.orders), "
            "cte2 AS (SELECT id FROM mydb.orders WHERE status = 'open') "
            "SELECT * FROM cte1"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        # The description should reference the schema-qualified table name.
        assert "mydb.orders" in issues[0].description.lower()

    def test_schema_qualified_different_schemas_no_detection(self) -> None:
        """``schemaA.orders`` and ``schemaB.orders`` must NOT be conflated."""
        sql = (
            "WITH cte1 AS (SELECT * FROM schemaA.orders), "
            "cte2 AS (SELECT * FROM schemaB.orders) "
            "SELECT * FROM cte1"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        # Different schemas → distinct tables, no duplication.
        assert issues == [], f"Expected no issues but got: {issues}"

    def test_sibling_cte_reference_not_flagged(self) -> None:
        """A CTE referencing a sibling CTE must not be flagged as base-table duplication."""
        sql = (
            "WITH "
            "base AS (SELECT * FROM orders), "
            "filtered AS (SELECT * FROM base WHERE status = 'open') "
            "SELECT * FROM filtered"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        # 'base' is a CTE alias, not a base table — no duplicate scan.
        assert issues == [], f"Expected no issues but got: {issues}"

    def test_description_mentions_cte_names(self) -> None:
        """The issue description must name the CTEs that cause the duplicate scan."""
        sql = "WITH cte1 AS (SELECT * FROM orders), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        desc = issues[0].description
        assert "cte1" in desc
        assert "cte2" in desc

    def test_description_mentions_table_name(self) -> None:
        """The issue description must mention the duplicated table name."""
        sql = "WITH cte1 AS (SELECT * FROM orders), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert "orders" in issues[0].description.lower()

    def test_target_node_is_cte(self) -> None:
        """The ``target_node`` must be an ``exp.CTE`` instance."""
        sql = "WITH cte1 AS (SELECT * FROM orders), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert isinstance(issues[0].target_node, exp.CTE)

    def test_is_not_replaceable(self) -> None:
        """Issue must not be auto-replaceable (``is_replaceable`` is False)."""
        sql = "WITH cte1 AS (SELECT * FROM orders), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].is_replaceable is False

    def test_two_duplicate_pairs_yields_two_issues(self) -> None:
        """Four CTEs with two distinct duplicated tables must yield 2 issues."""
        sql = (
            "WITH "
            "a1 AS (SELECT * FROM orders), "
            "a2 AS (SELECT * FROM orders), "
            "b1 AS (SELECT * FROM customers), "
            "b2 AS (SELECT * FROM customers) "
            "SELECT 1"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        # One issue for 'orders', one for 'customers'.
        assert len(issues) == 2
        duplicated_tables = {i.description.split("'")[1] for i in issues}
        assert "orders" in duplicated_tables
        assert "customers" in duplicated_tables

    def test_detects_ctes_with_table_aliases(self) -> None:
        """CTEs using table aliases (e.g. orders o1, orders AS o2) must be detected as duplicates."""
        sql = (
            "WITH cte1 AS (SELECT * FROM orders o1), "
            "cte2 AS (SELECT * FROM orders AS o2) "
            "SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert issues[0].rule_id == "SNOW-005"
        assert "orders" in issues[0].description.lower()

    def test_no_duplicate_on_self_join_in_single_cte(self) -> None:
        """Self-joins within a single CTE must not trigger duplicate scan warnings."""
        sql = (
            "WITH cte1 AS ("
            "  SELECT o1.id, o2.parent_id "
            "  FROM orders o1 JOIN orders o2 ON o1.parent_id = o2.id"
            "), "
            "cte2 AS (SELECT * FROM customers) "
            "SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert issues == [], f"Expected no duplicate scan warnings but got: {issues}"


# ---------------------------------------------------------------------------
# Case-insensitivity tests
# ---------------------------------------------------------------------------


class TestDuplicateTableScanRuleCaseSensitivity:
    """Tests for case-insensitive table name comparison."""

    def test_case_insensitive_table_names(self) -> None:
        """``ORDERS`` and ``orders`` must be treated as the same table."""
        sql = "WITH cte1 AS (SELECT * FROM ORDERS), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        # Case difference should not prevent detection.
        assert len(issues) == 1

    def test_mixed_case_three_ctes_one_issue(self) -> None:
        """Three CTEs with mixed-case references to the same table → 1 issue."""
        sql = (
            "WITH "
            "c1 AS (SELECT * FROM Orders), "
            "c2 AS (SELECT * FROM ORDERS), "
            "c3 AS (SELECT * FROM orders) "
            "SELECT 1"
        )
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1


# ---------------------------------------------------------------------------
# Rule metadata tests
# ---------------------------------------------------------------------------


class TestDuplicateTableScanRuleMetadata:
    """Tests for class-level rule metadata."""

    def test_rule_metadata(self) -> None:
        """Class-level metadata must match the specification."""
        assert RULE.rule_id == "SNOW-005"
        assert RULE.rule_name == "Duplicate Table Scan"
        assert RULE.severity == Severity.MEDIUM

    def test_snippet_is_non_empty(self) -> None:
        """The ``snippet`` field must be a non-empty string."""
        sql = "WITH cte1 AS (SELECT * FROM orders), cte2 AS (SELECT * FROM orders) SELECT 1"
        ast = _parse(sql)
        issues = RULE.check(ast)

        assert len(issues) == 1
        assert isinstance(issues[0].snippet, str)
        assert len(issues[0].snippet) > 0
