"""Unit tests for diff formatter and renderer module."""

from rich.syntax import Syntax

from icepick.diff import (
    DiffFormatter,
    format_diff,
    normalize_sql,
    render_diff,
    split_hunks,
)


def test_format_diff_with_semantic_change() -> None:
    """Test generating unified diff when there is a meaningful SQL modification."""
    original_sql = "SELECT id, name FROM users WHERE DATE(created_at) = '2023-01-01'"
    modified_sql = (
        "SELECT id, name FROM users "
        "WHERE created_at >= '2023-01-01' AND created_at < '2023-01-02'"
    )

    diff = format_diff(original_sql, modified_sql, filename="models/users.sql")

    assert diff != ""
    assert "--- a/models/users.sql" in diff
    assert "+++ b/models/users.sql" in diff
    assert "@@ " in diff
    assert "-  TO_DATE" in diff or "-  DATE" in diff
    assert "+  created_at >=" in diff
    assert diff.endswith("\n")


def test_format_diff_noise_suppression_with_normalize() -> None:
    """Test that indentation/whitespace differences produce empty diff when normalized."""
    original_sql = """
    SELECT
        a,
        b
    FROM
        table_name
    WHERE
        id = 10
    """

    # Identical semantics, different indentation and casing of keywords
    whitespace_diff_sql = "select   a, b   from table_name where   id = 10"

    # With normalize=True (default), diff must be completely empty
    normalized_diff = format_diff(original_sql, whitespace_diff_sql, normalize=True)
    assert normalized_diff == ""

    # With normalize=False, diff detects the raw line differences
    raw_diff = format_diff(original_sql, whitespace_diff_sql, normalize=False)
    assert raw_diff != ""
    assert "-" in raw_diff
    assert "+" in raw_diff


def test_format_diff_identical_sql() -> None:
    """Test that completely identical SQL returns an empty diff string."""
    sql = "SELECT 1 AS col"
    diff = format_diff(sql, sql)
    assert diff == ""


def test_render_diff_returns_rich_syntax() -> None:
    """Test rendering diff returns a properly configured Rich Syntax instance."""
    diff_text = """--- a/test.sql
+++ b/test.sql
@@ -1,1 +1,1 @@
-SELECT 1
+SELECT 2
"""
    syntax = render_diff(diff_text, theme="ansi_dark", line_numbers=True)

    assert isinstance(syntax, Syntax)
    assert syntax.code == diff_text
    assert syntax.line_numbers is True


def test_diff_formatter_class() -> None:
    """Test DiffFormatter class wrapper."""
    formatter = DiffFormatter(dialect="snowflake", theme="monokai")
    assert formatter.dialect == "snowflake"
    assert formatter.theme == "monokai"

    orig = "SELECT col_a FROM tbl"
    mod = "SELECT col_b FROM tbl"

    diff = formatter.format(orig, mod, filename="query.sql")
    assert "col_a" in diff
    assert "col_b" in diff

    syntax = formatter.render(diff)
    assert isinstance(syntax, Syntax)
    assert syntax.code == diff


def test_split_hunks() -> None:
    """Test splitting unified diff into individual DiffHunk components."""
    diff_text = """--- a/test.sql
+++ b/test.sql
@@ -1,3 +1,3 @@
-SELECT a
+SELECT b
 FROM tbl
@@ -10,3 +10,3 @@
-WHERE x = 1
+WHERE x = 2
 ORDER BY id
"""
    hunks = split_hunks(diff_text)
    assert len(hunks) == 2

    assert hunks[0].header == "@@ -1,3 +1,3 @@"
    assert "-SELECT a" in hunks[0].lines
    assert "+SELECT b" in hunks[0].lines
    assert hunks[0].to_text().startswith("@@ -1,3 +1,3 @@\n")

    assert hunks[1].header == "@@ -10,3 +10,3 @@"
    assert "-WHERE x = 1" in hunks[1].lines
    assert "+WHERE x = 2" in hunks[1].lines

    # Empty diff
    assert split_hunks("") == []
    assert split_hunks("   \n ") == []


def test_normalize_sql() -> None:
    """Test normalize_sql helper directly."""
    raw = "select  id,   name   from users"
    norm = normalize_sql(raw)
    assert "SELECT" in norm
    assert "FROM users" in norm
