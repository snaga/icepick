"""Tests for TextSplicer (Source-Preserving Targeted Rewrite Engine).

Verifies that raw SQL formatting, user comments, custom indentation (e.g. 4-space),
lowercase keywords, and line breaks are preserved with 100% fidelity while
surgically applying DiagnosticIssue fixes.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from icepick.linter.base import DiagnosticIssue, Severity
from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.linter.rules.snow_006_union import UnionToUnionAllRule
from icepick.linter.rules.snow_008_redundant_distinct import RedundantDistinctRule
from icepick.parser import parse_snowflake_sql
from icepick.patcher.splicer import TextSplicer


def test_splice_non_sargable_preserves_raw_formatting() -> None:
    """Verify SNOW-001 replacement preserves 4-space indent, lowercase keywords, and comments."""
    raw_sql = """-- Important business query
select
    id,
    user_name
from users
where
    date(created_at) = '2026-09-01' -- important user logic
    and status = 'active'
"""
    ast = parse_snowflake_sql(raw_sql)
    issues = NonSargableRule().check(ast)
    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-001"

    splicer = TextSplicer()
    modified_sql, success = splicer.splice_issue(raw_sql, issues[0])

    assert success is True
    # Verify original comments are strictly preserved
    assert "-- Important business query\n" in modified_sql
    assert " -- important user logic" in modified_sql
    # Verify lowercase keywords are intact
    assert "select\n" in modified_sql
    assert "from users\n" in modified_sql
    assert "where\n" in modified_sql
    assert "    and status = 'active'" in modified_sql
    # Verify sargable replacement occurred
    assert "date(created_at) = '2026-09-01'" not in modified_sql
    assert "created_at >= '2026-09-01'" in modified_sql


def test_splice_redundant_sort_preserves_formatting() -> None:
    """Verify SNOW-003 deletion removes the ORDER BY line while preserving surrounding formatting."""
    raw_sql = """with user_cte as (
    -- Fetch active users
    select
        id,
        created_at
    from users
    order by created_at desc
)
select * from user_cte;
"""
    ast = parse_snowflake_sql(raw_sql)
    issues = RedundantSortRule().check(ast)
    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-003"

    splicer = TextSplicer()
    modified_sql, success = splicer.splice_issue(raw_sql, issues[0])

    assert success is True
    # Verify ORDER BY is removed
    assert "order by" not in modified_sql
    # Verify surrounding lines and comments are strictly intact
    assert "    -- Fetch active users\n" in modified_sql
    assert "    from users\n)\n" in modified_sql
    assert "select * from user_cte;\n" in modified_sql


def test_splice_multiple_issues_in_reverse_order() -> None:
    """Verify multiple issues (SNOW-001, SNOW-003, SNOW-006) are spliced in reverse order safely."""
    raw_sql = """-- Header comment
select
    u.id
from (
    select
        id,
        created_at
    from users
    order by created_at desc
) as u
where
    date(u.created_at) = '2026-09-01' -- sargable predicate
union
select
    a.id
from archive as a
"""
    ast = parse_snowflake_sql(raw_sql)
    issues: list[DiagnosticIssue] = []
    issues.extend(NonSargableRule().check(ast))
    issues.extend(RedundantSortRule().check(ast))
    issues.extend(UnionToUnionAllRule().check(ast))
    assert len(issues) == 3

    splicer = TextSplicer()
    modified_sql, applied = splicer.splice_all(raw_sql, issues)

    assert len(applied) == 3
    # Check that all fixes took effect
    assert "order by created_at desc" not in modified_sql
    assert "date(u.created_at) = '2026-09-01'" not in modified_sql
    assert "u.created_at >= '2026-09-01'" in modified_sql
    assert "union all" in modified_sql
    # Verify comments and formatting survived intact
    assert "-- Header comment\n" in modified_sql
    assert " -- sargable predicate\n" in modified_sql
    assert "    from users\n) as u" in modified_sql


def test_splice_multiple_same_rule_issues() -> None:
    """Verify multiple occurrences of the same rule in different branches are both replaced."""
    raw_sql = """select
    id
from t1
where
    date(t1.created_at) = '2026-01-01' -- branch 1
