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


def _find_matching_position(
    orig_lines: Sequence[str],
    old_lines: Sequence[str],
    hint_idx: int,
    search_window: int = 10,
    start_idx: int = 0,
) -> int:
    """Find the best starting index in orig_lines where old_lines match.

    Args:
        orig_lines: Lines of the original text.
        old_lines: Context and deleted lines expected by the hunk.
        hint_idx: Target index based on hunk header old_start.
        search_window: Maximum line distance to search around hint_idx.
        start_idx: Minimum starting index (e.g. end of previous hunk) to prevent rewind.

    Returns:
        int: Matching starting index in orig_lines.

    Raises:
        ValueError: If no match can be found for the hunk lines.
    """
    count = len(old_lines)
    total_orig = len(orig_lines)

    hint_clamped = max(start_idx, min(hint_idx, total_orig))
    if count == 0:
        return hint_clamped

    # 1. Exact match at hint position
    if (
        hint_clamped >= start_idx
        and hint_clamped + count <= total_orig
        and orig_lines[hint_clamped : hint_clamped + count] == list(old_lines)
    ):
        return hint_clamped

    # 2. Search within window around hint position
    min_idx = max(start_idx, hint_clamped - search_window)
    max_idx = min(total_orig - count + 1, hint_clamped + search_window + 1)
    for i in range(min_idx, max_idx):
        if orig_lines[i : i + count] == list(old_lines):
            return i

    # 3. Fallback: Whitespace-stripped match
    old_stripped = [line.rstrip() for line in old_lines]
    for i in range(min_idx, max_idx):
        if [line.rstrip() for line in orig_lines[i : i + count]] == old_stripped:
            return i

    # 4. Global search forward from start_idx if still not found
    for i in range(start_idx, total_orig - count + 1):
        if orig_lines[i : i + count] == list(old_lines):
            return i

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
        old_lines, new_lines = _extract_hunk_lines(hunk)

        hint_idx = max(0, old_start - 1) if old_count > 0 else max(0, old_start)
        match_idx = _find_matching_position(
            orig_lines, old_lines, hint_idx, start_idx=current_orig_idx
        )

        # Copy unchanged original lines preceding this hunk
        if match_idx > current_orig_idx:
            result_lines.extend(orig_lines[current_orig_idx:match_idx])

        should_apply = selected_set is None or idx in selected_set
        if should_apply:
            result_lines.extend(new_lines)
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
