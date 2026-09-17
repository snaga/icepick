"""Source-preserving targeted text splicing engine.

Performs surgical text replacements on raw SQL queries based on DiagnosticIssues
while strictly preserving user formatting, comments, indentation, casing, and line breaks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlglot import exp

from icepick.linter.base import DiagnosticIssue
from icepick.linter.rules.snow_001_sargable import _extract_column_from_func

logger = logging.getLogger(__name__)

# Tokenizes SQL fragments into string literals, identifiers, numbers, operators, or symbols
TOKEN_PATTERN = re.compile(
    r"('(?:''|[^'])*')|"  # Single-quoted string literal
    r'("(?:""|[^"])*")|'  # Double-quoted identifier
    r"([a-zA-Z0-9_]+)|"  # Word (identifier, keyword, numeric literal)
    r"(>=|<=|!=|<>|[=<>+\-*/%,;()])|"  # Multi-character or single operators/delimiters
    r"(\S)",  # Any remaining non-whitespace character
)

# Regex to safely identify comment spans and string literals in raw SQL
COMMENT_OR_LITERAL_PATTERN = re.compile(
    r"('(?:''|[^'])*')|"  # 1: Single-quoted string literal
    r'("(?:""|[^"])*")|'  # 2: Double-quoted identifier
    r"((?:--|//)[^\r\n]*)|"  # 3: Single-line comment (-- or //)
    r"(/\*[\s\S]*?\*/)",  # 4: Multi-line block comment
)

# Equivalent function names produced during AST transpilation vs raw source
FUNCTION_EQUIVALENTS: dict[str, list[str]] = {
    "TO_DATE": ["TO_DATE", "DATE", "TRY_TO_DATE"],
    "DATE": ["DATE", "TO_DATE", "TRY_TO_DATE"],
    "TO_VARCHAR": ["TO_VARCHAR", "TO_CHAR"],
    "TO_CHAR": ["TO_CHAR", "TO_VARCHAR"],
    "TO_TIMESTAMP": ["TO_TIMESTAMP", "TIMESTAMP"],
}


@dataclass(frozen=True)
class _Token:
    """Internal token representation for flexible regex pattern construction.

    Attributes:
        pattern: Regex pattern string for matching this token.
        is_word: True if the token is an alphanumeric identifier or keyword.
        is_as: True if the token represents the SQL 'AS' keyword.
    """

    pattern: str
    is_word: bool
    is_as: bool = False


def _extract_comment_spans(sql: str) -> list[tuple[int, int]]:
    """Extract character offset spans (start, end) of comments in SQL text.

    Correctly ignores comment delimiters occurring inside single-quoted string
    literals or double-quoted identifiers.

    Args:
        sql: Raw SQL text.

    Returns:
        list[tuple[int, int]]: List of (start, end) character offset tuples for comments.
    """
    comment_spans: list[tuple[int, int]] = []
    for match in COMMENT_OR_LITERAL_PATTERN.finditer(sql):
        if match.group(3) or match.group(4):
            comment_spans.append((match.start(), match.end()))
    return comment_spans


def _is_in_comment(
    sql: str,
    start: int,
    end: int,
    comment_spans: Sequence[tuple[int, int]] | None = None,
) -> bool:
    """Check if a text span [start, end] intersects with any comment span in sql.

    Args:
        sql: Raw SQL text.
        start: Start character offset.
        end: End character offset.
        comment_spans: Optional precomputed comment spans. If None, extracted from sql.

    Returns:
        bool: True if [start, end] intersects with any comment span, False otherwise.
    """
    if comment_spans is None:
        comment_spans = _extract_comment_spans(sql)
    for c_start, c_end in comment_spans:
        if not (end <= c_start or start >= c_end):
            return True
    return False


@dataclass(frozen=True)
class SplicingCandidate:
    """Internal candidate representation of a surgical text replacement span.

    Attributes:
        start: Absolute start character offset in the raw SQL text.
        end: Absolute end character offset in the raw SQL text.
        replacement: Replacement text to splice into [start:end].
        issue: Associated DiagnosticIssue.
    """

    start: int
    end: int
    replacement: str
    issue: DiagnosticIssue


def _build_flexible_pattern(sql_fragment: str) -> re.Pattern[str]:
    r"""Compile an SQL fragment into a flexible regex matching varied whitespace and casing.

    Converts tokens into regex elements where word-to-word transitions require at least
    one whitespace character (\s+), while transitions involving operators or delimiters
    allow optional whitespace (\s*). Function synonyms (e.g. TO_DATE vs DATE) are
    automatically expanded into equivalent alternatives.

    The 'AS' keyword is treated as optional (e.g. (?:\bAS\s+)? or similar) to allow
    matching SQL text regardless of whether explicit 'AS' syntax is present or omitted.

    Args:
        sql_fragment: SQL string snippet or node SQL representation.

    Returns:
        re.Pattern[str]: Compiled case-insensitive regular expression pattern.
    """
    tokens: list[_Token] = []
    for match in TOKEN_PATTERN.finditer(sql_fragment):
        s = match.group(0)
        if match.group(1) or match.group(2):  # string literal or quoted identifier
            tokens.append(_Token(re.escape(s), False))
        elif match.group(3):  # word (identifier / keyword / number)
            upper_s = s.upper()
            if upper_s == "AS":
                tokens.append(_Token(rf"\b{re.escape(s)}\b", True, is_as=True))
            elif upper_s in FUNCTION_EQUIVALENTS:
                equiv = "|".join(FUNCTION_EQUIVALENTS[upper_s])
                tokens.append(_Token(rf"\b(?:{equiv})\b", True))
            else:
                tokens.append(_Token(rf"\b{re.escape(s)}\b", True))
        else:  # operator / symbol
            tokens.append(_Token(re.escape(s), False))

    if not tokens:
        # Match nothing
        return re.compile(r"$^")

    parts: list[str] = []
    i = 0
    while i < len(tokens):
        # Handle optional AS keyword between tokens (e.g. `) AS sub` or `col AS alias`)
        if i > 0 and i < len(tokens) - 1 and tokens[i].is_as:
            prev_token = tokens[i - 1]
            next_token = tokens[i + 1]
            sep = r"\s+" if prev_token.is_word else r"\s*"
            post_sep = r"\s+" if next_token.is_word else r"\s*"
            parts.append(rf"{sep}(?:\bAS\b{post_sep})?{next_token.pattern}")
            i += 2  # Consumed both AS and next_token
            continue

        if i == 0:
            parts.append(tokens[0].pattern)
        else:
            prev_token = tokens[i - 1]
            curr_token = tokens[i]
            sep = r"\s+" if (prev_token.is_word and curr_token.is_word) else r"\s*"
            parts.append(sep + curr_token.pattern)
        i += 1

    return re.compile("".join(parts), re.IGNORECASE)


def _adjust_replacement_indent(replacement: str, base_indent: str) -> str:
    """Adjust multiline replacement string by prefixing base_indent to subsequent lines.

    Preserves single-line replacements without modification. For multiline replacements,
    applies the host line's leading whitespace to subsequent non-empty lines.

    Args:
        replacement: The replacement text.
        base_indent: Leading whitespace string of the target line.

    Returns:
        str: Indent-adjusted replacement text.
    """
    lines = replacement.split("\n")
    if len(lines) <= 1:
        return replacement

    adjusted: list[str] = [lines[0]]
    for line in lines[1:]:
        if line.strip():
            adjusted.append(base_indent + line)
        else:
            adjusted.append("")
    return "\n".join(adjusted)


def _is_overlapping(span: tuple[int, int], occupied_spans: Sequence[tuple[int, int]]) -> bool:
    """Check if a span [start, end] intersects with any already occupied spans.

    Args:
        span: (start, end) character offsets.
        occupied_spans: Sequence of previously allocated spans.

    Returns:
        bool: True if span overlaps with any occupied span, False otherwise.
    """
    s, e = span
    for occ_s, occ_e in occupied_spans:
        if not (e <= occ_s or s >= occ_e):
            return True
    return False


def _find_scoped_match(
    original_sql: str,
    pattern: re.Pattern[str],
    line_number: int | None = None,
    occupied_spans: Sequence[tuple[int, int]] = (),
) -> tuple[int, int, str] | None:
    """Find the best pattern match in original_sql using hierarchical line scoping.

    Attempts to locate the match first within a narrow window centered around
    line_number, then within a wider neighborhood, and finally across the entire SQL.
    Skips any spans that overlap with occupied_spans or intersect with comments
    to prevent spurious replacements inside user comments.

    Args:
        original_sql: Raw SQL text.
        pattern: Compiled regex pattern to search for.
        line_number: Optional 1-indexed target line number hint.
        occupied_spans: Already claimed character spans.

    Returns:
        tuple[int, int, str] | None: (start_idx, end_idx, matched_text) or None if not found.
    """
    lines = original_sql.splitlines(keepends=True)
    if not lines:
        return None

    comment_spans = _extract_comment_spans(original_sql)

    line_starts: list[int] = [0]
    for line in lines[:-1]:
        line_starts.append(line_starts[-1] + len(line))

    # Phase 1: Search within line windows if line_number is provided and valid
    if line_number is not None and 1 <= line_number <= len(lines):
        c_idx = line_number - 1
        windows: list[tuple[int, int]] = [
            (c_idx, min(len(lines), c_idx + 3)),
            (max(0, c_idx - 5), min(len(lines), c_idx + 6)),
        ]
        for w_start, w_end in windows:
            sub_start = line_starts[w_start]
            sub_end = line_starts[w_end - 1] + len(lines[w_end - 1])
            sub_text = original_sql[sub_start:sub_end]
            for m in pattern.finditer(sub_text):
                abs_s = sub_start + m.start()
                abs_e = sub_start + m.end()
                if not _is_overlapping((abs_s, abs_e), occupied_spans) and not _is_in_comment(
                    original_sql, abs_s, abs_e, comment_spans
                ):
                    return abs_s, abs_e, m.group(0)

    # Phase 2: Fallback to full SQL search
    for m in pattern.finditer(original_sql):
        abs_s = m.start()
        abs_e = m.end()
        if not _is_overlapping((abs_s, abs_e), occupied_spans) and not _is_in_comment(
            original_sql, abs_s, abs_e, comment_spans
        ):
            return abs_s, abs_e, m.group(0)

    return None


def _resolve_deletion_span(
    original_sql: str,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    """Resolve full line deletion or in-line deletion for an expression to be removed.

    If the line containing the match contains only whitespace (or comments) outside the match,
    the entire line including its trailing newline (or leading newline if at EOF)
    is marked for deletion. Otherwise, only the match and preceding whitespace are removed.

    Args:
        original_sql: Raw SQL text.
        match_start: Start offset of the matched snippet.
        match_end: End offset of the matched snippet.

    Returns:
        tuple[int, int]: Character range (del_start, del_end) to be deleted.
    """
    line_start = original_sql.rfind("\n", 0, match_start)
    line_start = 0 if line_start == -1 else line_start + 1

    line_end = original_sql.find("\n", match_end)
    has_trailing_newline = line_end != -1
    line_end_with_nl = line_end + 1 if has_trailing_newline else len(original_sql)

    left_text = original_sql[line_start:match_start]
    right_text = (
        original_sql[match_end:line_end] if has_trailing_newline else original_sql[match_end:]
    )

    left_empty = left_text.strip() == ""
    right_stripped = right_text.strip()
    right_empty = right_stripped == "" or right_stripped.startswith("--")

    if left_empty and right_empty:
        # Full line deletion
        if not has_trailing_newline and line_start > 0:
            # Trailing EOF line: remove preceding newline to avoid blank trailing line
            return line_start - 1, len(original_sql)
        return line_start, line_end_with_nl

    # In-line deletion: remove match and preceding horizontal whitespace
    del_start = match_start
    while del_start > line_start and original_sql[del_start - 1] in " \t":
        del_start -= 1
    return del_start, match_end


class TextSplicer:
    """Performs source-preserving targeted replacement on raw SQL text.

    Surgically splices AST optimization changes into raw SQL strings without
    disrupting user comments, custom indentation, line breaks, or casing styles.

    Attributes:
        dialect: SQL dialect name (default: "snowflake").
    """

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize TextSplicer with SQL dialect.

        Args:
            dialect: SQL dialect name (default: "snowflake").
        """
        self.dialect = dialect

    def _find_issue_candidate(
        self,
        original_sql: str,
        issue: DiagnosticIssue,
        occupied_spans: Sequence[tuple[int, int]] = (),
    ) -> SplicingCandidate | None:
        """Locate the text span corresponding to issue.target_node and determine replacement text.

        Args:
            original_sql: Raw SQL text.
            issue: DiagnosticIssue pointing to the offending AST node.
            occupied_spans: Sequence of already assigned character spans to avoid overlapping.

        Returns:
            SplicingCandidate | None: Splicing candidate if successfully located, else None.
        """
        if issue.target_node is None:
            return None

        # Case 1: SNOW-006 UNION to UNION ALL
        if issue.rule_id == "SNOW-006" or isinstance(issue.target_node, exp.Union):
            union_pat = re.compile(r"\bunion\b(?!\s+(?:all|distinct)\b)", re.IGNORECASE)
            match_info = _find_scoped_match(
                original_sql,
                union_pat,
                line_number=issue.line_number,
                occupied_spans=occupied_spans,
            )
            if match_info is None:
                logger.debug(
                    "TextSplicer: Unable to locate UNION keyword for issue %s", issue.rule_id
                )
                return None

            s, e, text = match_info
            if text.islower():
                repl = "union all"
            elif text.istitle():
                repl = "Union All"
            else:
                repl = "UNION ALL"
            return SplicingCandidate(start=s, end=e, replacement=repl, issue=issue)

        # Case 2: Deletion (suggested_replacement is None, e.g. SNOW-003 Redundant Sort)
        if issue.suggested_replacement is None:
            target_sql = issue.target_node.sql(dialect=self.dialect, comments=False)
            pattern = _build_flexible_pattern(target_sql)
            match_info = _find_scoped_match(
                original_sql,
                pattern,
                line_number=issue.line_number,
                occupied_spans=occupied_spans,
            )
            if match_info is None:
                logger.debug(
                    "TextSplicer: Unable to locate deletable node for issue %s", issue.rule_id
                )
                return None

            s, e, _ = match_info
            del_s, del_e = _resolve_deletion_span(original_sql, s, e)
            return SplicingCandidate(start=del_s, end=del_e, replacement="", issue=issue)

        # Case 3: Node Replacement (suggested_replacement is not None)
        # Attempt A: Dedicated sargable pattern generator for SNOW-001
        match_info = None
        if issue.rule_id == "SNOW-001" and isinstance(
            issue.target_node, (exp.EQ, exp.NEQ, exp.NullSafeEQ, exp.NullSafeNEQ)
        ):
            left, right = issue.target_node.this, issue.target_node.expression
            col_node = _extract_column_from_func(left) or _extract_column_from_func(right)
            lit_node = left if left.is_string else (right if right.is_string else None)
            if col_node and lit_node:
                col_pattern = _build_flexible_pattern(col_node.sql(comments=False)).pattern
                lit_pattern = _build_flexible_pattern(lit_node.sql(comments=False)).pattern
                sargable_pat = re.compile(
                    rf"(?:DATE|TO_DATE|TRY_TO_DATE|CAST)\s*\(\s*{col_pattern}(?:\s+AS\s+DATE)?\s*\)\s*=\s*{lit_pattern}",
                    re.IGNORECASE,
                )
                match_info = _find_scoped_match(
                    original_sql,
                    sargable_pat,
                    line_number=issue.line_number,
                    occupied_spans=occupied_spans,
                )

        # Attempt B: Target node SQL with flexible pattern
        if match_info is None:
            target_sql = issue.target_node.sql(dialect=self.dialect, comments=False)
            pattern = _build_flexible_pattern(target_sql)
            match_info = _find_scoped_match(
                original_sql,
                pattern,
                line_number=issue.line_number,
                occupied_spans=occupied_spans,
            )

        # Attempt C: Clean snippet fallback
        if match_info is None and issue.snippet:
            snippet_clean = re.sub(r"/\*.*?\*/", "", issue.snippet)
            snippet_clean = re.sub(r"--.*$", "", snippet_clean).strip()
            if snippet_clean:
                pat2 = _build_flexible_pattern(snippet_clean)
                match_info = _find_scoped_match(
                    original_sql,
                    pat2,
                    line_number=issue.line_number,
                    occupied_spans=occupied_spans,
                )

        if match_info is None:
            logger.debug(
                "TextSplicer: Unable to locate target node for issue %s in text", issue.rule_id
            )
            return None

        s, e, _ = match_info
        replacement_sql = issue.suggested_replacement.sql(dialect=self.dialect, comments=False)

        # Detect base indentation of the line containing the replacement start
        line_start = original_sql.rfind("\n", 0, s)
        line_start = 0 if line_start == -1 else line_start + 1
        indent_match = re.match(r"^[ \t]*", original_sql[line_start:])
        base_indent = indent_match.group(0) if indent_match else ""

        adjusted_repl = _adjust_replacement_indent(replacement_sql, base_indent)
        return SplicingCandidate(start=s, end=e, replacement=adjusted_repl, issue=issue)

    def splice_issue(
        self,
        original_sql: str,
        issue: DiagnosticIssue,
    ) -> tuple[str, bool]:
        """Surgically replace the text corresponding to issue.target_node in raw SQL text.

        Preserves 100% of untouched lines, comments, indentation, and formatting.

        Args:
            original_sql: Raw SQL text with original comments, indent, and casing.
            issue: DiagnosticIssue pointing to the offending AST node.

        Returns:
            tuple[str, bool]: (modified_raw_sql, True) on success,
                              or (original_sql, False) on fallback/failure.
        """
        try:
            candidate = self._find_issue_candidate(original_sql, issue)
            if candidate is None:
                return original_sql, False
            modified = (
                original_sql[: candidate.start]
                + candidate.replacement
                + original_sql[candidate.end :]
            )
            return modified, True
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, re.error) as err:
            logger.warning(
                "TextSplicer: Error during splice_issue for rule %s: %s",
                issue.rule_id,
                err,
                exc_info=True,
            )
            return original_sql, False

    def splice_all(
        self,
        original_sql: str,
        issues: Sequence[DiagnosticIssue],
    ) -> tuple[str, list[DiagnosticIssue]]:
        """Apply multiple DiagnosticIssues to the raw SQL text in reverse position order.

        Surgical replacements are performed from bottom-to-top (descending offset order)
        to ensure that character indices and offsets of earlier issues are never invalidated.

        Args:
            original_sql: Original raw SQL text.
            issues: Sequence of DiagnosticIssues to apply.

        Returns:
            tuple[str, list[DiagnosticIssue]]:
                A tuple containing the modified raw SQL text and the list of successfully
                applied issues in their original order.
        """
        candidates: list[SplicingCandidate] = []
        occupied: list[tuple[int, int]] = []

        for issue in issues:
            try:
                cand = self._find_issue_candidate(original_sql, issue, occupied_spans=occupied)
                if cand is not None:
                    candidates.append(cand)
                    occupied.append((cand.start, cand.end))
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, re.error) as err:
                logger.warning(
                    "TextSplicer: Error resolving candidate for rule %s: %s",
                    issue.rule_id,
                    err,
                )

        # Sort descending by start position to avoid offset shifts
        candidates.sort(key=lambda c: c.start, reverse=True)

        current_sql = original_sql
        applied_ids: set[int] = set()
        for cand in candidates:
            current_sql = current_sql[: cand.start] + cand.replacement + current_sql[cand.end :]
            applied_ids.add(id(cand.issue))

        # Return applied issues maintaining the caller's original sequence order
        ordered_applied = [issue for issue in issues if id(issue) in applied_ids]
        return current_sql, ordered_applied
