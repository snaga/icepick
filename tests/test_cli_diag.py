"""CLI tests for icepick diag command using typer.testing.CliRunner.

Validates human-readable Rich cards output, machine-readable JSON output,
severity threshold filtering, dialect validation, and syntax error handling.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from icepick.cli import app

runner = CliRunner()


class TestCliDiag:
    """Test suite for the 'icepick diag' command."""

    def test_cli_diag_clean_sql(self, tmp_path: Path) -> None:
        """Verify that a clean query without anti-patterns exits with 0 and prints clean message."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id, name FROM users WHERE id = 10", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file)])
        assert result.exit_code == 0
        assert "No optimization issues found" in result.output
        assert "Clean query!" in result.output

    def test_cli_diag_issues_text_output(self, tmp_path: Path) -> None:
        """Verify that a query with anti-patterns prints Rich cards and exits with 1."""
        sql_file = tmp_path / "issues.sql"
        # Triggers SNOW-001 (DATE(created_at)) and SNOW-003 (ORDER BY in CTE)
        sql = """
        WITH cte_sub AS (
            SELECT id, name FROM users ORDER BY name
        )
        SELECT id FROM cte_sub WHERE DATE(created_at) = '2024-01-01'
        """
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file)])
        assert result.exit_code == 1
        assert "Prescription Plan:" in result.output
        assert "RX-001" in result.output
        assert "Expected Impact:" in result.output
        assert "Rationale:" in result.output
        assert "Original SQL:" in result.output

    def test_cli_diag_json_output_format_option(self, tmp_path: Path) -> None:
        """Verify that --format json outputs machine-readable PrescriptionPlan JSON schema."""
        sql_file = tmp_path / "issues.sql"
        sql = "SELECT id, name FROM tbl WHERE DATE(created_at) = '2023-01-01'"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["schema_version"] == "1.0"
        assert data["file"] == str(sql_file)
        assert data["issues_count"] >= 1
        assert len(data["prescriptions"]) >= 1
        assert data["prescriptions"][0]["id"] == "RX-001"
        assert data["prescriptions"][0]["rule_id"] == "SNOW-001"

    def test_cli_diag_json_output_flag(self, tmp_path: Path) -> None:
        """Verify that --json flag behaves identically to --format json."""
        sql_file = tmp_path / "issues.sql"
        sql = "SELECT id, name FROM tbl WHERE DATE(created_at) = '2023-01-01'"
        sql_file.write_text(sql, encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["schema_version"] == "1.0"
        assert data["issues_count"] >= 1

    def test_cli_diag_clean_sql_json_output(self, tmp_path: Path) -> None:
        """Verify that clean SQL with --format json exits with 0 and returns empty prescriptions list."""
        sql_file = tmp_path / "clean.sql"
        sql_file.write_text("SELECT id, name FROM users WHERE id = 10", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["schema_version"] == "1.0"
        assert data["issues_count"] == 0
        assert data["prescriptions"] == []

    def test_cli_diag_severity_filter(self, tmp_path: Path) -> None:
        """Verify that --severity filters out prescriptions below threshold."""
        sql_file = tmp_path / "multi_issues.sql"
        # Triggers SNOW-001 (HIGH) and SNOW-003 (MEDIUM)
        sql = """
        WITH cte_sub AS (
            SELECT id, name FROM users ORDER BY name
        )
        SELECT id FROM cte_sub WHERE DATE(created_at) = '2024-01-01'
        """
        sql_file.write_text(sql, encoding="utf-8")

        # Without filter: both HIGH and MEDIUM appear
        res_all = runner.invoke(app, ["diag", str(sql_file), "--json"])
        assert res_all.exit_code == 1
        data_all = json.loads(res_all.output)
        severities_all = {rx["severity"] for rx in data_all["prescriptions"]}
        assert "HIGH" in severities_all
        assert "MEDIUM" in severities_all

        # With --severity HIGH: MEDIUM is excluded
        res_high = runner.invoke(app, ["diag", str(sql_file), "--severity", "HIGH", "--json"])
        assert res_high.exit_code == 1
        data_high = json.loads(res_high.output)
        severities_high = {rx["severity"] for rx in data_high["prescriptions"]}
        assert "HIGH" in severities_high
        assert "MEDIUM" not in severities_high

        # With --severity CRITICAL: neither HIGH nor MEDIUM meet CRITICAL, so 0 issues -> exit 0
        res_crit = runner.invoke(app, ["diag", str(sql_file), "--severity", "CRITICAL", "--json"])
        assert res_crit.exit_code == 0
        data_crit = json.loads(res_crit.output)
        assert data_crit["issues_count"] == 0

    def test_cli_diag_syntax_error(self, tmp_path: Path) -> None:
        """Verify that invalid SQL syntax results in exit code 2 and parse error message."""
        sql_file = tmp_path / "bad.sql"
        sql_file.write_text("SELECT FROM ;;;", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file)])
        assert result.exit_code == 2
        assert "Parse Error" in result.output

    def test_cli_diag_invalid_severity(self, tmp_path: Path) -> None:
        """Verify that invalid severity threshold produces actionable guidance and exits 1."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--severity", "SUPER_HIGH"])
        assert result.exit_code == 1
        assert "Invalid severity 'SUPER_HIGH'" in result.output
        assert "CRITICAL, HIGH, MEDIUM, LOW" in result.output

    def test_cli_diag_invalid_format(self, tmp_path: Path) -> None:
        """Verify that invalid format produces error and exits 1."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--format", "yaml"])
        assert result.exit_code == 1
        assert "Invalid format 'yaml'" in result.output

    def test_cli_diag_invalid_dialect(self, tmp_path: Path) -> None:
        """Verify that unsupported dialect produces error and exits 1."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        result = runner.invoke(app, ["diag", str(sql_file), "--dialect", "oracle"])
        assert result.exit_code == 1
        assert "Invalid dialect 'oracle'" in result.output

    def test_cli_diag_missing_file(self, tmp_path: Path) -> None:
        """Verify that missing file fails with exit code non-zero."""
        missing = tmp_path / "non_existent.sql"
        result = runner.invoke(app, ["diag", str(missing)])
        assert result.exit_code != 0

    def test_cli_diag_with_config_file(self, tmp_path: Path) -> None:
        """Verify that diag command respects configuration disabling a rule."""
        sql_file = tmp_path / "query.sql"
        sql_file.write_text(
            "SELECT * FROM orders WHERE DATE(created_at) = '2023-01-01'", encoding="utf-8"
        )

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(
            json.dumps({"disabled_rules": ["SNOW-001"]}),
            encoding="utf-8",
        )

        # With config disabling SNOW-001, no issues detected
        result = runner.invoke(app, ["diag", str(sql_file), "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "No optimization issues found" in result.output
