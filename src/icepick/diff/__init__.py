"""Diff generation and terminal formatting module."""

from icepick.diff.formatter import (
    DiffFormatter,
    DiffHunk,
    format_diff,
    normalize_sql,
    render_diff,
    split_hunks,
)
from icepick.diff.patcher import apply_unified_diff

__all__ = [
    "DiffFormatter",
    "DiffHunk",
    "apply_unified_diff",
    "format_diff",
    "normalize_sql",
    "render_diff",
    "split_hunks",
]
