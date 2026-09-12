"""London-style CLI tests for icepick using typer.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner, _NamedTextIOWrapper

from icepick import __version__
from icepick.cli import app

runner = CliRunner()


class TestCli:
    """CLI tests for check, rewrite, patch, and verify commands."""

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

    def test_check_and_rewrite_read_error(self, tmp_path: Path) -> None:
        """Test handling of file read error in check and rewrite commands."""
        sql_file = tmp_path / "dummy.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("Read failure")):
            res_check = runner.invoke(app, ["check", str(sql_file)])
            assert res_check.exit_code == 2
            assert "Error reading file" in res_check.output

            res_rewrite = runner.invoke(app, ["rewrite", str(sql_file)])
            assert res_rewrite.exit_code == 2
            assert "Error reading file" in res_rewrite.output

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

        with patch(
            "icepick.cli.resolve_credential", return_value=("mock_secret", "environment variable")
        ):
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

    def test_rewrite_invalid_dialect_enum_error(self, tmp_path: Path) -> None:
        """Test that rewrite with invalid --dialect outputs supported dialect enum choices and exits 1."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--dialect", "unknown_sql"])
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

    def test_rewrite_stdout_unified_diff(self, tmp_path: Path) -> None:
        """Test that rewrite outputs unified diff to stdout and leaves original file untouched."""
        sql_file = tmp_path / "test.sql"
        original_sql = "SELECT 1 UNION SELECT 2"
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 0
        assert "UNION" in result.output
        assert "UNION ALL" in result.output
        # Verify read-only behavior
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_output_patch_file(self, tmp_path: Path) -> None:
        """Test that rewrite -o creates a patch file with valid unified diff."""
        sql_file = tmp_path / "test.sql"
        original_sql = "SELECT 1 UNION SELECT 2"
        sql_file.write_text(original_sql, encoding="utf-8")
        patch_file = tmp_path / "test.patch"

        result = runner.invoke(app, ["rewrite", str(sql_file), "-o", str(patch_file)])
        assert result.exit_code == 0
        assert patch_file.exists()
        patch_content = patch_file.read_text(encoding="utf-8")
        assert f"--- a/{sql_file.name}" in patch_content
        assert f"+++ b/{sql_file.name}" in patch_content
        assert "-UNION" in patch_content
        assert "+UNION ALL" in patch_content
        # Verify original file untouched
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_json_output(self, tmp_path: Path) -> None:
        """Test that rewrite --json outputs structured JSON and leaves original file untouched."""
        sql_file = tmp_path / "test.sql"
        original_sql = "SELECT 1 UNION SELECT 2"
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["file"] == str(sql_file)
        assert data["has_changes"] is True
        assert data["issues_count"] == 1
        assert len(data["issues"]) == 1
        assert data["issues"][0]["rule_id"] == "SNOW-006"
        assert "UNION ALL" in data["diff"]
        # Verify original file untouched
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_flatten_subqueries(self, tmp_path: Path) -> None:
        """Test that rewrite --flatten-subqueries extracts inline derived tables to top-level CTEs."""
        sql_file = tmp_path / "subquery.sql"
        original_sql = "SELECT * FROM (SELECT id FROM tbl) AS sub"
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--flatten-subqueries"])
        assert result.exit_code == 0
        assert "WITH" in result.output
        assert "cte_sub" in result.output
        # Verify original file untouched
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_no_changes(self, tmp_path: Path) -> None:
        """Test rewrite on clean SQL with no optimizable issues."""
        sql_file = tmp_path / "clean.sql"
        original_sql = "SELECT id, name FROM users WHERE id = 10"
        sql_file.write_text(original_sql, encoding="utf-8")

        # Standard output mode
        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 0
        assert "No optimizable issues found" in result.output

        # JSON mode
        result_json = runner.invoke(app, ["rewrite", str(sql_file), "--json"])
        assert result_json.exit_code == 0
        data = json.loads(result_json.output)
        assert data["has_changes"] is False
        assert data["issues_count"] == 0
        assert data["diff"] == ""
        assert data["issues"] == []

        # Output patch file when clean
        patch_file = tmp_path / "clean.patch"
        result_patch = runner.invoke(app, ["rewrite", str(sql_file), "-o", str(patch_file)])
        assert result_patch.exit_code == 0
        assert patch_file.exists()
        assert patch_file.read_text(encoding="utf-8") == ""

    def test_rewrite_invalid_category(self, tmp_path: Path) -> None:
        """Test that invalid --category outputs actionable error with enum list and exits 1."""
        sql_file = tmp_path / "test.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--category", "URGENT"])
        assert result.exit_code == 1
        assert "Invalid category 'URGENT'" in result.output
        assert "CRITICAL" in result.output
        assert "HIGH" in result.output
        assert "MEDIUM" in result.output
        assert "LOW" in result.output
        assert "Actionable Advice" in result.output

    def test_rewrite_category_filtering(self, tmp_path: Path) -> None:
        """Test that --category filters issues to only those at or above the threshold."""
        sql_file = tmp_path / "mixed.sql"
        # SNOW-001 (HIGH) + SNOW-006 (LOW)
        original_sql = "SELECT * FROM tbl WHERE DATE(created_at) = '2023-01-01' UNION SELECT * FROM tbl WHERE DATE(created_at) = '2023-01-02'"
        sql_file.write_text(original_sql, encoding="utf-8")

        # Filter by HIGH: SNOW-001 (HIGH) applied, SNOW-006 (LOW) ignored
        result = runner.invoke(app, ["rewrite", str(sql_file), "--category", "HIGH", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["has_changes"] is True
        applied_rule_ids = [issue["rule_id"] for issue in data["issues"]]
        assert "SNOW-001" in applied_rule_ids
        assert "SNOW-006" not in applied_rule_ids

    def test_rewrite_parse_error_exits_2(self, tmp_path: Path) -> None:
        """Test that rewrite on invalid SQL syntax exits with 2."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("SELECT FROM WHERE ;;;", encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 2
        assert "Parse Error" in result.output

    def test_patch_file_argument(self, tmp_path: Path) -> None:
        """Test applying a patch file directly via argument updates the file."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT id\nFROM users\nWHERE id = 1\n", encoding="utf-8")

        patch_file = tmp_path / "change.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -3,1 +3,1 @@\n-WHERE id = 1\n+WHERE id = 99\n",
            encoding="utf-8",
        )

        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            result = runner.invoke(app, ["patch", str(sql_file), str(patch_file)])

        assert result.exit_code == 0
        assert "Successfully applied 1/1 hunk(s)" in result.output
        assert "WHERE id = 99" in sql_file.read_text(encoding="utf-8")

    def test_patch_stdin_pipe(self, tmp_path: Path) -> None:
        """Test applying a patch piped via sys.stdin with --force flag."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT id\nFROM users\nWHERE id = 1\n", encoding="utf-8")

        diff_text = (
            "--- a/target.sql\n+++ b/target.sql\n@@ -3,1 +3,1 @@\n-WHERE id = 1\n+WHERE id = 42\n"
        )

        with patch("sys.stdin.isatty", return_value=False):
            result = runner.invoke(app, ["patch", str(sql_file), "--force"], input=diff_text)

        assert result.exit_code == 0
        assert "Successfully applied 1/1 hunk(s)" in result.output
        assert "WHERE id = 42" in sql_file.read_text(encoding="utf-8")

    def test_patch_non_interactive_without_force_fails(self, tmp_path: Path) -> None:
        """Test that overwriting in non-interactive environment without --force exits with 1."""
        sql_file = tmp_path / "target.sql"
        original_sql = "SELECT id\nFROM users\nWHERE id = 1\n"
        sql_file.write_text(original_sql, encoding="utf-8")

        patch_file = tmp_path / "change.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -3,1 +3,1 @@\n-WHERE id = 1\n+WHERE id = 42\n",
            encoding="utf-8",
        )

        with patch("sys.stdin.isatty", return_value=False):
            result = runner.invoke(app, ["patch", str(sql_file), str(patch_file)])

        assert result.exit_code == 1
        assert "error: Overwriting files in non-interactive environment requires --force flag" in (
            result.output + result.stderr
        )
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_patch_non_interactive_with_force_succeeds(self, tmp_path: Path) -> None:
        """Test that non-interactive patch application succeeds when --force is provided."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT id\nFROM users\nWHERE id = 1\n", encoding="utf-8")

        patch_file = tmp_path / "change.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -3,1 +3,1 @@\n-WHERE id = 1\n+WHERE id = 100\n",
            encoding="utf-8",
        )

        with patch("sys.stdin.isatty", return_value=False):
            result = runner.invoke(app, ["patch", str(sql_file), str(patch_file), "--force"])

        assert result.exit_code == 0
        assert "WHERE id = 100" in sql_file.read_text(encoding="utf-8")

    def test_patch_dry_run_leaves_file_unchanged(self, tmp_path: Path) -> None:
        """Test that --dry-run simulates application without modifying target file."""
        sql_file = tmp_path / "target.sql"
        original_sql = "SELECT id\nFROM users\nWHERE id = 1\n"
        sql_file.write_text(original_sql, encoding="utf-8")

        patch_file = tmp_path / "change.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -3,1 +3,1 @@\n-WHERE id = 1\n+WHERE id = 999\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["patch", str(sql_file), str(patch_file), "--dry-run"])

        assert result.exit_code == 0
        assert "Dry-run: Simulated applying 1/1 hunk(s)" in result.output
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_patch_interactive_yes_and_no(self, tmp_path: Path) -> None:
        """Test that --interactive allows selective hunk application."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text(
            "SELECT a\nFROM tbl\nWHERE x = 1\nORDER BY a\n",
            encoding="utf-8",
        )

        patch_file = tmp_path / "multi.patch"
        patch_file.write_text(
            "--- a/target.sql\n"
            "+++ b/target.sql\n"
            "@@ -1,1 +1,1 @@\n"
            "-SELECT a\n"
            "+SELECT a, b\n"
            "@@ -4,1 +4,1 @@\n"
            "-ORDER BY a\n"
            "+ORDER BY id\n",
            encoding="utf-8",
        )

        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            # Accept hunk 1 (y), reject hunk 2 (n)
            result = runner.invoke(
                app,
                ["patch", str(sql_file), str(patch_file), "--interactive"],
                input="y\nn\n",
            )

        assert result.exit_code == 0
        assert "Successfully applied 1/2 hunk(s)" in result.output
        content = sql_file.read_text(encoding="utf-8")
        assert "SELECT a, b" in content
        assert "ORDER BY a" in content  # Hunk 2 was rejected

    def test_patch_json_output(self, tmp_path: Path) -> None:
        """Test that --json outputs structured result on stdout."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT 1\n", encoding="utf-8")

        patch_file = tmp_path / "change.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -1,1 +1,1 @@\n-SELECT 1\n+SELECT 2\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["patch", str(sql_file), str(patch_file), "--force", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "applied"
        assert data["file"] == str(sql_file)
        assert data["hunks_applied"] == 1
        assert data["hunks_total"] == 1

        # Reset target file before testing dry-run
        sql_file.write_text("SELECT 1\n", encoding="utf-8")
        result_dry = runner.invoke(
            app, ["patch", str(sql_file), str(patch_file), "--dry-run", "--json"]
        )
        assert result_dry.exit_code == 0
        data_dry = json.loads(result_dry.output)
        assert data_dry["status"] == "dry_run"

    def test_patch_empty_input_fails(self, tmp_path: Path) -> None:
        """Test that patch fails with exit code 1 when input diff has no hunks."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT 1\n", encoding="utf-8")

        result = runner.invoke(app, ["patch", str(sql_file)], input="")
        assert result.exit_code == 1
        assert "Error: No diff content or hunks found in patch input." in (
            result.output + result.stderr
        )

    def test_patch_mismatch_shows_actionable_advice_and_exits_1(self, tmp_path: Path) -> None:
        """Test that patch mismatch prints actionable advice and exits with code 1."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text("SELECT id FROM users WHERE id = 1\n", encoding="utf-8")

        patch_file = tmp_path / "mismatch.patch"
        patch_file.write_text(
            "--- a/target.sql\n+++ b/target.sql\n@@ -1,1 +1,1 @@\n-SELECT completely_different_line\n+SELECT id\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["patch", str(sql_file), str(patch_file), "--force"])
        assert result.exit_code == 1
        assert "Error applying patch:" in result.stderr
        assert "Actionable Advice:" in result.stderr
        assert "Ensure the target file has not been modified" in result.stderr
        assert "icepick rewrite" in result.stderr

    def test_patch_interactive_quit(self, tmp_path: Path) -> None:
        """Test that inputting 'q' in interactive patch mode aborts prompting immediately."""
        sql_file = tmp_path / "target.sql"
        sql_file.write_text(
            "SELECT a\nFROM tbl\nWHERE x = 1\nORDER BY a\n",
            encoding="utf-8",
        )

        patch_file = tmp_path / "multi.patch"
        patch_file.write_text(
            "--- a/target.sql\n"
            "+++ b/target.sql\n"
            "@@ -1,1 +1,1 @@\n"
            "-SELECT a\n"
            "+SELECT a, b\n"
            "@@ -4,1 +4,1 @@\n"
            "-ORDER BY a\n"
            "+ORDER BY id\n",
            encoding="utf-8",
        )

        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            # Input 'q' at the first prompt
            result = runner.invoke(
                app,
                ["patch", str(sql_file), str(patch_file), "--interactive"],
                input="q\n",
            )

        assert result.exit_code == 0
        assert "No hunks applied (0/2)" in result.output
        content = sql_file.read_text(encoding="utf-8")
        assert "SELECT a\n" in content
        assert "ORDER BY a\n" in content
