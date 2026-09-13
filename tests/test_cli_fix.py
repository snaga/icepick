"""CLI tests for icepick fix command using typer.testing.CliRunner.

Validates in-place optimization prescription application with --rx filtering,
--dry-run simulation, non-interactive environment safety guards (--force),
interactive confirmation prompting, error handling for invalid prescription IDs,
clean queries, and syntax errors according to ADR-0005 and requirements G-3 & E-6.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from icepick.cli import app

runner = CliRunner()


class TestCliFix:
    """Test suite for the 'icepick fix' CLI command."""

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

    def test_cli_fix_all_prescriptions_with_force(self, multi_issue_sql_file: Path) -> None:
        """Verify that all prescriptions are applied to the target file when --force is used."""
        result = runner.invoke(app, ["fix", str(multi_issue_sql_file), "--force"])
        assert result.exit_code == 0
        assert "Successfully applied" in result.output
        assert "RX-001" in result.output
        assert "RX-002" in result.output

        modified_content = multi_issue_sql_file.read_text(encoding="utf-8")
        # Sargable fix applied
        assert "created_at >= '2023-01-01'" in modified_content
        assert "DATE(created_at)" not in modified_content
        # Redundant sort removed
        assert "ORDER BY" not in modified_content
        # Structural CTE/wrapping preserved
        assert "SELECT * FROM (" in modified_content
        assert ") sub" in modified_content

    def test_cli_fix_selected_rx_only(self, multi_issue_sql_file: Path) -> None:
        """Verify that --rx RX-001 --force applies only RX-001 and leaves RX-002 untouched."""
        result = runner.invoke(
            app, ["fix", str(multi_issue_sql_file), "--rx", "RX-001", "--force"]
        )
        assert result.exit_code == 0
        assert "Successfully applied 1 prescription(s)" in result.output
        assert "RX-001" in result.output

        modified_content = multi_issue_sql_file.read_text(encoding="utf-8")
        # RX-001 (sargable) applied
        assert "created_at >= '2023-01-01'" in modified_content
        # RX-002 (redundant sort) untouched
        assert "ORDER BY id" in modified_content

    def test_cli_fix_dry_run_does_not_modify_file(self, multi_issue_sql_file: Path) -> None:
        """Verify that --dry-run simulates the fix without modifying the file."""
        original_content = multi_issue_sql_file.read_text(encoding="utf-8")
        result = runner.invoke(app, ["fix", str(multi_issue_sql_file), "--dry-run"])
        assert result.exit_code == 0
        assert "Simulated in-place fix" in result.output
        assert "Dry-run complete." in result.output
        assert "would be applied" in result.output

        # File content must remain strictly unchanged
        assert multi_issue_sql_file.read_text(encoding="utf-8") == original_content

    def test_cli_fix_non_interactive_without_force_fails(
        self, multi_issue_sql_file: Path
    ) -> None:
        """Verify that non-interactive environment without --force exits 1 and guards the file."""
        original_content = multi_issue_sql_file.read_text(encoding="utf-8")
        with patch("sys.stdin.isatty", return_value=False):
            result = runner.invoke(app, ["fix", str(multi_issue_sql_file)])
        assert result.exit_code == 1
        output_combined = result.output + (result.stderr if hasattr(result, "stderr") and result.stderr else "")
        assert "Non-interactive environment detected without --force" in output_combined

        # File must remain untouched
        assert multi_issue_sql_file.read_text(encoding="utf-8") == original_content

    def test_cli_fix_interactive_confirm_yes(self, multi_issue_sql_file: Path) -> None:
        """Verify that interactive confirmation with 'y' applies fixes in-place."""
        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            result = runner.invoke(app, ["fix", str(multi_issue_sql_file)], input="y\n")
        assert result.exit_code == 0
        assert "Successfully applied" in result.output

        modified_content = multi_issue_sql_file.read_text(encoding="utf-8")
        assert "created_at >= '2023-01-01'" in modified_content

    def test_cli_fix_interactive_confirm_no(self, multi_issue_sql_file: Path) -> None:
        """Verify that interactive confirmation with 'n' aborts without modifying file."""
        original_content = multi_issue_sql_file.read_text(encoding="utf-8")
        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            result = runner.invoke(app, ["fix", str(multi_issue_sql_file)], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output

        # File must remain untouched
        assert multi_issue_sql_file.read_text(encoding="utf-8") == original_content

    def test_cli_fix_invalid_rx_shows_actionable_error(
        self, multi_issue_sql_file: Path
    ) -> None:
        """Verify that invalid prescription ID displays actionable error with available IDs and exits 1."""
        original_content = multi_issue_sql_file.read_text(encoding="utf-8")
        result = runner.invoke(
            app, ["fix", str(multi_issue_sql_file), "--rx", "RX-999", "--force"]
        )
        assert result.exit_code == 1
        output_combined = result.output + (result.stderr if hasattr(result, "stderr") and result.stderr else "")
        assert "Invalid prescription ID(s): RX-999" in output_combined
        assert "Available IDs:" in output_combined
        assert "RX-001" in output_combined

        assert multi_issue_sql_file.read_text(encoding="utf-8") == original_content

    def test_cli_fix_clean_query_no_changes(self, tmp_path: Path) -> None:
        """Verify that clean query results in exit 0 with no changes applied."""
        clean_file = tmp_path / "clean.sql"
        clean_sql = "SELECT id, name FROM users WHERE id = 1;\n"
        clean_file.write_text(clean_sql, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(clean_file), "--force"])
        assert result.exit_code == 0
        assert "No changes applied" in result.output
        assert clean_file.read_text(encoding="utf-8") == clean_sql

    def test_cli_fix_syntax_error(self, tmp_path: Path) -> None:
        """Verify that invalid SQL syntax results in parse error with line info and exits 2."""
        broken_file = tmp_path / "syntax_error.sql"
        broken_file.write_text("SELECT FROM WHERE ;", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(broken_file), "--force"])
        assert result.exit_code == 2
        output_combined = result.output + (result.stderr if hasattr(result, "stderr") and result.stderr else "")
        assert "Parse Error:" in output_combined
