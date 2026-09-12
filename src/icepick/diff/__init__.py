"""Diff generation and terminal formatting module."""

from icepick.diff.formatter import (
    DiffFormatter,
    DiffHunk,
    format_diff,
    normalize_sql,
    render_diff,
    split_hunks,
)

__all__ = [
    "DiffFormatter",
    "DiffHunk",
    "format_diff",
    "normalize_sql",
    "render_diff",
    "split_hunks",
]
