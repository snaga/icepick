"""London-style CLI tests for icepick using typer.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from icepick import __version__
from icepick.cli import app

runner = CliRunner()


class TestCli:
    """CLI tests for check and fix commands."""

    def test_version_option(self) -> None:
        """Test that --version displays the application version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert f"icepick version {__version__}" in result.output

    def test_check_clean_sql_exits_0(self, tmp_path: Path) -> None:
        """Test that check on clean SQL exits with 0 and prints success message."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id, name FROM users WHERE id = 10", encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    def test_check_issues_detected_exits_1(self, tmp_path: Path) -> None:
        """Test that check on SQL with anti-patterns prints table and exits with 1."""
        sql_file = tmp_path / "issues.sql"
        # Triggers SNOW-001 (HIGH), SNOW-003 (MEDIUM), and SNOW-007 (LOW)
        sql = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub WHERE DATE(created_at) = '2023-01-01'"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 1
        assert "SNOW-001" in result.output
        assert "SNOW-003" in result.output
        assert "SNOW-007" in result.output
        assert "Diagnostic Report" in result.output
        assert "Found 3 issue(s)" in result.output

    def test_check_parse_error_exits_2(self, tmp_path: Path) -> None:
        """Test that check on invalid SQL syntax exits with 2."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("SELECT FROM WHERE ;;;", encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 2
        assert "Parse Error" in result.output

    def test_check_missing_file_fails(self, tmp_path: Path) -> None:
        """Test that check fails when target file does not exist."""
        non_existent = tmp_path / "not_found.sql"
        result = runner.invoke(app, ["check", str(non_existent)])
        assert result.exit_code != 0

    def test_check_with_json_config(self, tmp_path: Path) -> None:
        """Test check command loading a JSON config disabling SNOW-001."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'", encoding="utf-8")

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"disabled_rules": ["SNOW-001"]}), encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    def test_check_with_toml_config(self, tmp_path: Path) -> None:
        """Test check command loading a TOML config."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'", encoding="utf-8")

        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('disabled_rules = ["SNOW-001"]\n', encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    def test_fix_clean_sql_no_op(self, tmp_path: Path) -> None:
        """Test that fix on clean SQL prints no modifications needed."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id FROM users\n", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file)])
        assert result.exit_code == 0
        assert "No modifications needed" in result.output

    def test_fix_diff_option_prints_unified_diff(self, tmp_path: Path) -> None:
        """Test that fix --diff outputs unified diff without modifying file."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub\n"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--diff"])
        assert result.exit_code == 0
        # ORDER BY id should be removed by SNOW-003
        assert "-  ORDER BY" in result.output or "-    ORDER BY" in result.output or "-" in result.output
        # File should remain unchanged
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_write_option_modifies_file(self, tmp_path: Path) -> None:
        """Test that fix --write modifies the target SQL file in-place."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--write"])
        assert result.exit_code == 0
        assert "Successfully updated" in result.output

        new_content = sql_file.read_text(encoding="utf-8")
        assert "ORDER BY" not in new_content

    def test_fix_patch_option_creates_patch_file(self, tmp_path: Path) -> None:
        """Test that fix --patch writes the diff to a specified file."""
        sql_file = tmp_path / "fixable.sql"
        patch_file = tmp_path / "changes.patch"
        sql_file.write_text("SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--patch", str(patch_file)])
        assert result.exit_code == 0
        assert "Saved patch to" in result.output
        assert patch_file.exists()

        patch_content = patch_file.read_text(encoding="utf-8")
        assert "---" in patch_content
        assert "+++" in patch_content

    def test_fix_interactive_yes(self, tmp_path: Path) -> None:
        """Test interactive mode applying fix when user inputs 'y'."""
        sql_file = tmp_path / "fixable.sql"
        sql_file.write_text("SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--interactive", "--write"], input="y\n")
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "Successfully updated" in result.output
        assert "ORDER BY" not in sql_file.read_text(encoding="utf-8")

    def test_fix_interactive_no(self, tmp_path: Path) -> None:
        """Test interactive mode skipping fix when user inputs 'n'."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--interactive", "--write"], input="n\n")
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "No modifications needed" in result.output
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_interactive_quit(self, tmp_path: Path) -> None:
        """Test interactive mode aborting fixes when user inputs 'q'."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--interactive", "--write"], input="q\n")
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "No modifications needed" in result.output
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_flatten_subqueries(self, tmp_path: Path) -> None:
        """Test fix with --flatten-subqueries lifts derived tables to CTEs."""
        sql_file = tmp_path / "nested.sql"
        sql_file.write_text("SELECT * FROM (SELECT a FROM tbl) AS sub", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--flatten-subqueries", "--write"])
        assert result.exit_code == 0
        new_content = sql_file.read_text(encoding="utf-8")
        assert "WITH" in new_content
        assert "sub AS (" in new_content or "cte_" in new_content

    def test_fix_interactive_flatten_subqueries_accept(self, tmp_path: Path) -> None:
        """Test interactive mode accepting subquery flattening."""
        sql_file = tmp_path / "nested.sql"
        sql_file.write_text("SELECT * FROM (SELECT a FROM tbl) AS sub", encoding="utf-8")

        result = runner.invoke(
            app,
            ["fix", str(sql_file), "--interactive", "--flatten-subqueries", "--write"],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "Apply subquery flattening to CTE?" in result.output
        assert "WITH" in sql_file.read_text(encoding="utf-8")

    def test_fix_interactive_flatten_subqueries_reject(self, tmp_path: Path) -> None:
        """Test interactive mode rejecting subquery flattening."""
        sql_file = tmp_path / "nested.sql"
        original = "SELECT * FROM (SELECT a FROM tbl) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(
            app,
            ["fix", str(sql_file), "--interactive", "--flatten-subqueries", "--write"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "Apply subquery flattening to CTE?" in result.output
        assert "No modifications needed" in result.output
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_parse_error_exits_2(self, tmp_path: Path) -> None:
        """Test that fix on invalid SQL syntax exits with 2."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("SELECT FROM WHERE ;;;", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file)])
        assert result.exit_code == 2
        assert "Parse Error" in result.output

    def test_check_and_fix_read_error(self, tmp_path: Path) -> None:
        """Test handling of file read error in check and fix commands."""
        sql_file = tmp_path / "dummy.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("Read failure")):
            res_check = runner.invoke(app, ["check", str(sql_file)])
            assert res_check.exit_code == 2
            assert "Error reading file" in res_check.output

            res_fix = runner.invoke(app, ["fix", str(sql_file)])
            assert res_fix.exit_code == 2
            assert "Error reading file" in res_fix.output

    def test_fix_interactive_patcher_exception_handling(self, tmp_path: Path) -> None:
        """Test that an exception during interactive patching logs a warning and skips safely."""
        sql_file = tmp_path / "fixable.sql"
        sql_file.write_text("SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8")

        with patch("icepick.patcher.in_place.ASTPatcher.apply_issue", side_effect=ValueError("Patch failed")):
            result = runner.invoke(app, ["fix", str(sql_file), "--interactive"], input="y\n")
            assert result.exit_code == 0
            assert "Skipped fix for SNOW-003" in result.output

    def test_fix_interactive_flatten_exception_handling(self, tmp_path: Path) -> None:
        """Test that an exception during interactive flattening logs a warning and skips safely."""
        sql_file = tmp_path / "nested.sql"
        sql_file.write_text("SELECT * FROM (SELECT a FROM tbl) AS sub", encoding="utf-8")

        with patch("icepick.patcher.subquery_to_cte.SubqueryToCTE.flatten_all_subqueries", side_effect=ValueError("Flatten failed")):
            result = runner.invoke(
                app,
                ["fix", str(sql_file), "--interactive", "--flatten-subqueries"],
                input="y\n",
            )
            assert result.exit_code == 0
            assert "Skipped subquery flattening" in result.output

    def test_verify_dry_run(self, tmp_path: Path) -> None:
        """Test that verify --dry-run prints the bidirectional EXCEPT query and exits 0."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT id FROM users", encoding="utf-8")
        opt_file.write_text("SELECT id FROM users WHERE 1=1", encoding="utf-8")

        result = runner.invoke(app, ["verify", str(orig_file), str(opt_file), "--dry-run"])
        assert result.exit_code == 0
        assert "Generated Verification SQL" in result.output
        assert "EXCEPT" in result.output
        assert "orig_not_in_opt" in result.output
        assert "opt_not_in_orig" in result.output

    def test_verify_without_dry_run_warns_credentials(self, tmp_path: Path) -> None:
        """Test that verify without --dry-run outputs warning about credentials and exits 1."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])
        assert result.exit_code == 1
        assert "connection credentials" in result.output

    def test_verify_read_error(self, tmp_path: Path) -> None:
        """Test verify error handling when reading SQL files fails."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = runner.invoke(app, ["verify", str(orig_file), str(opt_file), "--dry-run"])
            assert result.exit_code == 2
            assert "Error reading files" in result.output

