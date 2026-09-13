"""Detroit-style unit tests for pure equivalence verification SQL generation.

Verifies deterministic bidirectional EXCEPT SQL generation without database execution dependencies.
"""

from __future__ import annotations

from icepick.verifier.equivalence import EquivalenceVerifier, generate_verification_sql


def test_generate_verification_sql_default() -> None:
    """Test generating standard bidirectional EXCEPT verification SQL with diff row output."""
    orig_sql = "SELECT id, name FROM users WHERE id > 10;"
    opt_sql = "SELECT id, name FROM users WHERE id > 10"

    sql = generate_verification_sql(orig_sql, opt_sql)

    # Header and comments
    assert "-- Icepick Equivalence Verification Query" in sql
    assert "-- Returns 0 rows if both queries are semantically equivalent." in sql

    # CTE definitions with trimmed semicolons
    assert "WITH orig AS (\nSELECT id, name FROM users WHERE id > 10\n)" in sql
    assert "opt AS (\nSELECT id, name FROM users WHERE id > 10\n)" in sql

    # Bidirectional EXCEPT diff rows
    assert "SELECT 'orig_not_in_opt' AS diff_type, * FROM (" in sql
    assert "SELECT * FROM orig EXCEPT SELECT * FROM opt" in sql
    assert "UNION ALL" in sql
    assert "SELECT 'opt_not_in_orig' AS diff_type, * FROM (" in sql
    assert "SELECT * FROM opt EXCEPT SELECT * FROM orig" in sql

    # Ends with single semicolon
    assert sql.endswith(");")
    assert not sql.endswith(";;")


def test_generate_verification_sql_count_only() -> None:
    """Test generating aggregated difference count verification SQL."""
    orig_sql = "SELECT id, val FROM metrics;"
    opt_sql = "SELECT id, val FROM metrics;"

    sql = generate_verification_sql(orig_sql, opt_sql, count_only=True)

    # Header comment
    assert "-- Icepick Equivalence Verification Count Query" in sql

    # CTE definitions
    assert "WITH orig AS (\nSELECT id, val FROM metrics\n)" in sql
    assert "opt AS (\nSELECT id, val FROM metrics\n)" in sql

    # Aggregation with COUNT(*) AS cnt
    assert "SELECT 'orig_not_in_opt' AS diff_type, COUNT(*) AS cnt FROM (" in sql
    assert "SELECT * FROM orig EXCEPT SELECT * FROM opt" in sql
    assert "UNION ALL" in sql
    assert "SELECT 'opt_not_in_orig' AS diff_type, COUNT(*) AS cnt FROM (" in sql
    assert "SELECT * FROM opt EXCEPT SELECT * FROM orig" in sql
    assert sql.endswith(");")


def test_generate_verification_sql_strips_trailing_semicolons_and_whitespace() -> None:
    """Test that trailing semicolons and extraneous whitespace are cleanly stripped."""
    orig_sql = "  \n SELECT x FROM t;;; \n "
    opt_sql = "SELECT x FROM t;   "

    sql = generate_verification_sql(orig_sql, opt_sql)

    assert "WITH orig AS (\nSELECT x FROM t\n)" in sql
    assert "opt AS (\nSELECT x FROM t\n)" in sql


def test_generate_verification_sql_complex_query_with_cte() -> None:
    """Test generating verification SQL when input queries already contain CTEs."""
    orig_sql = "WITH t AS (SELECT 1 AS c) SELECT c FROM t;"
    opt_sql = "SELECT 1 AS c;"

    sql = generate_verification_sql(orig_sql, opt_sql)

    assert "WITH orig AS (\nWITH t AS (SELECT 1 AS c) SELECT c FROM t\n)" in sql
    assert "opt AS (\nSELECT 1 AS c\n)" in sql


def test_equivalence_verifier_class_methods() -> None:
    """Test EquivalenceVerifier class methods generate_sql and backward-compatible build_verification_query."""
    verifier = EquivalenceVerifier(dialect="snowflake")
    orig_sql = "SELECT col1, col2 FROM table_a"
    opt_sql = "SELECT col1, col2 FROM table_b"

    # Default full diff mode
    sql_via_gen = verifier.generate_sql(orig_sql, opt_sql)
    sql_via_build = verifier.build_verification_query(orig_sql, opt_sql)
    sql_via_func = generate_verification_sql(orig_sql, opt_sql)

    assert sql_via_gen == sql_via_func
    assert sql_via_build == sql_via_func
    assert "SELECT 'orig_not_in_opt' AS diff_type, * FROM (" in sql_via_gen

    # Count only mode
    count_sql_via_gen = verifier.generate_sql(orig_sql, opt_sql, count_only=True)
    count_sql_via_build = verifier.build_verification_query(orig_sql, opt_sql, count_only=True)
    count_sql_via_func = generate_verification_sql(orig_sql, opt_sql, count_only=True)

    assert count_sql_via_gen == count_sql_via_func
    assert count_sql_via_build == count_sql_via_func
    assert "COUNT(*) AS cnt" in count_sql_via_gen
