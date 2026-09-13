"""CLI tests for icepick diff command using typer.testing.CliRunner.

Validates unified diff generation with optional prescription ID filtering (--rx),
output to file (-o / --output), handling of clean queries, invalid prescription IDs,
and syntax errors according to ADR-0005 and requirement G-2.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from icepick.cli import app

runner = CliRunner()


class TestCliDiff:
    """Test suite for the 'icepick diff' command."""

    @pytest.fixture
    def multi_issue_sql_file(self, tmp_path: Path) -> Path:
        """Create a temporary SQL file with multiple anti-patterns (sargable and redundant sort)."""
        sql_file = tmp_path / "multi_issues.sql"
        sql = (
            "SELECT * FROM (\n"
            "    SELECT id, name FROM raw_data ORDER BY id\n"
            ") sub\n"
            "WHERE DATE(created_at) = '2023-01-01';\n"
        )
        sql_file.write_text(sql, encoding="utf-8")
        return sql_file

    def test_cli_diff_default_all(self, multi_issue_sql_file: Path) -> None:
        """Verify that without --rx, diff for all detected prescriptions is generated with exit code 0."""
        result = runner.invoke(app, ["diff", str(multi_issue_sql_file)])
        assert result.exit_code == 0
        output = result.output
        assert "--- a/multi_issues.sql" in output
        assert "+++ b/multi_issues.sql" in output
        # Redundant sort removed
        assert "-    SELECT id, name FROM raw_data ORDER BY id" in output
        assert "+    SELECT id, name FROM raw_data" in output
        # Non-sargable replaced
        assert "-WHERE DATE(created_at) = '2023-01-01';" in output
        assert "created_at >= '2023-01-01'" in output

    def test_cli_diff_with_rx_single(self, multi_issue_sql_file: Path) -> None:
        """Verify that --rx RX-001 applies only that single prescription and keeps other parts untouched."""
        # RX-001 is NonSargableRule
        result = runner.invoke(app, ["diff", str(multi_issue_sql_file), "--rx", "RX-001"])
        assert result.exit_code == 0
        output = result.output
        assert "--- a/multi_issues.sql" in output
        assert "+++ b/multi_issues.sql" in output
        # Non-sargable is replaced
        assert "created_at >= '2023-01-01'" in output
        # ORDER BY is untouched in the diff (not modified)
        assert not any(
            line.strip().startswith(("-", "+")) and "ORDER BY" in line
            for line in output.splitlines()
        )

    def test_cli_diff_with_rx_multiple(self, tmp_path: Path) -> None:
        """Verify that --rx RX-001,RX-002 applies multiple selected prescriptions."""
        sql_file = tmp_path / "three_issues.sql"
        sql = (
            "SELECT id, val FROM t1\n"
            "UNION\n"
            "SELECT id, val FROM (\n"
            "    SELECT id, val FROM t2 ORDER BY id\n"
            ")\n"
            "WHERE DATE(created_at) = '2023-01-01';\n"
        )
        sql_file.write_text(sql, encoding="utf-8")

        # Apply RX-001 (sargable) and RX-002 (redundant sort)
        result = runner.invoke(app, ["diff", str(sql_file), "--rx", "RX-001,RX-002"])
        assert result.exit_code == 0
        output = result.output
        assert "created_at >= '2023-01-01'" in output
        assert "-    SELECT id, val FROM t2 ORDER BY id" in output
        assert "+    SELECT id, val FROM t2" in output
        # UNION distinct is not changed to UNION ALL because RX-003 was not selected
        assert not any(
            line.strip().startswith(("-", "+")) and "UNION" in line for line in output.splitlines()
        )

    def test_cli_diff_invalid_rx_shows_actionable_error(self, multi_issue_sql_file: Path) -> None:
        """Verify that --rx RX-999 outputs actionable error with available IDs and exits 1."""
        result = runner.invoke(app, ["diff", str(multi_issue_sql_file), "--rx", "RX-999"])
        assert result.exit_code == 1
        output = result.output
        assert "Error:" in output
        assert "Invalid prescription ID(s): RX-999" in output
        assert "Available IDs:" in output
        assert "RX-001" in output

    def test_cli_diff_output_to_file(self, multi_issue_sql_file: Path, tmp_path: Path) -> None:
        """Verify that -o / --output saves the unified diff to the specified patch file."""
        patch_file = tmp_path / "patches" / "changes.patch"
        result = runner.invoke(app, ["diff", str(multi_issue_sql_file), "-o", str(patch_file)])
        assert result.exit_code == 0
        assert "Unified diff saved to" in result.output
        assert patch_file.name in result.output
        assert patch_file.exists()
        patch_content = patch_file.read_text(encoding="utf-8")
        assert "--- a/multi_issues.sql" in patch_content
        assert "+++ b/multi_issues.sql" in patch_content
        assert "created_at >= '2023-01-01'" in patch_content

    def test_cli_diff_clean_query_no_diff(self, tmp_path: Path) -> None:
        """Verify that a clean query produces a message indicating no diff and exits 0."""
        clean_file = tmp_path / "clean.sql"
        clean_file.write_text("SELECT id, name FROM users WHERE id = 42;", encoding="utf-8")

        result = runner.invoke(app, ["diff", str(clean_file)])
        assert result.exit_code == 0
        assert "No diff generated" in result.output
        assert "already optimal" in result.output

    def test_cli_diff_syntax_error(self, tmp_path: Path) -> None:
        """Verify that a syntax error in the SQL file reports error and exits 2."""
        bad_file = tmp_path / "syntax_error.sql"
        bad_file.write_text("SELECT FROM WHERE ;;;", encoding="utf-8")

        result = runner.invoke(app, ["diff", str(bad_file)])
        assert result.exit_code == 2
        assert "Parse Error:" in result.output

    def test_cli_diff_invalid_dialect_error(self, multi_issue_sql_file: Path) -> None:
        """Verify that an unsupported SQL dialect produces an error and exits 1."""
        result = runner.invoke(app, ["diff", str(multi_issue_sql_file), "--dialect", "oracle"])
        assert result.exit_code == 1
        assert "error: Invalid dialect 'oracle'" in result.output

    def test_cli_diff_with_valid_dialect(self, tmp_path: Path) -> None:
        """Verify that a supported alternative dialect like duckdb works with diff."""
        sql_file = tmp_path / "duckdb.sql"
        sql = "SELECT * FROM (\n    SELECT id, name FROM raw_data ORDER BY id\n) sub;\n"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["diff", str(sql_file), "--dialect", "duckdb"])
        assert result.exit_code == 0
        assert "--- a/duckdb.sql" in result.output
