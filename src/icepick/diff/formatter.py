"""Unified Diff formatter and renderer for SQL queries.

Uses difflib and sqlglot to generate noise-free, AST-normalized unified diffs,
and rich for colored terminal rendering.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from rich.console import Console
from rich.syntax import Syntax

from icepick.parser import parse_snowflake_sql


@dataclass
class DiffHunk:
    """Represents a single unified diff hunk.

    Attributes:
        header: The hunk range header (e.g. '@@ -1,5 +1,6 @@').
        lines: The diff lines within this hunk (starting with ' ', '+', or '-').
    """

    header: str
    lines: list[str]

    def to_text(self) -> str:
        """Convert the hunk back to standard Unified Diff text."""
        return "\n".join([self.header, *self.lines]) + "\n"


def normalize_sql(sql: str, dialect: str = "snowflake") -> str:
    """Normalize and pretty-format SQL using sqlglot for canonical comparison.

    Args:
        sql: SQL query string.
        dialect: SQL dialect name (default: "snowflake").

    Returns:
        str: Formatted and normalized SQL text.
    """
    ast = parse_snowflake_sql(sql, dialect=dialect)
    return ast.sql(dialect=dialect, pretty=True)


def format_diff(
    original_sql: str,
    modified_sql: str,
    filename: str = "query.sql",
    normalize: bool = True,
    dialect: str = "snowflake",
) -> str:
    """Generate a Unified Diff between original and modified SQL strings.

    Args:
        original_sql: The original SQL text.
        modified_sql: The modified/optimized SQL text.
        filename: File path / label to use in the diff header (default: "query.sql").
        normalize: Whether to normalize AST formatting before diffing to eliminate
            indentation and whitespace false positives (default: True).
        dialect: SQL dialect for normalization (default: "snowflake").

    Returns:
        str: Unified diff text conforming to standard patch format,
             or empty string if there are no semantic differences.
    """
    if normalize:
        src = normalize_sql(original_sql, dialect=dialect)
        dst = normalize_sql(modified_sql, dialect=dialect)
    else:
        src = original_sql
        dst = modified_sql

    src_lines = src.splitlines()
    dst_lines = dst.splitlines()

    from_header = f"a/{filename}"
    to_header = f"b/{filename}"

    diff_lines = list(
        difflib.unified_diff(
            src_lines,
            dst_lines,
            fromfile=from_header,
            tofile=to_header,
            lineterm="",
        )
    )

    if not diff_lines:
        return ""

    return "\n".join(diff_lines) + "\n"


def render_diff(
    diff_text: str,
    theme: str = "ansi_dark",
    line_numbers: bool = False,
    console: Console | None = None,
) -> Syntax:
    """Render a Unified Diff string as a rich Syntax object for terminal display.

    Args:
        diff_text: Unified diff formatted text.
        theme: Pygments syntax highlighting theme (default: "ansi_dark").
        line_numbers: Whether to show line numbers in terminal output.
        console: Optional Rich console to print the rendered diff to.

    Returns:
        Syntax: Renderable Rich syntax object.
    """
    syntax = Syntax(
        diff_text,
        lexer="diff",
        theme=theme,
        line_numbers=line_numbers,
        word_wrap=True,
    )
    if console is not None:
        console.print(syntax)
    return syntax


def split_hunks(diff_text: str) -> list[DiffHunk]:
    """Parse unified diff text into individual DiffHunk objects.

    Args:
        diff_text: Full unified diff string containing one or more hunks.

    Returns:
        list[DiffHunk]: Extracted list of DiffHunk instances.
    """
    if not diff_text.strip():
        return []

    hunks: list[DiffHunk] = []
    current_header: str | None = None
    current_lines: list[str] = []

    hunk_header_pattern = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")

    for line in diff_text.splitlines():
        if hunk_header_pattern.match(line):
            if current_header is not None:
                hunks.append(DiffHunk(header=current_header, lines=current_lines))
            current_header = line
            current_lines = []
        elif current_header is not None:
            # Only include diff content lines inside a hunk
            if line.startswith(("+", "-", " ", "\\")):
                current_lines.append(line)

    if current_header is not None:
        hunks.append(DiffHunk(header=current_header, lines=current_lines))

    return hunks


class DiffFormatter:
    """Configurable Unified Diff formatter and terminal renderer.

    Attributes:
        dialect: SQL dialect for normalization (default: "snowflake").
        theme: Syntax highlighting theme for terminal diffs (default: "ansi_dark").
    """

    def __init__(self, dialect: str = "snowflake", theme: str = "ansi_dark") -> None:
        """Initialize DiffFormatter with dialect and theme.

        Args:
            dialect: SQL dialect name.
            theme: Rich syntax theme.
        """
        self.dialect = dialect
        self.theme = theme

    def format(
        self,
        original_sql: str,
        modified_sql: str,
        filename: str = "query.sql",
        normalize: bool = True,
    ) -> str:
        """Format diff between original and modified SQL.

        Args:
            original_sql: Original query.
            modified_sql: Modified query.
            filename: Query filename for header.
            normalize: Whether to normalize AST formatting.

        Returns:
            str: Unified diff text.
        """
        return format_diff(
            original_sql,
            modified_sql,
            filename=filename,
            normalize=normalize,
            dialect=self.dialect,
        )

    def render(self, diff_text: str, line_numbers: bool = False) -> Syntax:
        """Render diff text into a Rich Syntax object.

        Args:
            diff_text: Unified diff text.
            line_numbers: Whether to show line numbers.

        Returns:
            Syntax: Rich renderable syntax object.
        """
        return render_diff(diff_text, theme=self.theme, line_numbers=line_numbers)