union all
select
    id
from t2
where
    date(t2.created_at) = '2026-02-01' -- branch 2
"""
    ast = parse_snowflake_sql(raw_sql)
    issues = NonSargableRule().check(ast)
    assert len(issues) == 2

    splicer = TextSplicer()
    modified_sql, applied = splicer.splice_all(raw_sql, issues)

    assert len(applied) == 2
    assert "date(t1.created_at) = '2026-01-01'" not in modified_sql
    assert "t1.created_at >= '2026-01-01'" in modified_sql
    assert "-- branch 1" in modified_sql

    assert "date(t2.created_at) = '2026-02-01'" not in modified_sql
    assert "t2.created_at >= '2026-02-01'" in modified_sql
    assert "-- branch 2" in modified_sql


def test_splice_unmatched_falls_back_safely() -> None:
    """Verify safe fallback (original_sql, False) when an issue node cannot be matched."""
    raw_sql = "select id from users where id = 100;"
    # Fabricate an issue for a node that does not exist in raw_sql
    bogus_node = sqlglot.parse_one("DATE(missing_col) = '2026-01-01'", read="snowflake")
    bogus_replacement = sqlglot.parse_one("missing_col >= '2026-01-01'", read="snowflake")
    assert isinstance(bogus_node, exp.Expression)
    assert isinstance(bogus_replacement, exp.Expression)
    issue = DiagnosticIssue(
        rule_id="SNOW-001",
        rule_name="Non-Sargable Predicate",
        severity=Severity.HIGH,
        description="Missing node test",
        target_node=bogus_node,
        snippet=bogus_node.sql(dialect="snowflake"),
        suggested_replacement=bogus_replacement,
    )

    splicer = TextSplicer()
    modified_sql, success = splicer.splice_issue(raw_sql, issue)

    assert success is False
    assert modified_sql == raw_sql

    # In splice_all, the issue should simply be skipped
    modified_all, applied = splicer.splice_all(raw_sql, [issue])
    assert modified_all == raw_sql
    assert len(applied) == 0


def test_splice_union_preserves_case() -> None:
    """Verify UNION replacement respects lowercase union and UPPERCASE UNION."""
    sql_lower = "select 1 from t1\nunion\nselect 2 from t2"
    ast_lower = parse_snowflake_sql(sql_lower)
    issues_lower = UnionToUnionAllRule().check(ast_lower)
    assert len(issues_lower) == 1

    splicer = TextSplicer()
    mod_lower, ok_lower = splicer.splice_issue(sql_lower, issues_lower[0])
    assert ok_lower is True
    assert "union all" in mod_lower

    sql_upper = "SELECT 1 FROM t1\nUNION\nSELECT 2 FROM t2"
    ast_upper = parse_snowflake_sql(sql_upper)
    issues_upper = UnionToUnionAllRule().check(ast_upper)
    assert len(issues_upper) == 1

    mod_upper, ok_upper = splicer.splice_issue(sql_upper, issues_upper[0])
    assert ok_upper is True
    assert "UNION ALL" in mod_upper


def test_splice_inline_order_by() -> None:
    """Verify inline ORDER BY (not occupying full line) is deleted without dropping other clauses."""
    raw_sql = "select id from users order by id desc;"
    ast = parse_snowflake_sql("select * from (select id from users order by id desc)")
    issues = RedundantSortRule().check(ast)
    assert len(issues) == 1

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issues[0])
    assert ok is True
    assert modified_sql == "select id from users;"


def test_splice_multiline_indent_adjustment() -> None:
    """Verify multiline replacement automatically inherits the target line's base indent."""
    from unittest.mock import MagicMock

    raw_sql = "select\n    id\nfrom t\nwhere\n    date(val) = '2026-01-01'"
    replacement_mock = MagicMock(spec=exp.Expression)
    replacement_mock.sql.return_value = "val >= '2026-01-01'\nAND val < '2026-01-02'"

    ast = parse_snowflake_sql(raw_sql)
    issues = NonSargableRule().check(ast)
    assert len(issues) == 1
    issues[0].suggested_replacement = replacement_mock

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issues[0])
    assert ok is True

    expected_part = "where\n    val >= '2026-01-01'\n    AND val < '2026-01-02'"
    assert expected_part in modified_sql


