"""Agentic AST patcher module leveraging ContextSlicer and LLMClient.

Enables targeted, LLM-assisted SQL refactoring for complex anti-patterns
(e.g., correlated subqueries, implicit cross joins, duplicate table scans)
with guaranteed in-place AST replacement and safe fallback on failure.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlglot import exp

from icepick.llm.client import LLMClient
from icepick.llm.slicer import ContextSlicer

if TYPE_CHECKING:
    from icepick.linter.base import DiagnosticIssue

logger = logging.getLogger(__name__)


class AgenticPatcher:
    """Performs targeted LLM-assisted AST transformations based on diagnostic issues.

    Uses ContextSlicer to extract minimal AST subtrees and context prompts, invokes
    LLMClient for syntax-validated rewritten AST nodes, and safely replaces target
    nodes in-place. Falls back safely without mutating the AST on any failure.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        slicer: ContextSlicer | None = None,
        dialect: str = "snowflake",
    ) -> None:
        """Initialize the AgenticPatcher.

        Args:
            llm_client: Optional LLMClient instance. Lazily initialized if None.
            slicer: Optional ContextSlicer instance. Initialized with dialect if None.
            dialect: SQL dialect for slicing and AST parsing (default: "snowflake").
        """
        self.dialect = dialect
        self._llm_client = llm_client
        self._slicer = slicer

    @property
    def llm_client(self) -> LLMClient:
        """Return the LLMClient instance, creating a default one lazily if not provided."""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client

    @property
    def slicer(self) -> ContextSlicer:
        """Return the ContextSlicer instance, creating a default one lazily if not provided."""
        if self._slicer is None:
            self._slicer = ContextSlicer(dialect=self.dialect)
        return self._slicer

    def apply_issue(
        self,
        ast: exp.Expression,
        issue: DiagnosticIssue,
    ) -> tuple[exp.Expression, bool]:
        """Apply an LLM-assisted rewrite for a single diagnostic issue in-place.

        Args:
            ast: Root AST Expression.
            issue: DiagnosticIssue requiring LLM rewriting.

        Returns:
            tuple[exp.Expression, bool]:
                A tuple containing the updated root AST and a boolean indicating
                whether the rewrite and replacement succeeded. If failed, returns
                (ast, False) without mutating the tree (Fail-Safe).
        """
        if issue.target_node is None or not isinstance(issue.target_node, exp.Expression):
            logger.warning(
                "Issue %s cannot be patched: target_node is missing or invalid.",
                issue.rule_id,
            )
            return ast, False

        target_node = issue.target_node

        try:
            slice_ctx = self.slicer.slice_node(ast, target_node, issue=issue)
            replacement = self.llm_client.rewrite_fragment(slice_ctx, dialect=self.dialect)

            if replacement is None:
                logger.warning(
                    "LLM rewrite returned no valid replacement for issue %s. Keeping original AST.",
                    issue.rule_id,
                )
                return ast, False

            # In-place replacement: if target is root, return replacement directly.
            # Otherwise mutate parent in-place preserving surrounding clauses.
            if target_node is ast or target_node.parent is None:
                return replacement, True

            target_node.replace(replacement)
            return ast, True

        except Exception as exc:  # noqa: BLE001
            # Fail-Safe: Log and preserve original AST on any unexpected error
            logger.warning(
                "Fail-Safe: Exception occurred while applying LLM rewrite for issue %s: %s",
                issue.rule_id,
                exc,
            )
            return ast, False

    def apply_all(
        self,
        ast: exp.Expression,
        issues: Sequence[DiagnosticIssue],
    ) -> tuple[exp.Expression, list[DiagnosticIssue]]:
        """Sequentially apply LLM-assisted rewrites for all applicable issues in-place.

        Args:
            ast: Root AST Expression.
            issues: Sequence of DiagnosticIssues.

        Returns:
            tuple[exp.Expression, list[DiagnosticIssue]]:
                A tuple containing the updated root AST and a list of DiagnosticIssues
                that were successfully rewritten and replaced.
        """
        applied: list[DiagnosticIssue] = []
        current_ast = ast

        for issue in issues:
            if not issue.requires_llm or issue.target_node is None:
                continue

            current_ast, success = self.apply_issue(current_ast, issue)
            if success:
                applied.append(issue)

        return current_ast, applied
