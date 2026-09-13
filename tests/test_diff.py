"""Unit tests for diff formatter and renderer module."""

import pytest
from rich.syntax import Syntax

from icepick.diff import (
    DiffFormatter,
    apply_unified_diff,
    format_diff,
    normalize_sql,
    render_diff,
    split_hunks,
)


def test_format_diff_with_semantic_change() -> None:
    """Test generating unified diff when there is a meaningful SQL modification."""
    original_sql = "SELECT id, name FROM users WHERE DATE(created_at) = '2023-01-01'"
    modified_sql = (
        "SELECT id, name FROM users WHERE created_at >= '2023-01-01' AND created_at < '2023-01-02'"
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


def test_apply_unified_diff_single_hunk() -> None:
    """Test applying a single hunk patch correctly modifies target text."""
    original = "SELECT id, name\nFROM users\nWHERE id = 1\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -3,1 +3,1 @@
-WHERE id = 1
+WHERE id = 2
"""
    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1
    assert patched == "SELECT id, name\nFROM users\nWHERE id = 2\n"


def test_apply_unified_diff_multiple_hunks() -> None:
    """Test applying all hunks in a multi-hunk patch."""
    original = "SELECT a\nFROM tbl\nWHERE x = 1\nGROUP BY a\nORDER BY a\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -1,1 +1,1 @@
-SELECT a
+SELECT a, b
@@ -5,1 +5,1 @@
-ORDER BY a
+ORDER BY b
"""
    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 2
    assert applied == 2
    assert "SELECT a, b" in patched
    assert "ORDER BY b" in patched
    assert "WHERE x = 1" in patched


def test_apply_unified_diff_selected_hunks() -> None:
    """Test applying only selected hunks while skipping unselected hunks."""
    original = "SELECT a\nFROM tbl\nWHERE x = 1\nORDER BY a\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -1,1 +1,1 @@
-SELECT a
+SELECT a, b
@@ -4,1 +4,1 @@
-ORDER BY a
+ORDER BY id
"""
    # Apply only Hunk 0
    patched_0, applied_0, total_0 = apply_unified_diff(original, diff, selected_hunks=[0])
    assert total_0 == 2
    assert applied_0 == 1
    assert "SELECT a, b" in patched_0
    assert "ORDER BY a" in patched_0  # unchanged

    # Apply only Hunk 1
    patched_1, applied_1, total_1 = apply_unified_diff(original, diff, selected_hunks=[1])
    assert total_1 == 2
    assert applied_1 == 1
    assert "SELECT a\n" in patched_1  # unchanged
    assert "ORDER BY id" in patched_1

    # Skip all hunks
    patched_none, applied_none, total_none = apply_unified_diff(original, diff, selected_hunks=[])
    assert total_none == 2
    assert applied_none == 0
    assert patched_none == original


def test_apply_unified_diff_crlf_preservation() -> None:
    """Test that CRLF line endings are preserved when applying LF unified diff."""
    original = "SELECT id\r\nFROM users\r\nWHERE id = 1\r\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -3,1 +3,1 @@
-WHERE id = 1
+WHERE id = 2
"""
    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1
    assert "\r\n" in patched
    assert patched == "SELECT id\r\nFROM users\r\nWHERE id = 2\r\n"


def test_apply_unified_diff_empty_or_no_hunks() -> None:
    """Test that empty diff or diff without hunks returns original text and 0 counts."""
    original = "SELECT 1;\n"
    patched, applied, total = apply_unified_diff(original, "")
    assert patched == original
    assert applied == 0
    assert total == 0

    patched_header_only, applied_ho, total_ho = apply_unified_diff(
        original, "--- a/q.sql\n+++ b/q.sql\n"
    )
    assert patched_header_only == original
    assert applied_ho == 0
    assert total_ho == 0


def test_apply_unified_diff_with_no_newline_marker() -> None:
    """Test applying a diff containing '\\ No newline at end of file' marker safely."""
    original = "SELECT 1\nFROM tbl\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -1,2 +1,2 @@
-SELECT 1
+SELECT 2
\\ No newline at end of file
 FROM tbl
"""
    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1
    assert patched == "SELECT 2\nFROM tbl\n"


def test_apply_unified_diff_mismatch_raises_value_error() -> None:
    """Test that applying a diff with mismatched original lines raises ValueError."""
    original = "SELECT id, name FROM users\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -1,1 +1,1 @@
-SELECT non_existent_column FROM orders
+SELECT id FROM users
"""
    with pytest.raises(ValueError, match="Hunk target lines could not be matched"):
        apply_unified_diff(original, diff)


def test_apply_unified_diff_start_idx_prevents_rewind() -> None:
    """Test that second hunk searches forward from current_orig_idx and does not rewind to match previous identical lines."""
    original = "SELECT 1\nSELECT 2\nSELECT 1\n"
    diff = """--- a/query.sql
+++ b/query.sql
@@ -1,1 +1,1 @@
-SELECT 1
+SELECT A
@@ -3,1 +3,1 @@
-SELECT 1
+SELECT B
"""
    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 2
    assert applied == 2
    assert patched == "SELECT A\nSELECT 2\nSELECT B\n"


def test_apply_unified_diff_indent_re_targeting() -> None:
    """Test that a 2-space indented unified diff cleanly applies to a 4-space indented file.

    Verifies Indent Re-targeting:
      - Old lines in the hunk with 2 spaces match the 4-space lines via strip() fallback.
      - Inserted lines (+ lines) are automatically re-indented to 4 spaces to seamlessly
        blend into the original file's formatting style.
    """
    original = "SELECT\n    id,\n    name\nFROM\n    users\nWHERE\n    id = 1\n"

    # Patch created with 2-space indentation
    diff = (
        "--- a/query.sql\n"
        "+++ b/query.sql\n"
        "@@ -6,2 +6,3 @@\n"
        " WHERE\n"
        "-  id = 1\n"
        "+  id = 2\n"
        "+  AND active = TRUE\n"
    )

    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1

    expected = (
        "SELECT\n    id,\n    name\nFROM\n    users\nWHERE\n    id = 2\n    AND active = TRUE\n"
    )
    assert patched == expected


def test_apply_unified_diff_fuzzy_casing_and_whitespace() -> None:
    """Test that diff with lowercase keywords and single spaces matches original SQL with uppercase and extra spaces.

    Verifies Fuzzy Token Match:
      - Whitespace sequences and case differences between hunk old lines and original lines
        are safely resolved via token normalization.
      - Correctly retargets indentation to match original line indent level.
    """
    original = (
        "SELECT\n"
        "    ID,\n"
        "    NAME\n"
        "FROM\n"
        "    USERS\n"
        "WHERE\n"
        "    DATE(CREATED_AT)   =   '2023-01-01'\n"
    )

    # Patch with lowercase SQL and normalized single spaces
    diff = (
        "--- a/query.sql\n"
        "+++ b/query.sql\n"
        "@@ -6,2 +6,2 @@\n"
        " where\n"
        "-    date(created_at) = '2023-01-01'\n"
        "+    created_at >= '2023-01-01' and created_at < '2023-01-02'\n"
    )

    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1

    expected = (
        "SELECT\n"
        "    ID,\n"
        "    NAME\n"
        "FROM\n"
        "    USERS\n"
        "WHERE\n"
        "    created_at >= '2023-01-01' and created_at < '2023-01-02'\n"
    )
    assert patched == expected


def test_find_matching_position_fallback_tiers() -> None:
    """Test _find_matching_position multi-tier fallback progression: exact, rstrip, strip, fuzzy."""
    from icepick.diff.patcher import _find_matching_position

    orig_lines = [
        "SELECT a,",
        "    b,",
        "    c    ",  # has trailing spaces
        "FROM tbl",
        "WHERE ID = 100",
    ]

    # 1. Exact match
    idx, mode = _find_matching_position(orig_lines, ["SELECT a,", "    b,"], hint_idx=0)
    assert idx == 0
    assert mode == "exact"

    # 2. rstrip match (patch line has no trailing spaces, orig has trailing spaces)
    idx, mode = _find_matching_position(orig_lines, ["    c"], hint_idx=2)
    assert idx == 2
    assert mode == "rstrip"

    # 3. strip match (indentation difference: patch has 2 spaces, orig has 4 spaces)
    idx, mode = _find_matching_position(orig_lines, ["  b,"], hint_idx=1)
    assert idx == 1
    assert mode == "strip"

    # 4. Fuzzy match (lowercase and collapsed spaces)
    idx, mode = _find_matching_position(orig_lines, ["where   id = 100"], hint_idx=4)
    assert idx == 4
    assert mode == "fuzzy"


def test_apply_unified_diff_tab_indent_re_targeting() -> None:
    """Test applying a space-indented diff to a tab-indented original file."""
    original = "SELECT\n\tid,\n\tname\nFROM\n\tusers\nWHERE\n\tid = 1\n"
    diff = (
        "--- a/query.sql\n"
        "+++ b/query.sql\n"
        "@@ -6,2 +6,3 @@\n"
        " WHERE\n"
        "-  id = 1\n"
        "+  id = 2\n"
        "+  AND active = TRUE\n"
    )

    patched, applied, total = apply_unified_diff(original, diff)
    assert total == 1
    assert applied == 1

    expected = "SELECT\n\tid,\n\tname\nFROM\n\tusers\nWHERE\n\tid = 2\n\tAND active = TRUE\n"
    assert patched == expected
