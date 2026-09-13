"""Tests for agent_context module and icepick agent-context CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from icepick import __version__
from icepick.agent_context import get_agent_context
from icepick.cli import app

runner = CliRunner()


class TestAgentContextUnit:
    """Unit tests for get_agent_context() data structure and completeness."""

    def test_top_level_keys(self) -> None:
        """Verify that get_agent_context() contains all expected top-level keys."""
        ctx = get_agent_context()
        required_keys = {
            "name",
            "version",
            "description",
            "commands",
            "rules",
            "environment_variables",
            "credentials",
        }
        assert required_keys.issubset(ctx.keys())
        assert ctx["name"] == "icepick"
        assert ctx["version"] == __version__
        assert "Snowflake" in ctx["description"]

    def test_commands_schema(self) -> None:
        """Verify that all core CLI commands are documented with arguments and options."""
        ctx = get_agent_context()
        commands = ctx["commands"]
        expected_commands = {
            "check",
            "diag",
            "diff",
            "rewrite",
            "patch",
            "verify",
            "feedback",
            "agent-context",
            "config",
        }
        assert expected_commands.issubset(commands.keys())
        assert "fix" not in commands

        for cmd_name in expected_commands:
            cmd_info = commands[cmd_name]
            assert "description" in cmd_info
            assert "arguments" in cmd_info
            assert "options" in cmd_info

        # Check argument for 'check'
        assert "file" in commands["check"]["arguments"]
        assert commands["check"]["arguments"]["file"]["required"] is True

        # Check arguments and options for 'diag'
        assert "file" in commands["diag"]["arguments"]
        assert commands["diag"]["arguments"]["file"]["required"] is True
        diag_opts = commands["diag"]["options"]
        assert "--format" in diag_opts
        assert diag_opts["--format"]["flag"] == "-f"
        assert diag_opts["--format"]["choices"] == ["text", "json"]
        assert "--json" in diag_opts
        assert diag_opts["--json"]["type"] == "bool"
        assert "--severity" in diag_opts
        assert diag_opts["--severity"]["flag"] == "-s"
        assert diag_opts["--severity"]["choices"] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        assert "--config" in diag_opts

        # Check arguments and options for 'diff'
        assert "file" in commands["diff"]["arguments"]
        assert commands["diff"]["arguments"]["file"]["required"] is True
        diff_opts = commands["diff"]["options"]
        assert "--rx" in diff_opts
        assert diff_opts["--rx"]["type"] == "str"
        assert "--output" in diff_opts
        assert diff_opts["--output"]["flag"] == "-o"
        assert "--dialect" in diff_opts
        assert "--config" in diff_opts

        # Check options for 'rewrite'
        rewrite_opts = commands["rewrite"]["options"]
        assert "--output" in rewrite_opts
        assert "--category" in rewrite_opts
        assert "--agentic" in rewrite_opts
        assert rewrite_opts["--agentic"]["type"] == "bool"
        assert "--flatten-subqueries" in rewrite_opts
        assert "--reformat" in rewrite_opts
        assert rewrite_opts["--reformat"]["type"] == "bool"
        assert "--json" in rewrite_opts
        assert "--provider" in rewrite_opts
        assert rewrite_opts["--provider"]["choices"] == ["gemini", "vertex"]
        assert "--model" in rewrite_opts
        assert "--verify-loop" not in rewrite_opts
        assert "--max-retries" not in rewrite_opts

        # Check 'config' command and subcommands
        config_cmd = commands["config"]
        assert "subcommands" in config_cmd
        assert "show" in config_cmd["subcommands"]
        show_sub = config_cmd["subcommands"]["show"]
        assert "--config" in show_sub["options"]
        assert "--json" in show_sub["options"]

        assert "test" in config_cmd["subcommands"]
        test_sub = config_cmd["subcommands"]["test"]
        assert "--target" not in test_sub["options"]
        assert "--llm" not in test_sub["options"]
        assert "--snowflake" not in test_sub["options"]
        assert "--provider" in test_sub["options"]
        assert test_sub["options"]["--provider"]["choices"] == ["gemini", "vertex"]
        assert "--model" in test_sub["options"]
        assert "--timeout" in test_sub["options"]
        assert "--config" in test_sub["options"]
        assert "--json" in test_sub["options"]

        # Check options for 'patch'
        patch_opts = commands["patch"]["options"]
        assert "--interactive" in patch_opts
        assert "--dry-run" in patch_opts
        assert "--force" in patch_opts
        assert "--json" in patch_opts

        # Check options for 'verify'
        verify_opts = commands["verify"]["options"]
        assert "--output" in verify_opts
        assert verify_opts["--output"]["flag"] == "-o"
        assert "--count-only" in verify_opts
        assert verify_opts["--count-only"]["type"] == "bool"
        assert "--dialect" in verify_opts
        assert "--dry-run" not in verify_opts
        assert "--timeout" not in verify_opts
        assert "--config" not in verify_opts
        assert "--json" not in verify_opts
        assert "snow CLI" in commands["verify"]["description"]

        # Check --dialect choices
        expected_dialects = ["snowflake", "postgres", "duckdb", "bigquery"]
        assert commands["check"]["options"]["--dialect"]["choices"] == expected_dialects
        assert commands["diag"]["options"]["--dialect"]["choices"] == expected_dialects
        assert commands["diff"]["options"]["--dialect"]["choices"] == expected_dialects
        assert commands["rewrite"]["options"]["--dialect"]["choices"] == expected_dialects
        assert commands["verify"]["options"]["--dialect"]["choices"] == expected_dialects

    def test_rules_coverage(self) -> None:
        """Verify that all rules from SNOW-001 to SNOW-007 are cataloged."""
        ctx = get_agent_context()
        rules = ctx["rules"]
        rule_ids = {r["id"] for r in rules}

        expected_rule_ids = {
            "SNOW-001",
            "SNOW-002",
            "SNOW-003",
            "SNOW-004",
            "SNOW-005",
            "SNOW-006",
            "SNOW-007",
        }
        assert expected_rule_ids.issubset(rule_ids)

        for rule in rules:
            assert "id" in rule
            assert "name" in rule
            assert "severity" in rule
            assert "description" in rule
            assert "can_auto_fix" in rule
            assert isinstance(rule["can_auto_fix"], bool)

        # Specific rule checks
        snow_001 = next(r for r in rules if r["id"] == "SNOW-001")
        assert snow_001["name"] == "NonSargableRule"
        assert snow_001["can_auto_fix"] is True
        assert snow_001["severity"] == "HIGH"

        snow_002 = next(r for r in rules if r["id"] == "SNOW-002")
        assert snow_002["name"] == "CorrelatedSubqueryRule"
        assert snow_002["can_auto_fix"] is False
        assert snow_002["severity"] == "CRITICAL"

        snow_003 = next(r for r in rules if r["id"] == "SNOW-003")
        assert snow_003["name"] == "RedundantSortRule"
        assert snow_003["can_auto_fix"] is True
        assert snow_003["severity"] == "MEDIUM"

        snow_004 = next(r for r in rules if r["id"] == "SNOW-004")
        assert snow_004["name"] == "ImplicitCrossJoinRule"
        assert snow_004["can_auto_fix"] is False
        assert snow_004["severity"] == "HIGH"

        snow_005 = next(r for r in rules if r["id"] == "SNOW-005")
        assert snow_005["name"] == "DuplicateTableScanRule"
        assert snow_005["can_auto_fix"] is False
        assert snow_005["severity"] == "MEDIUM"

        snow_006 = next(r for r in rules if r["id"] == "SNOW-006")
        assert snow_006["name"] == "UnionToUnionAllRule"
        assert snow_006["can_auto_fix"] is True
        assert snow_006["severity"] == "LOW"

        snow_007 = next(r for r in rules if r["id"] == "SNOW-007")
        assert snow_007["name"] == "NestedSubqueryRule"
        assert snow_007["can_auto_fix"] is True
        assert snow_007["severity"] == "MEDIUM"

    def test_environment_variables_debug_only(self) -> None:
        """Verify that environment variables only expose DEBUG_ICEPICK_ prefixed vars."""
        ctx = get_agent_context()
        env_vars = ctx["environment_variables"]

        assert "DEBUG_ICEPICK_GEMINI_API_KEY" in env_vars
        assert "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD" not in env_vars

        # Ensure no generic/broad environment variables are included
        for var_name in env_vars:
            assert var_name.startswith("DEBUG_ICEPICK_")

    def test_credentials_schema(self) -> None:
        """Verify credential targets and command examples."""
        ctx = get_agent_context()
        creds = ctx["credentials"]

        assert "icepick:gemini_api_key" in creds
        assert "icepick:snowflake_password" not in creds
        for cred_target, cred_info in creds.items():
            assert cred_target.startswith("icepick:")
            assert "description" in cred_info
            assert "cmdkey_example" in cred_info


class TestAgentContextCli:
    """CLI tests for icepick agent-context command."""

    def test_agent_context_default_json_output(self) -> None:
        """Verify agent-context exits with 0 and outputs valid JSON by default."""
        result = runner.invoke(app, ["agent-context"])
        assert result.exit_code == 0

        parsed = json.loads(result.output)
        assert parsed["name"] == "icepick"
        assert parsed["version"] == __version__
        assert "commands" in parsed
        assert "rules" in parsed
        assert "environment_variables" in parsed
        assert "credentials" in parsed

    def test_agent_context_with_json_flag(self) -> None:
        """Verify agent-context --json exits with 0 and outputs valid JSON."""
        result = runner.invoke(app, ["agent-context", "--json"])
        assert result.exit_code == 0

        parsed = json.loads(result.output)
        assert parsed["name"] == "icepick"
        assert len(parsed["rules"]) >= 7

    def test_agent_context_no_json(self) -> None:
        """Verify agent-context --no-json exits with 0 and prints human-readable summary."""
        result = runner.invoke(app, ["agent-context", "--no-json"])
        assert result.exit_code == 0
        assert "icepick" in result.output
        assert "Snowflake" in result.output


class TestFeedbackCliErrorHandling:
    """Tests for actionable error handling in feedback command."""

    def test_feedback_oserror_displays_actionable_error(self) -> None:
        """Verify OSError during feedback recording prints actionable error and exits 1."""
        with patch("icepick.cli.FeedbackRecorder.record", side_effect=OSError("Permission denied")):
            result = runner.invoke(app, ["feedback", "Encountered slow query execution"])
            assert result.exit_code == 1
            assert "File Error:" in result.output
            assert "Actionable Advice:" in result.output
            assert "--log-file" in result.output
