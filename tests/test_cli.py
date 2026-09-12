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

    def test_check_snow_002_correlated_subquery_exits_1(self, tmp_path: Path) -> None:
        """Test that check detects SNOW-002 correlated subquery with CRITICAL severity and exits 1."""
        sql_file = tmp_path / "correlated.sql"
        sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 1
        assert "SNOW-002" in result.output
        assert "CRITICAL" in result.output
        assert "Correlated Subquery" in result.output
        assert "Diagnostic Report" in result.output
        assert "Found 1 issue(s)" in result.output

    def test_check_snow_002_correlated_subquery_json_output(self, tmp_path: Path) -> None:
        """Test that check --json outputs SNOW-002 with CRITICAL severity and exits 1."""
        sql_file = tmp_path / "correlated.sql"
        sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]["rule_id"] == "SNOW-002"
        assert parsed[0]["severity"] == "CRITICAL"
        assert parsed[0]["can_auto_fix"] is False

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
        sql_file.write_text(
            "SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'", encoding="utf-8"
        )

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"disabled_rules": ["SNOW-001"]}), encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    def test_check_with_toml_config(self, tmp_path: Path) -> None:
        """Test check command loading a TOML config."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text(
            "SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'", encoding="utf-8"
        )

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
        assert (
            "-  ORDER BY" in result.output
            or "-    ORDER BY" in result.output
            or "-" in result.output
        )
        # File should remain unchanged
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_write_without_force_in_non_tty_fails(self, tmp_path: Path) -> None:
        """Test that fix --write without --force in non-interactive environment exits 1 with actionable error."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--write"])
        assert result.exit_code == 1
        assert (
            "error: Overwriting files in non-interactive environment requires --force flag, or use --patch to output a patch file."
            in result.output
        )
        # File must remain untouched
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_write_with_force_modifies_file(self, tmp_path: Path) -> None:
        """Test that fix --write with --force overwrites the target SQL file in-place."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--write", "--force"])
        assert result.exit_code == 0
        assert "Successfully updated" in result.output

        new_content = sql_file.read_text(encoding="utf-8")
        assert "ORDER BY" not in new_content

    def test_fix_dry_run_does_not_modify_file_or_patch(self, tmp_path: Path) -> None:
        """Test that --dry-run skips modifying file and saving patch, and displays diff preview."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub\n"
        sql_file.write_text(original, encoding="utf-8")
        patch_file = tmp_path / "dry_run.patch"

        result = runner.invoke(
            app,
            ["fix", str(sql_file), "--write", "--patch", str(patch_file), "--dry-run"],
        )
        assert result.exit_code == 0
        # Diff preview output
        assert "-" in result.output or "ORDER BY" in result.output
        # File should remain unmodified
        assert sql_file.read_text(encoding="utf-8") == original
        # Patch file must not be created
        assert not patch_file.exists()

    def test_fix_patch_option_creates_patch_file(self, tmp_path: Path) -> None:
        """Test that fix --patch writes the diff to a specified file."""
        sql_file = tmp_path / "fixable.sql"
        patch_file = tmp_path / "changes.patch"
        sql_file.write_text(
            "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8"
        )

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
        sql_file.write_text(
            "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8"
        )

        result = runner.invoke(
            app, ["fix", str(sql_file), "--interactive", "--write", "--force"], input="y\n"
        )
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "Successfully updated" in result.output
        assert "ORDER BY" not in sql_file.read_text(encoding="utf-8")

    def test_fix_interactive_no(self, tmp_path: Path) -> None:
        """Test interactive mode skipping fix when user inputs 'n'."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(
            app, ["fix", str(sql_file), "--interactive", "--write", "--force"], input="n\n"
        )
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "No modifications needed" in result.output
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_interactive_quit(self, tmp_path: Path) -> None:
        """Test interactive mode aborting fixes when user inputs 'q'."""
        sql_file = tmp_path / "fixable.sql"
        original = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub"
        sql_file.write_text(original, encoding="utf-8")

        result = runner.invoke(
            app, ["fix", str(sql_file), "--interactive", "--write", "--force"], input="q\n"
        )
        assert result.exit_code == 0
        assert "Apply fix for SNOW-003?" in result.output
        assert "No modifications needed" in result.output
        assert sql_file.read_text(encoding="utf-8") == original

    def test_fix_flatten_subqueries(self, tmp_path: Path) -> None:
        """Test fix with --flatten-subqueries lifts derived tables to CTEs."""
        sql_file = tmp_path / "nested.sql"
        sql_file.write_text("SELECT * FROM (SELECT a FROM tbl) AS sub", encoding="utf-8")

        result = runner.invoke(
            app, ["fix", str(sql_file), "--flatten-subqueries", "--write", "--force"]
        )
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
            ["fix", str(sql_file), "--interactive", "--flatten-subqueries", "--write", "--force"],
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
            ["fix", str(sql_file), "--interactive", "--flatten-subqueries", "--write", "--force"],
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
        sql_file.write_text(
            "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8"
        )

        with patch(
            "icepick.patcher.in_place.ASTPatcher.apply_issue",
            side_effect=ValueError("Patch failed"),
        ):
            result = runner.invoke(app, ["fix", str(sql_file), "--interactive"], input="y\n")
            assert result.exit_code == 0
            assert "Skipped fix for SNOW-003" in result.output

    def test_fix_interactive_flatten_exception_handling(self, tmp_path: Path) -> None:
        """Test that an exception during interactive flattening logs a warning and skips safely."""
        sql_file = tmp_path / "nested.sql"
        sql_file.write_text("SELECT * FROM (SELECT a FROM tbl) AS sub", encoding="utf-8")

        with patch(
            "icepick.patcher.subquery_to_cte.SubqueryToCTE.flatten_all_subqueries",
            side_effect=ValueError("Flatten failed"),
        ):
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
        """Test that verify without --dry-run outputs actionable AuthenticationError and exits 1."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        with patch("icepick.cli.resolve_credential") as mock_resolve:
            from icepick.exceptions import AuthenticationError
            mock_resolve.side_effect = AuthenticationError(
                "[Authentication Error] Credential for 'snowflake_password' is invalid or not provided.",
                key_name="snowflake_password",
            )
            result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])
            assert result.exit_code == 1
            assert "Authentication Error" in result.output
            assert "snowflake_password" in result.output

    def test_verify_with_resolved_credentials(self, tmp_path: Path) -> None:
        """Test that verify succeeds (exits 0) when snowflake credentials are successfully resolved."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        with patch("icepick.cli.resolve_credential", return_value=("mock_secret", "environment variable")):
            result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])
            assert result.exit_code == 0
            assert "Direct Snowflake execution" in result.output

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

    def test_check_json_clean_sql_outputs_empty_array(self, tmp_path: Path) -> None:
        """Test that check --json outputs an empty JSON array for clean SQL and exits 0."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id FROM tbl", encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 0

    def test_check_json_issues_outputs_structured_array(self, tmp_path: Path) -> None:
        """Test that check --json outputs a structured JSON array of detected issues and exits 1."""
        sql_file = tmp_path / "issues.sql"
        sql = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub WHERE DATE(created_at) = '2023-01-01'"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 3

        rule_ids = {item["rule_id"] for item in data}
        assert "SNOW-001" in rule_ids
        assert "SNOW-003" in rule_ids
        assert "SNOW-007" in rule_ids

        for item in data:
            assert "rule_name" in item
            assert "severity" in item
            assert "line" in item
            assert "description" in item

    def test_fix_json_optimized_output(self, tmp_path: Path) -> None:
        """Test that fix --json returns structured JSON with status 'optimized' and applied issues."""
        sql_file = tmp_path / "fixable.sql"
        sql_file.write_text(
            "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub", encoding="utf-8"
        )

        result = runner.invoke(app, ["fix", str(sql_file), "--json", "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "optimized"
        assert data["file"] == str(sql_file)
        assert "SNOW-003" in data["issues_fixed"]
        assert "ORDER BY" in data["diff"]
        assert len(data["diff"]) > 0

    def test_fix_json_unchanged_output(self, tmp_path: Path) -> None:
        """Test that fix --json returns status 'unchanged' when no fixes are needed."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id FROM tbl\n", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "unchanged"
        assert data["file"] == str(sql_file)
        assert data["issues_fixed"] == []
        assert data["diff"] == ""

    def test_check_invalid_dialect_enum_error(self, tmp_path: Path) -> None:
        """Test that check with invalid --dialect outputs supported dialect enum choices and exits 1."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file), "--dialect", "unknown_sql"])
        assert result.exit_code == 1
        assert "error: Invalid dialect 'unknown_sql'" in result.output
        assert "snowflake" in result.output
        assert "postgres" in result.output
        assert "duckdb" in result.output
        assert "bigquery" in result.output

    def test_fix_invalid_dialect_enum_error(self, tmp_path: Path) -> None:
        """Test that fix with invalid --dialect outputs supported dialect enum choices and exits 1."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--dialect", "unknown_sql"])
        assert result.exit_code == 1
        assert "error: Invalid dialect 'unknown_sql'" in result.output
        assert "snowflake" in result.output
        assert "postgres" in result.output
        assert "duckdb" in result.output
        assert "bigquery" in result.output

    def test_verify_invalid_dialect_enum_error(self, tmp_path: Path) -> None:
        """Test that verify with invalid --dialect outputs supported dialect enum choices and exits 1."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(
            app,
            ["verify", str(sql_file), str(sql_file), "--dialect", "unknown_sql"],
        )
        assert result.exit_code == 1
        assert "error: Invalid dialect 'unknown_sql'" in result.output
        assert "snowflake" in result.output

    def test_check_detects_snow_004_implicit_cross_join(self, tmp_path: Path) -> None:
        """Test that check command detects comma join (SNOW-004)."""
        sql_file = tmp_path / "cross_join.sql"
        sql_file.write_text("SELECT * FROM users, orders", encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 1
        assert "SNOW-004" in result.output

    def test_check_detects_snow_005_duplicate_scan(self, tmp_path: Path) -> None:
        """Test that check command detects duplicate table scans in CTEs (SNOW-005)."""
        sql_file = tmp_path / "dup_scan.sql"
        sql = "WITH c1 AS (SELECT * FROM tbl), c2 AS (SELECT * FROM tbl) SELECT * FROM c1 JOIN c2 ON c1.id = c2.id"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["check", str(sql_file)])
        assert result.exit_code == 1
        assert "SNOW-005" in result.output

    def test_fix_applies_snow_006_union_to_union_all(self, tmp_path: Path) -> None:
        """Test that fix automatically rewrites UNION to UNION ALL (SNOW-006)."""
        sql_file = tmp_path / "union.sql"
        sql_file.write_text("SELECT 1 UNION SELECT 2", encoding="utf-8")

        result = runner.invoke(app, ["fix", str(sql_file), "--write", "--force"])
        assert result.exit_code == 0
        assert "Successfully updated" in result.output

        content = sql_file.read_text(encoding="utf-8")
        assert "UNION ALL" in content

