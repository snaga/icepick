"""Feedback recording functionality for Icepick.

Provides structures and utilities to record developer and agent feedback
(friction, bug, doc, idea) into a local JSON Lines file.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from icepick import __version__


class FeedbackCategory(str, Enum):
    """Allowed categories for user/agent feedback."""

    FRICTION = "friction"
    BUG = "bug"
    DOC = "doc"
    IDEA = "idea"


VALID_CATEGORIES: tuple[str, ...] = tuple(c.value for c in FeedbackCategory)


@dataclass
class FeedbackEntry:
    """Represents a single recorded feedback entry.

    Attributes:
        timestamp: ISO 8601 formatted UTC timestamp string.
        version: Icepick version string.
        category: Category of feedback (friction, bug, doc, idea).
        message: Feedback content message.
        cwd: Current working directory where the feedback command was executed.
        git_commit: Current Git commit hash if in a Git repository, else None.
    """

    timestamp: str
    version: str
    category: str
    message: str
    cwd: str
    git_commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to a dictionary representation."""
        return asdict(self)


def get_git_commit(cwd: Path | str | None = None) -> str | None:
    """Retrieve the current Git commit hash (HEAD) if available.

    Args:
        cwd: Directory in which to execute git command.

    Returns:
        The commit hash string, or None if git is unavailable or not in a repo.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit = res.stdout.strip()
        return commit if commit else None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


class FeedbackRecorder:
    """Appends feedback entries to a local JSON Lines log file.

    Attributes:
        log_path: Path to the target JSON Lines log file.
    """

    def __init__(self, log_path: Path | str | None = None) -> None:
        """Initialize FeedbackRecorder.

        Args:
            log_path: Path to feedback JSON Lines file. Defaults to
                      '.icepick_feedback.jsonl' in the current working directory.
        """
        if log_path is not None:
            self.log_path = Path(log_path)
        else:
            self.log_path = Path.cwd() / ".icepick_feedback.jsonl"

    def record(self, message: str, category: str = "friction") -> FeedbackEntry:
        """Validate and record a feedback entry to the log file.

        Args:
            message: The feedback description or issue message.
            category: One of 'friction', 'bug', 'doc', 'idea'.

        Returns:
            The created and saved FeedbackEntry.

        Raises:
            ValueError: If category is not in the allowed list of categories.
        """
        category_clean = category.strip().lower()
        if category_clean not in VALID_CATEGORIES:
            valid_list = ", ".join(f"'{cat}'" for cat in VALID_CATEGORIES)
            raise ValueError(
                f"Invalid feedback category '{category}'. Accepted categories are: {valid_list}"
            )

        timestamp = datetime.now(timezone.utc).isoformat()
        cwd = str(Path.cwd())
        git_commit = get_git_commit(cwd)

        entry = FeedbackEntry(
            timestamp=timestamp,
            version=__version__,
            category=category_clean,
            message=message,
            cwd=cwd,
            git_commit=git_commit,
        )

        self._append_entry(entry)
        return entry

    def _append_entry(self, entry: FeedbackEntry) -> None:
        """Append entry as a JSON line to the target log file."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with self.log_path.open(mode="a", encoding="utf-8") as f:
            f.write(line + "\n")
