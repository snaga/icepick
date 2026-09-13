"""London-style CLI tests for icepick using typer.testing.CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from icepick import __version__
from icepick.cli import app
from icepick.health import ConnectionHealthReport, ServiceTestResult

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
        assert "gemini_api_key" in data
        assert data["gemini_api_key"]["value"] is None or "..." in str(
            data["gemini_api_key"]["value"]
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

    def test_config_test_success(self) -> None:
        """Test icepick config test succeeds and prints health table."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=True,
                    duration_ms=123.4,
                    message="Successfully connected to LLM provider.",
                    details={"provider": "gemini", "model": "gemini-3.8-flash"},
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
            assert "Icepick Connection Health Check" in result.output
            assert "PASS" in result.output
            assert "123.4ms" in result.output
            assert "provider=gemini" in result.output

    def test_config_test_failure_exits_1_with_advice(self) -> None:
        """Test icepick config test exits with 1 and displays advice panel when check fails."""
        mock_report = ConnectionHealthReport(
            results={
                "llm": ServiceTestResult(
                    service="llm",
                    success=False,
                    duration_ms=45.0,
                    message="Gemini API key is invalid.",
                    actionable_advice="Run cmdkey /generic:icepick:gemini_api_key /user:gemini /pass:<key> to register.",
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
            assert "cmdkey /generic:icepick:gemini_api_key" in result.output

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

            res_pass = runner.invoke(app, ["config", "test", "--json"])
            assert res_pass.exit_code == 0
            data_pass = json.loads(res_pass.output)
            assert data_pass["all_passed"] is True
            assert "llm" in data_pass["results"]
            assert data_pass["results"]["llm"]["success"] is True

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

            res_fail = runner.invoke(app, ["config", "test", "--json"])
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
