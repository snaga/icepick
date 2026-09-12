"""Tests for feedback recording module and CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from icepick import __version__
from icepick.cli import app
from icepick.feedback import (
    VALID_CATEGORIES,
    FeedbackEntry,
    FeedbackRecorder,
    get_git_commit,
)

runner = CliRunner()


class TestFeedbackUnit:
    """Unit tests for FeedbackEntry, FeedbackRecorder, and helper functions."""

    def test_feedback_entry_to_dict(self) -> None:
        """Test FeedbackEntry serialization to dictionary."""
        entry = FeedbackEntry(
            timestamp="2026-09-12T00:00:00+00:00",
            version="0.1.0",
            category="friction",
            message="Slow query parsing",
            cwd="/test/dir",
            git_commit="abcdef1234567890",
        )
        data = entry.to_dict()
        assert data["timestamp"] == "2026-09-12T00:00:00+00:00"
        assert data["version"] == "0.1.0"
        assert data["category"] == "friction"
        assert data["message"] == "Slow query parsing"
        assert data["cwd"] == "/test/dir"
        assert data["git_commit"] == "abcdef1234567890"

    def test_recorder_default_log_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test that default log path is .icepick_feedback.jsonl in current directory."""
        monkeypatch.chdir(tmp_path)
        recorder = FeedbackRecorder()
        assert recorder.log_path == tmp_path / ".icepick_feedback.jsonl"

    def test_record_success_appends_to_jsonl(self, tmp_path: Path) -> None:
        """Test that record creates file and appends successive JSON Lines."""
        log_file = tmp_path / "subdir" / "feedback.jsonl"
        recorder = FeedbackRecorder(log_path=log_file)

        # 1st record
        entry1 = recorder.record("First issue encountered", category="bug")
        assert log_file.exists()
        assert entry1.category == "bug"
        assert entry1.message == "First issue encountered"
        assert entry1.version == __version__

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record1 = json.loads(lines[0])
        assert record1["message"] == "First issue encountered"
        assert record1["category"] == "bug"

        # 2nd record (append)
        entry2 = recorder.record("Feature idea for index hint", category="idea")
        assert entry2.category == "idea"

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        record2 = json.loads(lines[1])
        assert record2["message"] == "Feature idea for index hint"
        assert record2["category"] == "idea"

    @pytest.mark.parametrize("category", ["friction", "bug", "doc", "idea"])
    def test_record_all_valid_categories(self, tmp_path: Path, category: str) -> None:
        """Test that all valid categories in VALID_CATEGORIES are accepted."""
        log_file = tmp_path / "valid.jsonl"
        recorder = FeedbackRecorder(log_path=log_file)

        # Test case insensitivity and whitespace trimming
        entry = recorder.record(f"Message for {category}", category=f" {category.upper()} ")
        assert entry.category == category

    def test_record_invalid_category_raises(self, tmp_path: Path) -> None:
        """Test that invalid category raises ValueError with acceptable choices."""
        log_file = tmp_path / "invalid.jsonl"
        recorder = FeedbackRecorder(log_path=log_file)

        with pytest.raises(ValueError) as exc_info:
            recorder.record("Invalid test", category="random_bad_category")

        err_msg = str(exc_info.value)
        assert "Invalid feedback category 'random_bad_category'" in err_msg
        for cat in VALID_CATEGORIES:
            assert cat in err_msg

    def test_get_git_commit_success(self) -> None:
        """Test get_git_commit when git command returns a valid commit hash."""
        mock_proc = MagicMock()
        mock_proc.stdout = "a1b2c3d4e5f6789012345678901234567890abcd\n"

        with patch("subprocess.run", return_value=mock_proc):
            commit = get_git_commit()
            assert commit == "a1b2c3d4e5f6789012345678901234567890abcd"

    def test_get_git_commit_not_a_repo(self) -> None:
        """Test get_git_commit when subprocess fails or git is not installed."""
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, ["git"])):
            commit = get_git_commit()
            assert commit is None

        with patch("subprocess.run", side_effect=FileNotFoundError):
            commit = get_git_commit()
            assert commit is None


class TestFeedbackCli:
    """Integration CLI tests for icepick feedback command."""

    def test_cli_feedback_basic(self, tmp_path: Path) -> None:
        """Test basic feedback command execution."""
        log_file = tmp_path / "test_feed.jsonl"
        result = runner.invoke(
            app,
            ["feedback", "Found syntax issue in CTE", "--log-file", str(log_file)],
        )
        assert result.exit_code == 0
        assert "Feedback recorded successfully" in result.output
        assert log_file.name in result.output
        assert log_file.exists()

        records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 1
        assert records[0]["message"] == "Found syntax issue in CTE"
        assert records[0]["category"] == "friction"

    def test_cli_feedback_category_option(self, tmp_path: Path) -> None:
        """Test feedback command with custom category option."""
        log_file = tmp_path / "test_feed.jsonl"
        result = runner.invoke(
            app,
            [
                "feedback",
                "Documentation typo on check command",
                "-c",
                "doc",
                "--log-file",
                str(log_file),
            ],
        )
        assert result.exit_code == 0

        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["category"] == "doc"
        assert record["message"] == "Documentation typo on check command"

    def test_cli_feedback_json_flag(self, tmp_path: Path) -> None:
        """Test feedback command with --json flag outputting JSON entry."""
        log_file = tmp_path / "test_feed.jsonl"
        result = runner.invoke(
            app,
            [
                "feedback",
                "Need rule for GROUP BY ALL",
                "-c",
                "idea",
                "--json",
                "--log-file",
                str(log_file),
            ],
        )
        assert result.exit_code == 0

        # Output should be valid JSON
        data = json.loads(result.output)
        assert data["message"] == "Need rule for GROUP BY ALL"
        assert data["category"] == "idea"
        assert data["version"] == __version__
        assert "timestamp" in data
        assert "cwd" in data

    def test_cli_feedback_invalid_category(self, tmp_path: Path) -> None:
        """Test feedback command with invalid category exits with code 1 and lists valid choices."""
        log_file = tmp_path / "test_feed.jsonl"
        result = runner.invoke(
            app,
            ["feedback", "Broken query", "-c", "unknown_category", "--log-file", str(log_file)],
        )
        assert result.exit_code == 1
        # Check that error message displays the invalid category and all valid categories
        combined_output = result.output + (
            result.stderr if hasattr(result, "stderr") and result.stderr else ""
        )
        assert "unknown_category" in combined_output
        for cat in ["friction", "bug", "doc", "idea"]:
            assert cat in combined_output

        # Log file should not have been created
        assert not log_file.exists()

    def test_cli_feedback_default_log_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test feedback command without --log-file defaults to current directory .icepick_feedback.jsonl."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["feedback", "Default path test message"])
        assert result.exit_code == 0

        default_file = tmp_path / ".icepick_feedback.jsonl"
        assert default_file.exists()
        record = json.loads(default_file.read_text(encoding="utf-8").strip())
        assert record["message"] == "Default path test message"
        assert record["category"] == "friction"
