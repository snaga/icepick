"""Agent Readiness Tests (REQ-E-8, Design 3.9).

Validates that Icepick operates safely, reliably, and deterministically
when autonomously executed by AI coding agents.
"""

from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from icepick import __version__
from icepick.cli import VALID_DIALECTS, app
from icepick.diff import normalize_sql
from icepick.feedback import VALID_CATEGORIES

runner = CliRunner()


def _assert_valid_json_and_no_ansi(raw_text: str) -> Any:
    """Validate that the text can be parsed as JSON and contains no ANSI escape codes.

    Args:
        raw_text: Raw standard output text to validate.

    Returns:
        The deserialized JSON Python object.

    Raises:
        AssertionError: If raw_text contains ANSI escape sequences or fails JSON parsing.
    """
    ansi_pattern = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    assert not ansi_pattern.search(raw_text), (
        f"ANSI escape sequence detected in JSON output: {raw_text!r}"
    )
    return json.loads(raw_text)


class TestAgentReadiness:
    """Agent Readiness Test Suite for validating autonomous CLI reliability."""

    def test_readiness_no_hang_on_non_interactive_stdin(self, tmp_path: Path) -> None:
        """Verify that CLI commands terminate promptly without hanging when stdin is non-interactive EOF."""
        sql_file = tmp_path / "query_with_sort.sql"
        sql_content = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub\n"
        sql_file.write_text(sql_content, encoding="utf-8")

        # 1. Test patch --interactive with empty/EOF stdin stream
        t_start = time.perf_counter()
        with patch("sys.stdin", io.StringIO("")):
            result_interactive = runner.invoke(
                app, ["patch", str(sql_file), "--interactive"], input=""
            )
        elapsed = time.perf_counter() - t_start

        # Execution must terminate immediately (within 2 seconds) and not hang waiting for input
        assert elapsed < 2.0, (
            f"Command execution took too long ({elapsed:.2f}s), possible hang on stdin"
        )
        assert result_interactive.exit_code in (0, 1)

        # 2. Test rewrite batch execution with non-interactive stdin
        t_start = time.perf_counter()
        with patch("sys.stdin", io.StringIO("")):
            result_batch = runner.invoke(app, ["rewrite", str(sql_file)], input="")
        elapsed_batch = time.perf_counter() - t_start

        assert elapsed_batch < 2.0, f"Batch rewrite took too long ({elapsed_batch:.2f}s)"
        assert result_batch.exit_code == 0

        # 3. Test check with non-interactive stdin
        t_start = time.perf_counter()
        with patch("sys.stdin", io.StringIO("")):
            result_check = runner.invoke(app, ["check", str(sql_file)], input="")
        elapsed_check = time.perf_counter() - t_start

        assert elapsed_check < 2.0, f"Check took too long ({elapsed_check:.2f}s)"
        assert result_check.exit_code in (0, 1)

    def test_readiness_all_commands_json_parseable(self, tmp_path: Path) -> None:
        """Verify that all commands with --json output clean, ANSI-free, parseable JSON on stdout."""
        # 1. check --json with detected issues
        dirty_sql = tmp_path / "issues.sql"
        dirty_sql_content = "SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub WHERE DATE(created_at) = '2023-01-01'"
        dirty_sql.write_text(normalize_sql(dirty_sql_content), encoding="utf-8")
        res_check_issues = runner.invoke(app, ["check", str(dirty_sql), "--json"])
        assert res_check_issues.exit_code == 1
        data_check_issues = _assert_valid_json_and_no_ansi(res_check_issues.stdout)
        assert isinstance(data_check_issues, list)
        assert len(data_check_issues) >= 2
        assert any(item["rule_id"] == "SNOW-001" for item in data_check_issues)
        assert any(item["rule_id"] == "SNOW-003" for item in data_check_issues)

        # 2. check --json with clean SQL
        clean_sql = tmp_path / "clean.sql"
        clean_sql.write_text("SELECT id, name FROM tbl WHERE id = 1", encoding="utf-8")
        res_check_clean = runner.invoke(app, ["check", str(clean_sql), "--json"])
        assert res_check_clean.exit_code == 0
        data_check_clean = _assert_valid_json_and_no_ansi(res_check_clean.stdout)
        assert isinstance(data_check_clean, list)
        assert len(data_check_clean) == 0

        # 3. rewrite --json and patch --json (--dry-run)
        res_rewrite = runner.invoke(app, ["rewrite", str(dirty_sql), "--json"])
        assert res_rewrite.exit_code == 0
        data_rewrite = _assert_valid_json_and_no_ansi(res_rewrite.stdout)
        assert isinstance(data_rewrite, dict)
        assert data_rewrite["file"] == str(dirty_sql)
        assert data_rewrite["has_changes"] is True
        assert len(data_rewrite["diff"]) > 0

        patch_file = tmp_path / "readiness.patch"
        patch_file.write_text(data_rewrite["diff"], encoding="utf-8")
        res_patch = runner.invoke(
            app, ["patch", str(dirty_sql), str(patch_file), "--dry-run", "--json"]
        )
        assert res_patch.exit_code == 0
        data_patch = _assert_valid_json_and_no_ansi(res_patch.stdout)
        assert isinstance(data_patch, dict)
        assert data_patch["status"] == "dry_run"

        # 4. feedback --json
        feedback_log = tmp_path / "readiness_feedback.jsonl"
        res_feedback = runner.invoke(
            app,
            [
                "feedback",
                "Automated agent verification feedback entry",
                "--category",
                "idea",
                "--log-file",
                str(feedback_log),
                "--json",
            ],
        )
        assert res_feedback.exit_code == 0
        data_feedback = _assert_valid_json_and_no_ansi(res_feedback.stdout)
        assert isinstance(data_feedback, dict)
        assert data_feedback["message"] == "Automated agent verification feedback entry"
        assert data_feedback["category"] == "idea"
        assert data_feedback["version"] == __version__
        assert "timestamp" in data_feedback
        assert "cwd" in data_feedback

        # 5. agent-context --json
        res_ctx = runner.invoke(app, ["agent-context", "--json"])
        assert res_ctx.exit_code == 0
        data_ctx = _assert_valid_json_and_no_ansi(res_ctx.stdout)
        assert isinstance(data_ctx, dict)
        assert data_ctx["name"] == "icepick"
        assert data_ctx["version"] == __version__
        assert "commands" in data_ctx
        assert "rules" in data_ctx
        assert "environment_variables" in data_ctx
        assert "credentials" in data_ctx

        # 6. config show --json
        res_cfg = runner.invoke(app, ["config", "show", "--json"])
        assert res_cfg.exit_code == 0
        data_cfg = _assert_valid_json_and_no_ansi(res_cfg.stdout)
        assert isinstance(data_cfg, dict)
        assert "llm_provider" in data_cfg
        assert "llm_model" in data_cfg
        assert "dialect" in data_cfg
        assert data_cfg["llm_provider"]["value"] in ("gemini", "vertex")
        assert "source" in data_cfg["llm_provider"]

    def test_readiness_all_commands_help_succeeds(self) -> None:
        """Verify that all commands and subcommands produce valid help messages and exit 0."""
        commands_to_test = [
            ["--help"],
            ["check", "--help"],
            ["rewrite", "--help"],
            ["patch", "--help"],
            ["verify", "--help"],
            ["feedback", "--help"],
            ["agent-context", "--help"],
            ["config", "--help"],
            ["config", "show", "--help"],
        ]
        for cmd in commands_to_test:
            result = runner.invoke(app, cmd)
            assert result.exit_code == 0, f"Command {cmd} failed with exit code {result.exit_code}"
            assert (
                "Usage" in result.stdout
                or "Options" in result.stdout
                or "Commands" in result.stdout
            )

    def test_readiness_actionable_error_on_missing_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that verify without credentials exits 1 with actionable recovery commands in stderr."""
        orig_file = tmp_path / "original.sql"
        opt_file = tmp_path / "optimized.sql"
        orig_file.write_text("SELECT id FROM users", encoding="utf-8")
        opt_file.write_text("SELECT id FROM users WHERE 1=1", encoding="utf-8")

        # Ensure no environment variables or WCM credentials exist
        monkeypatch.delenv("DEBUG_ICEPICK_SNOWFLAKE_PASSWORD", raising=False)
        monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)

        with patch("icepick.security.credentials.read_wcm_credential", return_value=None):
            result = runner.invoke(app, ["verify", str(orig_file), str(opt_file)])

        # Must exit with code 1 (Authentication Error)
        assert result.exit_code == 1

        # Actionable instructions must be present in stderr
        stderr_text = result.stderr
        assert "[Authentication Error]" in stderr_text
        assert "snowflake_password" in stderr_text

        # Verify PowerShell masked credential guidance
        assert '$cred = Get-Credential -UserName "any"' in stderr_text
        assert "cmdkey /generic:icepick:snowflake_password" in stderr_text

        # Verify debug environment variable fallback guidance
        assert '$env:DEBUG_ICEPICK_SNOWFLAKE_PASSWORD="<your_key>"' in stderr_text

    def test_readiness_enumerated_error_on_invalid_arguments(self, tmp_path: Path) -> None:
        """Verify that invalid arguments yield exit code 1 and display accepted enum choices in stderr."""
        sql_file = tmp_path / "dummy.sql"
        sql_file.write_text("SELECT 1", encoding="utf-8")

        # 1. Invalid dialect in 'check' command
        res_check_bad_dialect = runner.invoke(
            app, ["check", str(sql_file), "--dialect", "unsupported_dialect"]
        )
        assert res_check_bad_dialect.exit_code == 1
        for dialect in VALID_DIALECTS:
            assert f"'{dialect}'" in res_check_bad_dialect.stderr

        # 2. Invalid dialect in 'rewrite' command
        res_rewrite_bad_dialect = runner.invoke(
            app, ["rewrite", str(sql_file), "--dialect", "unsupported_dialect"]
        )
        assert res_rewrite_bad_dialect.exit_code == 1
        for dialect in VALID_DIALECTS:
            assert f"'{dialect}'" in res_rewrite_bad_dialect.stderr

        # 3. Invalid dialect in 'verify' command
        res_verify_bad_dialect = runner.invoke(
            app,
            ["verify", str(sql_file), str(sql_file), "--dialect", "unsupported_dialect"],
        )
        assert res_verify_bad_dialect.exit_code == 1
        for dialect in VALID_DIALECTS:
            assert f"'{dialect}'" in res_verify_bad_dialect.stderr

        # 4. Invalid category in 'feedback' command
        res_bad_category = runner.invoke(
            app, ["feedback", "Friction note", "--category", "invalid_category"]
        )
        assert res_bad_category.exit_code == 1
        for category in VALID_CATEGORIES:
            assert f"'{category}'" in res_bad_category.stderr

    def test_readiness_safe_mutation_boundary(self, tmp_path: Path) -> None:
        """Verify safe mutation boundary: non-TTY without --force is blocked, --dry-run prevents mutations."""
        sql_file = tmp_path / "mutable.sql"
        original_sql = normalize_sql("SELECT * FROM (SELECT id FROM tbl ORDER BY id) AS sub\n")
        sql_file.write_text(original_sql, encoding="utf-8")

        # Generate a patch via rewrite
        res_rewrite = runner.invoke(
            app, ["rewrite", str(sql_file), "--output", str(tmp_path / "test.patch")]
        )
        assert res_rewrite.exit_code == 0
        patch_file = tmp_path / "test.patch"
        assert patch_file.exists()

        # 1. Attempt patch without --force in non-interactive environment (sys.stdin.isatty() is False)
        with patch("sys.stdin.isatty", return_value=False):
            res_patch_no_force = runner.invoke(app, ["patch", str(sql_file), str(patch_file)])

        assert res_patch_no_force.exit_code == 1
        assert (
            "error: Overwriting files in non-interactive environment requires --force flag"
            in res_patch_no_force.stderr
        )
        # File must remain completely untouched
        assert sql_file.read_text(encoding="utf-8") == original_sql

        # 2. Attempt patch with --dry-run (simulation only)
        res_dry_run = runner.invoke(
            app,
            [
                "patch",
                str(sql_file),
                str(patch_file),
                "--dry-run",
            ],
        )
        assert res_dry_run.exit_code == 0
        # File must remain untouched
        assert sql_file.read_text(encoding="utf-8") == original_sql

        # 3. Contrast check: Explicit --force safely applies changes
        with patch("sys.stdin.isatty", return_value=False):
            res_patch_force = runner.invoke(
                app, ["patch", str(sql_file), str(patch_file), "--force"]
            )

        assert res_patch_force.exit_code == 0
        modified_sql = sql_file.read_text(encoding="utf-8")
        assert modified_sql != original_sql
        assert "ORDER BY" not in modified_sql
