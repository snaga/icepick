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
    """CLI tests for icepick general commands (version, help, config)."""

    def test_cli_version(self) -> None:
        """Test that --version displays the application version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert f"icepick version {__version__}" in result.output

    def test_cli_help(self) -> None:
        """Test that --help displays new commands and no legacy commands."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # New commands present
        for cmd in ["diag", "diff", "fix", "verify", "feedback", "agent-context", "config"]:
            assert cmd in result.output
        # Legacy commands absent
        for cmd in ["check", "rewrite", "patch"]:
            assert cmd not in result.output

    def test_config_show_table(self) -> None:
        """Test icepick config show displays configuration table with options, sources."""
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "Icepick Resolved Configuration" in result.output
        assert "Option" in result.output
        assert "Resolved Value" in result.output
        assert "Source" in result.output
        assert "Secret?" in result.output
        assert "dialect" in result.output
        assert "default" in result.output

    def test_config_show_json(self) -> None:
        """Test icepick config show --json returns structured JSON dictionary with provenance."""
        result = runner.invoke(app, ["config", "show", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "dialect" in data
        assert "value" in data["dialect"]
        assert "source" in data["dialect"]


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

        result = runner.invoke(app, ["verify", str(orig_file), str(opt_file), "-o", str(out_file)])
        assert result.exit_code == 0
        assert "[OK] Verification SQL saved to" in result.output
        assert out_file.exists()
        # Verify Windows CP932 / Shift_JIS compatibility
        assert len(result.output.encode("cp932")) > 0
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
