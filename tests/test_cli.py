"""London-style CLI tests for icepick using typer.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlglot
from typer.testing import CliRunner, _NamedTextIOWrapper

from icepick import __version__
from icepick.cli import app
from icepick.exceptions import AuthenticationError
from icepick.health import ConnectionHealthReport, ServiceTestResult

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
        original_sql = "SELECT 1\nUNION\nSELECT 2"
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

    def test_rewrite_source_preserving_diff(self, tmp_path: Path) -> None:
        """Test that rewrite preserves 4-space indent, lowercase casing, and comments."""
        sql_file = tmp_path / "query.sql"
        original_sql = (
            "-- Important reporting query\n"
            "select\n"
            "    id,\n"
            "    user_name,\n"
            "    created_at\n"
            "from\n"
            "    events\n"
            "where\n"
            "    date(created_at) = '2024-01-01'\n"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 0
        output = result.output

        # Context lines should preserve lowercase, 4 spaces indent, and comments
        assert "-    date(created_at) = '2024-01-01'" in output
        assert (
            "+    created_at >= '2024-01-01' AND created_at < DATEADD(DAY, 1, '2024-01-01')"
            in output
        )
        # Should NOT reformat untouched lines into uppercase or 2-space indents
        assert "-select" not in output
        assert "-    id," not in output
        assert "-    user_name," not in output
        assert "-from" not in output
        assert "-    events" not in output
        assert "-where" not in output
        # Verify original file untouched (read-only guarantee)
        assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_and_patch_roundtrip_with_raw_formatting(self, tmp_path: Path) -> None:
        """Test clean roundtrip rewrite -> patch pipeline preserving custom formatting."""
        sql_file = tmp_path / "report.sql"
        original_sql = (
            "-- Important reporting query\n"
            "select\n"
            "    id,\n"
            "    user_name,\n"
            "    created_at\n"
            "from\n"
            "    events\n"
            "where\n"
            "    date(created_at) = '2024-01-01'\n"
        )
        sql_file.write_text(original_sql, encoding="utf-8")
        patch_file = tmp_path / "report.patch"

        # 1. Generate patch with rewrite
        rewrite_res = runner.invoke(app, ["rewrite", str(sql_file), "-o", str(patch_file)])
        assert rewrite_res.exit_code == 0
        assert patch_file.exists()

        # 2. Apply patch with icepick patch (simulating interactive environment)
        with patch.object(_NamedTextIOWrapper, "isatty", return_value=True):
            patch_res = runner.invoke(app, ["patch", str(sql_file), str(patch_file)])
        assert patch_res.exit_code == 0
        assert "Successfully applied" in patch_res.output

        # 3. Verify file content preserves original comment, casing, and 4-space indent
        patched_sql = sql_file.read_text(encoding="utf-8")
        assert "-- Important reporting query" in patched_sql
        assert "select\n    id,\n    user_name,\n    created_at" in patched_sql
        assert "from\n    events\nwhere" in patched_sql
        assert (
            "    created_at >= '2024-01-01' AND created_at < DATEADD(DAY, 1, '2024-01-01')"
            in patched_sql
        )
        assert "date(created_at)" not in patched_sql

    def test_rewrite_reformat_flag(self, tmp_path: Path) -> None:
        """Test that --reformat triggers full AST reformatting diff (normalize=True)."""
        sql_file = tmp_path / "query.sql"
        original_sql = (
            "-- Important reporting query\n"
            "select\n"
            "    id,\n"
            "    user_name,\n"
            "    created_at\n"
            "from\n"
            "    events\n"
            "where\n"
            "    date(created_at) = '2024-01-01'\n"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--reformat"])
        assert result.exit_code == 0
        output = result.output

        # When --reformat is specified, normalize=True AST formatting kicks in
        # sqlglot canonical output normalizes to uppercase and 2 spaces indent
        assert "-  TO_DATE(created_at) = '2024-01-01'" in output
        assert (
            "+  created_at >= '2024-01-01' AND created_at < DATEADD(DAY, 1, '2024-01-01')"
            in output
        )
        assert "FROM events" in output
        assert "WHERE" in output

    def test_rewrite_parse_error_exits_2(self, tmp_path: Path) -> None:
        """Test that rewrite on invalid SQL syntax exits with 2."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("SELECT FROM WHERE ;;;", encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 2
        assert "Parse Error" in result.output

    def test_rewrite_agentic_success(self, tmp_path: Path) -> None:
        """Test that rewrite --agentic rewrites correlated subquery (SNOW-002) and preserves original file."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.api_key = "mock-api-key"
        mock_client._auth_error = None
        # Replacement simulating converting subquery into a join
        replacement = sqlglot.parse_one(
            "c.cust_id IN (SELECT o.cust_id FROM orders AS o JOIN customers AS sub_c ON o.cust_id = sub_c.cust_id)"
        )
        mock_client.rewrite_fragment.return_value = replacement

        with patch("icepick.cli.LLMClient", return_value=mock_client):
            result = runner.invoke(app, ["rewrite", str(sql_file), "--agentic"])
            assert result.exit_code == 0
            assert "JOIN" in result.output
            assert "EXISTS" in result.output
            # Read-only guarantee: original file should remain unchanged
            assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_agentic_auth_error_shows_actionable_advice(self, tmp_path: Path) -> None:
        """Test that rewrite --agentic outputs actionable advice and exits 1 on auth error."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        with patch(
            "icepick.cli.LLMClient",
            side_effect=AuthenticationError("No Gemini API key or credentials found"),
        ):
            result = runner.invoke(app, ["rewrite", str(sql_file), "--agentic"])
            assert result.exit_code == 1
            assert "Authentication Error:" in result.output
            assert "Actionable Advice:" in result.output
            assert "--provider vertex" in result.output
            assert "ICEPICK_LLM_PROVIDER" in result.output
            assert "cmdkey" in result.output
            # Read-only guarantee
            assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_missing_gemini_key_shows_vertex_guidance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that rewrite --agentic with missing Gemini API key shows Vertex AI guidance and exits 1."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        monkeypatch.delenv("DEBUG_ICEPICK_GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch("icepick.security.credentials.read_wcm_credential_fn", return_value=None):
            result = runner.invoke(app, ["rewrite", str(sql_file), "--agentic"])
            assert result.exit_code == 1
            assert "Authentication Error:" in result.output
            assert "Actionable Advice:" in result.output
            assert "--provider vertex" in result.output
            assert "ICEPICK_LLM_PROVIDER" in result.output
            assert "cmdkey" in result.output
            # Read-only guarantee
            assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_agentic_json_output(self, tmp_path: Path) -> None:
        """Test that rewrite --agentic --json outputs structured JSON with correlated subquery issue in applied_issues."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.api_key = "mock-api-key"
        mock_client._auth_error = None
        replacement = sqlglot.parse_one("c.cust_id IN (SELECT o.cust_id FROM orders AS o)")
        mock_client.rewrite_fragment.return_value = replacement

        with patch("icepick.cli.LLMClient", return_value=mock_client):
            result = runner.invoke(app, ["rewrite", str(sql_file), "--agentic", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["file"] == str(sql_file)
            assert data["has_changes"] is True
            assert data["issues_count"] == 1
            assert len(data["issues"]) == 1
            assert data["issues"][0]["rule_id"] == "SNOW-002"
            assert "c.cust_id IN" in data["diff"]
            # Read-only guarantee
            assert sql_file.read_text(encoding="utf-8") == original_sql

    def test_rewrite_without_agentic_skips_correlated_subquery(self, tmp_path: Path) -> None:
        """Test that rewrite without --agentic skips correlated subqueries since they require LLM."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file)])
        assert result.exit_code == 0
        assert "No optimizable issues found" in result.output
        assert sql_file.read_text(encoding="utf-8") == original_sql

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

    def test_rewrite_provider_and_model_options(self, tmp_path: Path) -> None:
        """Test rewrite with --provider and --model options passes them to config and shows feedback banner."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        mock_client = MagicMock()
        mock_client.provider = "vertex"
        mock_client.project = "test-project"
        mock_client._auth_error = None
        mock_client._get_vertex_token = MagicMock()
        replacement = sqlglot.parse_one("c.cust_id IN (SELECT o.cust_id FROM orders AS o)")
        mock_client.rewrite_fragment.return_value = replacement

        with patch("icepick.cli.LLMClient", return_value=mock_client) as mock_llm_cls:
            result = runner.invoke(
                app,
                [
                    "rewrite",
                    str(sql_file),
                    "--agentic",
                    "--provider",
                    "vertex",
                    "--model",
                    "gemini-1.5-pro",
                ],
                env={"GCP_PROJECT": "test-project"},
            )
            assert result.exit_code == 0
            # Verify LLMClient received the resolved config
            call_kwargs = mock_llm_cls.call_args.kwargs
            resolved_cfg = call_kwargs.get("config")
            assert resolved_cfg is not None
            assert resolved_cfg.llm_provider == "vertex"
            assert resolved_cfg.llm_model == "gemini-1.5-pro"

            # Verify feedback banner is printed
            assert "Active LLM Configuration" in result.output
            assert "Provider: vertex" in result.output
            assert "Model:    gemini-1.5-pro" in result.output
            assert "(Source: cli)" in result.output

    def test_rewrite_json_contains_runtime_config(self, tmp_path: Path) -> None:
        """Test that rewrite --json output contains runtime_config with resolved options and sources."""
        sql_file = tmp_path / "query.sql"
        # Triggers SNOW-001 rewrite
        sql_file.write_text(
            "SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'",
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "rewrite",
                str(sql_file),
                "--provider",
                "vertex",
                "--model",
                "gemini-2.5-flash",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "runtime_config" in data
        rt = data["runtime_config"]
        assert "llm_provider" in rt
        assert rt["llm_provider"]["value"] == "vertex"
        assert rt["llm_provider"]["source"] == "cli"
        assert "llm_model" in rt
        assert rt["llm_model"]["value"] == "gemini-2.5-flash"
        assert rt["llm_model"]["source"] == "cli"

        # Also test on clean query (has_changes: False)
        clean_file = tmp_path / "clean.sql"
        clean_file.write_text("SELECT id FROM tbl", encoding="utf-8")
        res_clean = runner.invoke(app, ["rewrite", str(clean_file), "--json"])
        assert res_clean.exit_code == 0
        clean_data = json.loads(res_clean.output)
        assert "runtime_config" in clean_data
        assert clean_data["runtime_config"]["llm_provider"]["value"] == "gemini"
        assert clean_data["runtime_config"]["llm_provider"]["source"] == "default"

    def test_rewrite_invalid_provider_fails(self, tmp_path: Path) -> None:
        """Test that passing an invalid provider to rewrite exits with 1 and configuration error."""
        sql_file = tmp_path / "dummy.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["rewrite", str(sql_file), "--provider", "unsupported_llm"])
        assert result.exit_code == 1
        assert "Configuration Error:" in (result.output + result.stderr)
        assert "unsupported_llm" in (result.output + result.stderr)

    def test_config_show_table(self) -> None:
        """Test icepick config show displays configuration table with options, sources, and secrets."""
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "Icepick Resolved Configuration" in result.output
        assert "Option" in result.output
        assert "Resolved Value" in result.output
        assert "Source" in result.output
        assert "Secret?" in result.output
        assert "llm_provider" in result.output
        assert "llm_model" in result.output
        assert "default" in result.output

    def test_config_show_json(self) -> None:
        """Test icepick config show --json returns structured JSON dictionary with provenance."""
        result = runner.invoke(app, ["config", "show", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "llm_provider" in data
        assert "value" in data["llm_provider"]
        assert "source" in data["llm_provider"]
        assert "snowflake_password" in data
        assert data["snowflake_password"]["value"] is None or "..." in str(
            data["snowflake_password"]["value"]
        )

        # Test with a masked secret environment variable
        result_secret = runner.invoke(
            app,
            ["config", "show", "--json"],
            env={"DEBUG_ICEPICK_GEMINI_API_KEY": "AIzaSySecretKeyExample1234567"},
        )
        assert result_secret.exit_code == 0
        data_secret = json.loads(result_secret.output)
        gemini_item = data_secret["gemini_api_key"]
        assert "..." in gemini_item["value"]
        assert "AIzaSySecretKeyExample1234567" not in gemini_item["value"]
        assert gemini_item["source"] == "env"

    def test_config_test_all_success(self) -> None:
        """Test icepick config test runs all checks by default and prints table on success."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=123.4,
                    message="Successfully connected to LLM provider.",
                    details={"provider": "gemini", "model": "gemini-3.8-flash"},
                ),
                "snowflake": ServiceTestResult(
                    service="snowflake",
                    success=True,
                    duration_ms=456.7,
                    message="Successfully connected to Snowflake.",
                    details={"version": "8.15.0", "user": "TEST_USER", "warehouse": "TEST_WH"},
                ),
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test"])
            assert result.exit_code == 0
            mock_tester.test_all.assert_called_once()
            call_kwargs = mock_tester.test_all.call_args.kwargs
            assert call_kwargs["targets"] == ["llm", "snowflake"]
            assert "Icepick Connection Health Check" in result.output
            assert "PASS" in result.output
            assert "123.4ms" in result.output
            assert "456.7ms" in result.output
            assert "provider=gemini" in result.output
            assert "version=8.15.0" in result.output

    def test_config_test_llm_only(self) -> None:
        """Test icepick config test --llm checks only LLM connection."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=99.5,
                    message="Connected.",
                    details={"provider": "gemini"},
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test", "--llm"])
            assert result.exit_code == 0
            call_kwargs = mock_tester.test_all.call_args.kwargs
            assert call_kwargs["targets"] == ["llm"]
            assert "LLM" in result.output
            assert "SNOWFLAKE" not in result.output

    def test_config_test_snowflake_only(self) -> None:
        """Test icepick config test --snowflake checks only Snowflake connection."""
        mock_report = ConnectionHealthReport(
            results={
                "snowflake": ServiceTestResult(
                    service="snowflake",
                    success=True,
                    duration_ms=250.0,
                    message="Connected.",
                    details={"version": "8.15.0"},
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test", "--snowflake"])
            assert result.exit_code == 0
            call_kwargs = mock_tester.test_all.call_args.kwargs
            assert call_kwargs["targets"] == ["snowflake"]
            assert "SNOWFLAKE" in result.output
            assert "LLM" not in result.output

    def test_config_test_both_flags(self) -> None:
        """Test icepick config test with both --llm and --snowflake tests both services."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=80.0,
                    message="Connected.",
                ),
                "snowflake": ServiceTestResult(
                    service="snowflake",
                    success=True,
                    duration_ms=150.0,
                    message="Connected.",
                ),
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test", "--llm", "--snowflake"])
            assert result.exit_code == 0
            call_kwargs = mock_tester.test_all.call_args.kwargs
            assert call_kwargs["targets"] == ["llm", "snowflake"]

    def test_config_test_target_option(self) -> None:
        """Test icepick config test --target parses targets correctly."""
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester_cls.return_value = mock_tester

            mock_tester.test_all.return_value = ConnectionHealthReport(
                results={"llm": ServiceTestResult("llm", True, 50.0, "OK")}
            )
            res_llm = runner.invoke(app, ["config", "test", "--target", "llm"])
            assert res_llm.exit_code == 0
            assert mock_tester.test_all.call_args.kwargs["targets"] == ["llm"]

            mock_tester.test_all.return_value = ConnectionHealthReport(
                results={"snowflake": ServiceTestResult("snowflake", True, 60.0, "OK")}
            )
            res_sf = runner.invoke(app, ["config", "test", "-t", "snowflake"])
            assert res_sf.exit_code == 0
            assert mock_tester.test_all.call_args.kwargs["targets"] == ["snowflake"]

            mock_tester.test_all.return_value = ConnectionHealthReport(
                results={
                    "llm": ServiceTestResult("llm", True, 50.0, "OK"),
                    "snowflake": ServiceTestResult("snowflake", True, 60.0, "OK"),
                }
            )
            res_all = runner.invoke(app, ["config", "test", "-t", "all"])
            assert res_all.exit_code == 0
            assert mock_tester.test_all.call_args.kwargs["targets"] == ["llm", "snowflake"]

    def test_config_test_invalid_target_error(self) -> None:
        """Test icepick config test with invalid target exits with 1 and shows actionable advice."""
        result = runner.invoke(app, ["config", "test", "--target", "invalid_svc"])
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "Invalid target 'invalid_svc'" in combined
        assert "Valid targets are 'all', 'llm', 'snowflake'" in combined
        assert "Actionable Advice:" in combined

    def test_config_test_failure_exits_1_with_advice(self) -> None:
        """Test icepick config test exits with 1 and displays advice panel when a check fails."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=False,
                    duration_ms=45.0,
                    message="Gemini API key is invalid.",
                    actionable_advice="Run cmdkey /generic:icepick:gemini /user:apikey /pass:<key> to register.",
                ),
                "snowflake": ServiceTestResult(
                    service="snowflake",
                    success=True,
                    duration_ms=180.0,
                    message="Connected.",
                ),
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test"])
            assert result.exit_code == 1
            assert "FAIL" in result.output
            assert "Gemini API key is invalid" in result.output
            assert "Actionable Advice: LLM" in result.output
            assert "cmdkey /generic:icepick:gemini" in result.output

    def test_config_test_json_output(self) -> None:
        """Test icepick config test --json outputs structured JSON report."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=120.0,
                    message="Connected.",
                    details={"provider": "gemini", "model": "gemini-3.8-flash"},
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            res_pass = runner.invoke(app, ["config", "test", "--llm", "--json"])
            assert res_pass.exit_code == 0
            data_pass = json.loads(res_pass.output)
            assert data_pass["all_passed"] is True
            assert "llm" in data_pass["results"]
            assert data_pass["results"]["llm"]["success"] is True
            assert data_pass["results"]["llm"]["details"]["model"] == "gemini-3.8-flash"

        mock_fail_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=False,
                    duration_ms=40.0,
                    message="Authentication error",
                    actionable_advice="Set GEMINI_API_KEY",
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_fail_report
            mock_tester_cls.return_value = mock_tester

            res_fail = runner.invoke(app, ["config", "test", "--llm", "--json"])
            assert res_fail.exit_code == 1
            data_fail = json.loads(res_fail.output)
            assert data_fail["all_passed"] is False
            assert data_fail["results"]["llm"]["success"] is False
            assert data_fail["results"]["llm"]["actionable_advice"] == "Set GEMINI_API_KEY"

    def test_config_test_config_load_error(self, tmp_path: Path) -> None:
        """Test icepick config test exits with 1 when configuration file cannot be loaded."""
        non_existent = tmp_path / "non_existent_config.toml"
        result = runner.invoke(app, ["config", "test", "--config", str(non_existent)])
        assert result.exit_code == 1
        combined = result.output + (result.stderr or "")
        assert "Configuration Error:" in combined

    def test_config_test_with_explicit_provider(self) -> None:
        """Test icepick config test --provider vertex overrides provider in config."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=100.0,
                    message="Connected.",
                    details={"provider": "vertex", "model": "gemini-1.5-pro"},
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test", "--provider", "vertex"])
            assert result.exit_code == 0
            call_kwargs = mock_tester.test_all.call_args.kwargs
            cfg_arg = call_kwargs["cfg"]
            assert cfg_arg.llm_provider == "vertex"

    def test_config_test_with_explicit_model(self) -> None:
        """Test icepick config test --model overrides model in config."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=100.0,
                    message="Connected.",
                    details={"provider": "gemini", "model": "custom-model"},
                )
            }
        )
        with patch("icepick.cli.ConnectionTester") as mock_tester_cls:
            mock_tester = MagicMock()
            mock_tester.test_all.return_value = mock_report
            mock_tester_cls.return_value = mock_tester

            result = runner.invoke(app, ["config", "test", "--model", "custom-model"])
            assert result.exit_code == 0
            call_kwargs = mock_tester.test_all.call_args.kwargs
            cfg_arg = call_kwargs["cfg"]
            assert cfg_arg.llm_model == "custom-model"

    def test_rewrite_vertex_auth_error_shows_adc_guidance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that rewrite --agentic -p vertex shows ADC guidance on project/auth failure."""
        sql_file = tmp_path / "correlated.sql"
        original_sql = (
            "SELECT c.cust_id FROM customers c "
            "WHERE EXISTS (SELECT 1 FROM orders o WHERE o.cust_id = c.cust_id)"
        )
        sql_file.write_text(original_sql, encoding="utf-8")

        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("CLOUDSDK_CORE_PROJECT", raising=False)
        result = runner.invoke(app, ["rewrite", str(sql_file), "--agentic", "--provider", "vertex"])
        assert result.exit_code == 1
        assert "Authentication Error:" in result.output
        assert "Ensure Google Cloud ADC is authenticated" in result.output
        assert "gcp_project" in result.output


class TestVerifyCLI:
    """CLI tests for the refreshed verify command (bidirectional EXCEPT SQL generation)."""

    def test_verify_cli_generates_sql_to_stdout(self, tmp_path: Path) -> None:
        """Test that icepick verify orig.sql opt.sql outputs EXCEPT query to stdout and exits 0."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT id, name FROM users WHERE id > 10;", encoding="utf-8")
        opt_file.write_text("SELECT id, name FROM users WHERE id > 10 AND 1=1;", encoding="utf-8")

        result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])
        assert result.exit_code == 0
        assert "WITH orig AS (" in result.output
        assert "opt AS (" in result.output
        assert "EXCEPT" in result.output
        assert "orig_not_in_opt" in result.output
        assert "opt_not_in_orig" in result.output
        # Verify pure SQL stdout output (no rich formatting tags)
        assert "[bold" not in result.output

    def test_verify_cli_output_to_file(self, tmp_path: Path) -> None:
        """Test that icepick verify -o verify.sql saves the verification SQL to the file and exits 0."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        out_file = tmp_path / "verify.sql"
        orig_file.write_text("SELECT 1 AS x", encoding="utf-8")
        opt_file.write_text("SELECT 1 AS x", encoding="utf-8")

        result = runner.invoke(
            app, ["verify", str(orig_file), str(opt_file), "-o", str(out_file)]
        )
        assert result.exit_code == 0
        assert "Verification SQL saved to" in result.output
        assert out_file.exists()
        saved_sql = out_file.read_text(encoding="utf-8")
        assert "WITH orig AS (" in saved_sql
        assert "EXCEPT" in saved_sql

    def test_verify_cli_count_only_flag(self, tmp_path: Path) -> None:
        """Test that icepick verify --count-only generates count aggregation query containing COUNT(*) AS cnt."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT id FROM users", encoding="utf-8")
        opt_file.write_text("SELECT id FROM users", encoding="utf-8")

        result = runner.invoke(app, ["verify", str(orig_file), str(opt_file), "--count-only"])
        assert result.exit_code == 0
        assert "COUNT(*) AS cnt" in result.output
        assert "WITH orig AS (" in result.output
        assert "EXCEPT" in result.output

    def test_verify_cli_invalid_dialect(self, tmp_path: Path) -> None:
        """Test that verify with invalid --dialect outputs supported dialect enum choices and exits 1."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(
            app, ["verify", str(orig_file), str(opt_file), "--dialect", "unknown_sql"]
        )
        assert result.exit_code == 1
        assert "error: Invalid dialect 'unknown_sql'" in result.output
        assert "snowflake" in result.output
        assert "postgres" in result.output
        assert "duckdb" in result.output
        assert "bigquery" in result.output

    def test_verify_cli_read_error(self, tmp_path: Path) -> None:
        """Test that verify exits with code 2 when reading files fails."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("Read error")):
            result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])
            assert result.exit_code == 2
            assert "Error reading files" in result.output

    def test_verify_cli_write_error(self, tmp_path: Path) -> None:
        """Test that verify exits with code 2 when writing output file fails."""
        orig_file = tmp_path / "orig.sql"
        opt_file = tmp_path / "opt.sql"
        out_file = tmp_path / "readonly" / "verify.sql"
        orig_file.write_text("SELECT 1", encoding="utf-8")
        opt_file.write_text("SELECT 1", encoding="utf-8")

        with patch.object(Path, "write_text", side_effect=OSError("Permission denied")):
            result = runner.invoke(
                app, ["verify", str(orig_file), str(opt_file), "-o", str(out_file)]
            )
            assert result.exit_code == 2
            assert "Error writing output file" in result.output

