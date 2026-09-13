"""Unit tests for EquivalenceVerifier module focusing on deterministic SQL generation."""

from __future__ import annotations

from icepick.verifier.equivalence import EquivalenceVerifier, VerificationResult


class TestEquivalenceVerifier:
    """Unit tests for EquivalenceVerifier."""

    def test_build_verification_query_basic(self) -> None:
        """Test generating verification query from simple queries."""
        verifier = EquivalenceVerifier()
        orig = "SELECT id, name FROM users WHERE id > 10;"
        opt = "SELECT id, name FROM users WHERE id > 10"

        sql = verifier.build_verification_query(orig, opt)

        assert "WITH orig AS (" in sql
        assert "SELECT id, name FROM users WHERE id > 10" in sql
        assert "opt AS (" in sql
        assert "SELECT 'orig_not_in_opt' AS diff_type" in sql
        assert "SELECT 'opt_not_in_orig' AS diff_type" in sql
        assert "EXCEPT" in sql
        assert "UNION ALL" in sql
        assert not sql.startswith(";")

    def test_build_verification_query_with_cte(self) -> None:
        """Test generating verification query when inputs already contain CTEs."""
        verifier = EquivalenceVerifier()
        orig = "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte"
        opt = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"

        sql = verifier.build_verification_query(orig, opt)

        assert "WITH orig AS (" in sql
        assert "WITH cte AS (SELECT 1 AS x)" in sql
        assert "opt AS (" in sql

    def test_generate_sql_count_only(self) -> None:
        """Test generate_sql with count_only=True generates count aggregation query."""
        verifier = EquivalenceVerifier()
        orig = "SELECT col FROM t"
        opt = "SELECT col FROM t WHERE 1=1"

        sql = verifier.generate_sql(orig, opt, count_only=True)

        assert "COUNT(*) AS cnt" in sql
        assert "diff_type" in sql
        assert "UNION ALL" in sql

    def test_verification_result_dataclass(self) -> None:
        """Test VerificationResult dataclass fields and behavior."""
        res = VerificationResult(
            is_equivalent=True,
            orig_not_in_opt_count=0,
            opt_not_in_orig_count=0,
            verification_sql="-- verification query",
        )
        assert res.is_equivalent is True
        assert res.orig_not_in_opt_count == 0
        assert res.opt_not_in_orig_count == 0
        assert res.verification_sql == "-- verification query"
        assert res.error_message is None
