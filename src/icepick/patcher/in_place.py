"""AST In-place Patcher module.

Provides surgical in-place AST node replacement and node removal (pop)
based on DiagnosticIssue recommendations, guaranteeing that untouched AST
structures and overall query semantics are strictly preserved.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlglot import exp

from icepick.linter.base import DiagnosticIssue

logger = logging.getLogger(__name__)


class ASTPatcher:
    """Performs in-place AST transformations based on diagnostic issues.

    Attributes:
        dialect: SQL dialect name used for verification (default: "snowflake").
    """

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize ASTPatcher.

        Args:
            dialect: SQL dialect name (default: "snowflake").
        """
        self.dialect = dialect

    def apply_issue(self, ast: exp.Expression, issue: DiagnosticIssue) -> exp.Expression:
        """Apply a single diagnostic issue's fix to the AST in-place.

        Args:
            ast: Root AST Expression.
            issue: DiagnosticIssue containing the target node and replacement suggestion.

        Returns:
            exp.Expression: Updated root AST Expression.

        Raises:
            ValueError: If the issue cannot be automatically fixed or is invalid.
        """
        if not issue.can_auto_fix:
            raise ValueError(
                f"Issue '{issue.rule_id}' cannot be automatically fixed (requires LLM or manual action)."
            )

        if issue.is_replaceable:
            assert issue.suggested_replacement is not None
            replacement = issue.suggested_replacement.copy()
            if issue.target_node is ast or issue.target_node.parent is None:
                return replacement

            issue.target_node.replace(replacement)
            return ast

        if issue.is_deletable:
            if issue.target_node is ast:
                raise ValueError("Cannot delete the root query AST node.")

            issue.target_node.pop()
            return ast

        raise ValueError(f"Issue '{issue.rule_id}' has no actionable fix.")

    def apply_all(
        self,
        ast: exp.Expression,
        issues: Sequence[DiagnosticIssue],
    ) -> tuple[exp.Expression, list[DiagnosticIssue]]:
        """Sequentially apply all auto-fixable issues to the AST in-place.

        Safely ignores non-auto-fixable issues or issues that encounter unexpected
        structural exceptions during in-place modification (Fail-Safe).

        Args:
            ast: Root AST Expression.
            issues: Sequence of DiagnosticIssues to apply.

        Returns:
            tuple[exp.Expression, list[DiagnosticIssue]]:
                A tuple containing the modified root AST and the list of successfully
                applied issues.
        """
        applied: list[DiagnosticIssue] = []
        current_ast = ast

        for issue in issues:
            if not issue.can_auto_fix or not (issue.is_replaceable or issue.is_deletable):
                continue

            try:
                current_ast = self.apply_issue(current_ast, issue)
                applied.append(issue)
            except (ValueError, TypeError, KeyError, AttributeError) as err:
                # Fail-safe: Skip corrupted/un-patchable node and preserve AST
                logger.debug("Failed to apply issue %s in-place: %s", issue.rule_id, err)
                continue

        return current_ast, applied
