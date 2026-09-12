"""London-style unit tests for EquivalenceVerifier module using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock

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

    def test_verify_identical_results_is_equivalent(self) -> None:
        """Test that 0 diffs in both directions evaluates to is_equivalent=True."""
        verifier = EquivalenceVerifier()
        orig = "SELECT 1"
        opt = "SELECT 1"

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("orig_not_in_opt", 0),
            ("opt_not_in_orig", 0),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = verifier.verify(orig, opt, mock_conn)

        assert isinstance(result, VerificationResult)
        assert result.is_equivalent is True
        assert result.orig_not_in_opt_count == 0
        assert result.opt_not_in_orig_count == 0
        assert result.error_message is None
        mock_cursor.execute.assert_called_once()
        mock_cursor.close.assert_called_once()

    def test_verify_orig_not_in_opt_difference(self) -> None:
        """Test that original query having extra rows marks query as not equivalent."""
        verifier = EquivalenceVerifier()
        orig = "SELECT id FROM t1"
        opt = "SELECT id FROM t1 WHERE id > 5"

        # Test passing a cursor directly instead of a connection
        mock_cursor = MagicMock(spec=["execute", "fetchall"])
        mock_cursor.fetchall.return_value = [
            ("orig_not_in_opt", 10),
            ("opt_not_in_orig", 0),
        ]

        result = verifier.verify(orig, opt, mock_cursor)

        assert result.is_equivalent is False
        assert result.orig_not_in_opt_count == 10
        assert result.opt_not_in_orig_count == 0
        assert result.error_message is None

    def test_verify_opt_not_in_orig_difference(self) -> None:
        """Test that optimized query having extra rows marks query as not equivalent."""
        verifier = EquivalenceVerifier()
        orig = "SELECT id FROM t1 WHERE id > 5"
        opt = "SELECT id FROM t1"

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("orig_not_in_opt", 0),
            ("opt_not_in_orig", 42),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = verifier.verify(orig, opt, mock_conn)

        assert result.is_equivalent is False
        assert result.orig_not_in_opt_count == 0
        assert result.opt_not_in_orig_count == 42

    def test_verify_dict_row_format(self) -> None:
        """Test verification when driver returns dictionary rows."""
        verifier = EquivalenceVerifier()
        orig = "SELECT a FROM t"
        opt = "SELECT a FROM t"

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"diff_type": "orig_not_in_opt", "cnt": 0},
            {"diff_type": "opt_not_in_orig", "cnt": 0},
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = verifier.verify(orig, opt, mock_conn)
        assert result.is_equivalent is True
        assert result.orig_not_in_opt_count == 0
        assert result.opt_not_in_orig_count == 0

    def test_verify_incomplete_result_rows(self) -> None:
        """Test handling when query returns missing difference rows."""
        verifier = EquivalenceVerifier()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("orig_not_in_opt", 0),
            # Missing second row
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = verifier.verify("SELECT 1", "SELECT 1", mock_conn)
        assert result.is_equivalent is False
        assert "missing expected difference rows" in (result.error_message or "")

    def test_verify_execution_exception_handled_safely(self) -> None:
        """Test that database execution error produces a failed result without raising exception."""
        verifier = EquivalenceVerifier()
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = RuntimeError("Snowflake connection timeout")

        result = verifier.verify("SELECT 1", "SELECT 1", mock_conn)

        assert result.is_equivalent is False
        assert result.orig_not_in_opt_count == -1
        assert result.opt_not_in_orig_count == -1
        assert "Snowflake connection timeout" in (result.error_message or "")