def test_splice_crlf_newlines_preserved() -> None:
    """Verify Windows CRLF (\\r\\n) line breaks are preserved during splicing."""
    raw_sql = "select\r\n    id\r\nfrom users\r\nwhere\r\n    date(created_at) = '2026-09-01'\r\n"
    ast = parse_snowflake_sql(raw_sql)
    issues = NonSargableRule().check(ast)
    assert len(issues) == 1

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issues[0])
    assert ok is True
    assert "\r\n" in modified_sql
    assert "date(created_at) = '2026-09-01'" not in modified_sql


def test_splicer_skips_comment_keyword() -> None:
    """Verify splicer ignores keyword in line comment and deletes DISTINCT from SQL body."""
    raw_sql = """-- SNOW-008: Redundant DISTINCT with GROUP BY
SELECT DISTINCT
    id,
    COUNT(*)
FROM emp
GROUP BY id;
"""
    ast = parse_snowflake_sql(raw_sql)
    issues = RedundantDistinctRule().check(ast)
    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-008"

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issues[0])
    assert ok is True
    # Verify the comment is 100% intact, not a single character removed
    assert "-- SNOW-008: Redundant DISTINCT with GROUP BY\n" in modified_sql
    # Verify DISTINCT in SQL body was removed
    assert "SELECT\n" in modified_sql
    assert "SELECT DISTINCT" not in modified_sql


def test_splicer_skips_block_comment_keyword() -> None:
    """Verify block comment containing DISTINCT is untouched while body DISTINCT is spliced."""
    raw_sql = (
        "/* Comment with DISTINCT inside */ SELECT DISTINCT id, COUNT(*) FROM emp GROUP BY id;"
    )
    ast = parse_snowflake_sql(raw_sql)
    issues = RedundantDistinctRule().check(ast)
    assert len(issues) == 1

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issues[0])
    assert ok is True
    assert "/* Comment with DISTINCT inside */" in modified_sql
    assert "SELECT id, COUNT(*) FROM emp GROUP BY id;" in modified_sql


def test_splicer_matches_optional_as() -> None:
    """Verify pattern with AS keyword matches raw SQL without AS keyword."""
    raw_sql = "SELECT * FROM (SELECT id FROM emp) sub WHERE rn = 1;"
    # sqlglot transpile / target_node contains 'AS sub'
    target_node = sqlglot.parse_one(
        "SELECT * FROM (SELECT id FROM emp) AS sub WHERE rn = 1", read="snowflake"
    )
    replacement_node = sqlglot.parse_one(
        "SELECT * FROM (SELECT id FROM emp) AS sub WHERE rn = 1 AND id > 0", read="snowflake"
    )
    assert isinstance(target_node, exp.Expression)
    assert isinstance(replacement_node, exp.Expression)

    issue = DiagnosticIssue(
        rule_id="TEST-001",
        rule_name="Optional AS Test",
        severity=Severity.LOW,
        description="Verify flexible AS matching",
        target_node=target_node,
        snippet=target_node.sql(dialect="snowflake"),
        suggested_replacement=replacement_node,
    )

    splicer = TextSplicer()
    modified_sql, ok = splicer.splice_issue(raw_sql, issue)
    assert ok is True
    assert "AND id > 0" in modified_sql


def test_comment_spans_ignore_string_literals() -> None:
    """Verify string literals containing comment delimiters are not treated as comments."""
    from icepick.patcher.splicer import _extract_comment_spans, _is_in_comment

    sql = "SELECT '-- not a comment' AS val, /* real comment */ 1;"
    spans = _extract_comment_spans(sql)
    assert len(spans) == 1
    # Only '/* real comment */' should be recognized as a comment span
    comment_text = sql[spans[0][0] : spans[0][1]]
    assert comment_text == "/* real comment */"

    lit_start = sql.find("'-- not a comment'")
    lit_end = lit_start + len("'-- not a comment'")
    assert not _is_in_comment(sql, lit_start, lit_end, spans)
