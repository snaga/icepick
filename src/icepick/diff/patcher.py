"""Unified Diff patch application engine for SQL files.

Applies unified diff hunks to original text with hunk selection and newline preservation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from icepick.diff.formatter import DiffHunk, split_hunks

HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    """Parse range values from a unified diff hunk header.

    Args:
        header: Hunk header line (e.g. '@@ -1,5 +1,6 @@').

    Returns:
        tuple[int, int, int, int]: (old_start, old_count, new_start, new_count)
            where omitted counts default to 1.

    Raises:
        ValueError: If header does not match expected hunk header format.
    """
    match = HUNK_HEADER_PATTERN.match(header.strip())
    if not match:
        raise ValueError(f"Invalid hunk header: {header!r}")

    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1

    return old_start, old_count, new_start, new_count


def _extract_hunk_lines(hunk: DiffHunk) -> tuple[list[str], list[str]]:
    """Extract expected old lines and replacement new lines from a diff hunk.

    Args:
        hunk: DiffHunk instance.

    Returns:
        tuple[list[str], list[str]]: (old_lines, new_lines)
    """
    old_lines: list[str] = []
    new_lines: list[str] = []

    for line in hunk.lines:
        if line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith("\\"):
            # e.g., "\\ No newline at end of file"
            continue

    return old_lines, new_lines


def _fuzzy_normalize(line: str) -> str:
    """Normalize whitespace sequences to single spaces and lowercase.

    Why:
        Unified diffs generated from different tools or external linters
        might have slight whitespace differences (e.g. multiple spaces vs tabs)
        or keyword casing differences (e.g. SELECT vs select) while keeping the same
        semantic SQL structure.
    """
    return re.sub(r"\s+", " ", line.strip()).lower()


def _detect_indent_mapping(
    orig_matched: Sequence[str],
    old_lines: Sequence[str],
) -> tuple[str, str, int]:
    """Detect base indentation mapping between original lines and patch lines.

    Finds the first non-empty line pair where indentation differs (e.g. 4 spaces vs 2 spaces),
    or falls back to the first non-empty line pair.

    Args:
        orig_matched: Lines from original text that matched old_lines.
        old_lines: Context and deleted lines expected by the hunk.

    Returns:
        tuple[str, str, int]: (orig_base_ws, old_base_ws, shift)
    """
    first_non_empty: tuple[str, str] | None = None

    for o_line, p_line in zip(orig_matched, old_lines):
        if o_line.strip() and p_line.strip():
            o_m = re.match(r"^[ \t]*", o_line)
            p_m = re.match(r"^[ \t]*", p_line)
            o_ws = o_m.group(0) if o_m else ""
            p_ws = p_m.group(0) if p_m else ""
            if first_non_empty is None:
                first_non_empty = (o_ws, p_ws)
            if o_ws != p_ws:
                # Prefer the line pair that exhibits an indentation difference
                return o_ws, p_ws, len(o_ws) - len(p_ws)

    if first_non_empty is not None:
        o_ws, p_ws = first_non_empty
        return o_ws, p_ws, len(o_ws) - len(p_ws)

    return "", "", 0


def _adjust_added_line(
    line: str,
    orig_base_ws: str,
    old_base_ws: str,
    shift: int,
) -> str:
    """Adjust indentation of an added line to match original file formatting.

    Why:
        External patches or patches generated against normalized SQL may use
        2-space indentation while the user's project uses 4-space indentation or tabs.
        When applying such patches via strip() or fuzzy matching, we must adapt the
        inserted lines so the resulting code maintains the project's native style.

    Args:
        line: Added line content (without diff '+' prefix).
        orig_base_ws: Base leading whitespace of original matching line.
        old_base_ws: Base leading whitespace of patch line.
        shift: Difference in leading spaces count.

    Returns:
        str: Adjusted line content.
    """
    if not line.strip():
        return line

    if old_base_ws and line.startswith(old_base_ws):
        return orig_base_ws + line[len(old_base_ws) :]
    elif shift > 0:
        return (" " * shift) + line
    elif shift < 0:
        leading_len = len(line) - len(line.lstrip(" "))
        remove_len = min(leading_len, -shift)
        return line[remove_len:]
    else:
        return line


def _find_matching_position(
    orig_lines: Sequence[str],
    old_lines: Sequence[str],
    hint_idx: int,
    search_window: int = 10,
    start_idx: int = 0,
) -> tuple[int, str]:
    """Find the best starting index in orig_lines where old_lines match.

    Applies progressive multi-tier fallback matching:
      1. Exact match (hint -> window -> global)
      2. rstrip match (window -> global) - ignores trailing whitespace differences
      3. strip match (window -> global) - ignores indentation differences
      4. Fuzzy token & case match (window -> global) - normalizes whitespace and lowercases

    Args:
        orig_lines: Lines of the original text.
        old_lines: Context and deleted lines expected by the hunk.
        hint_idx: Target index based on hunk header old_start.
        search_window: Maximum line distance to search around hint_idx.
        start_idx: Minimum starting index (e.g. end of previous hunk) to prevent rewind.

    Returns:
        tuple[int, str]: (matching_index, match_mode), where match_mode is one of
            'exact', 'rstrip', 'strip', or 'fuzzy'.

    Raises:
        ValueError: If no match can be found for the hunk lines.
    """
    count = len(old_lines)
    total_orig = len(orig_lines)

    hint_clamped = max(start_idx, min(hint_idx, total_orig))
    if count == 0:
        return hint_clamped, "exact"

    target_exact = list(old_lines)
    target_rstrip = [line.rstrip() for line in old_lines]
    target_strip = [line.strip() for line in old_lines]
    target_fuzzy = [_fuzzy_normalize(line) for line in old_lines]

    min_idx = max(start_idx, hint_clamped - search_window)
    max_idx = min(total_orig - count + 1, hint_clamped + search_window + 1)
    end_global_idx = total_orig - count + 1

    # 1. Exact match
    # 1a. Exact match at hint position
    if (
        hint_clamped >= start_idx
        and hint_clamped + count <= total_orig
        and orig_lines[hint_clamped : hint_clamped + count] == target_exact
    ):
        return hint_clamped, "exact"

    # 1b. Search within window around hint position
    for i in range(min_idx, max_idx):
        if orig_lines[i : i + count] == target_exact:
            return i, "exact"

    # 1c. Global search forward from start_idx
    for i in range(start_idx, end_global_idx):
        if orig_lines[i : i + count] == target_exact:
            return i, "exact"

    # 2. rstrip match (trailing whitespace differences ignored)
    # 2a. Search within window around hint position
    for i in range(min_idx, max_idx):
        if [line.rstrip() for line in orig_lines[i : i + count]] == target_rstrip:
            return i, "rstrip"

    # 2b. Global search forward from start_idx
    for i in range(start_idx, end_global_idx):
        if [line.rstrip() for line in orig_lines[i : i + count]] == target_rstrip:
            return i, "rstrip"

    # 3. strip match (leading and trailing whitespace ignored, indentation-independent)
    # 3a. Search within window around hint position
    for i in range(min_idx, max_idx):
        if [line.strip() for line in orig_lines[i : i + count]] == target_strip:
            return i, "strip"

    # 3b. Global search forward from start_idx
    for i in range(start_idx, end_global_idx):
        if [line.strip() for line in orig_lines[i : i + count]] == target_strip:
            return i, "strip"

    # 4. Fuzzy token & casing match (collapse whitespace sequences and lowercase)
    # 4a. Search within window around hint position
    for i in range(min_idx, max_idx):
        if [_fuzzy_normalize(line) for line in orig_lines[i : i + count]] == target_fuzzy:
            return i, "fuzzy"

    # 4b. Global search forward from start_idx
    for i in range(start_idx, end_global_idx):
        if [_fuzzy_normalize(line) for line in orig_lines[i : i + count]] == target_fuzzy:
            return i, "fuzzy"

    raise ValueError(f"Hunk target lines could not be matched at or near line {hint_idx + 1}.")


def apply_unified_diff(
    original_text: str,
    diff_text: str,
    selected_hunks: Sequence[int] | None = None,
) -> tuple[str, int, int]:
    """Apply unified diff text to original text.

    Args:
        original_text: Original text to patch.
        diff_text: Unified diff containing one or more hunks.
        selected_hunks: Optional sequence of 0-based hunk indices to apply.
            If None, all hunks in the diff are applied.

    Returns:
        tuple[str, int, int]: (patched_text, hunks_applied, hunks_total)

    Raises:
        ValueError: If diff header is malformed or hunk content does not match original text.
    """
    hunks = split_hunks(diff_text)
    total_hunks = len(hunks)
    if total_hunks == 0:
        return original_text, 0, 0

    # Preserve line endings and trailing newline
    newline = "\r\n" if "\r\n" in original_text else "\n"
    has_trailing_newline = original_text.endswith(("\r\n", "\n"))

    orig_lines = original_text.splitlines()
    selected_set = set(selected_hunks) if selected_hunks is not None else None

    # Parse all hunks
    parsed_hunks = []
    for idx, hunk in enumerate(hunks):
        old_start, old_count, new_start, new_count = _parse_hunk_header(hunk.header)
        parsed_hunks.append((idx, hunk, old_start, old_count, new_start, new_count))

    result_lines: list[str] = []
    current_orig_idx = 0
    applied_count = 0

    for idx, hunk, old_start, old_count, _new_start, _new_count in parsed_hunks:
        old_lines, _ = _extract_hunk_lines(hunk)

        hint_idx = max(0, old_start - 1) if old_count > 0 else max(0, old_start)
        match_idx, match_mode = _find_matching_position(
            orig_lines, old_lines, hint_idx, start_idx=current_orig_idx
        )

        # Copy unchanged original lines preceding this hunk
        if match_idx > current_orig_idx:
            result_lines.extend(orig_lines[current_orig_idx:match_idx])

        should_apply = selected_set is None or idx in selected_set
        if should_apply:
            orig_matched = orig_lines[match_idx : match_idx + len(old_lines)]
            orig_base_ws, old_base_ws, shift = _detect_indent_mapping(orig_matched, old_lines)

            orig_ptr = match_idx
            for line in hunk.lines:
                if line.startswith(" "):
                    # Preserve exact context line from original file (casing, comments, indent)
                    if orig_ptr < len(orig_lines):
                        result_lines.append(orig_lines[orig_ptr])
                        orig_ptr += 1
                elif line.startswith("-"):
                    # Consume deleted line from original
                    orig_ptr += 1
                elif line.startswith("+"):
                    added_content = line[1:]
                    if match_mode in ("strip", "fuzzy"):
                        added_content = _adjust_added_line(
                            added_content, orig_base_ws, old_base_ws, shift
                        )
                    result_lines.append(added_content)
                elif line.startswith("\\"):
                    # e.g., \ No newline at end of file
                    continue

            applied_count += 1
        else:
            result_lines.extend(orig_lines[match_idx : match_idx + len(old_lines)])

        current_orig_idx = match_idx + len(old_lines)

    # Append remaining original lines
    if current_orig_idx < len(orig_lines):
        result_lines.extend(orig_lines[current_orig_idx:])

    patched_text = newline.join(result_lines)
    if has_trailing_newline or (not original_text and result_lines):
        patched_text += newline
    elif not result_lines and not has_trailing_newline:
        patched_text = ""

    return patched_text, applied_count, total_hunks
