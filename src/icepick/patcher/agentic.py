"""Agentic AST patcher module leveraging ContextSlicer and LLMClient.

Enables targeted, LLM-assisted SQL refactoring for complex anti-patterns
(e.g., correlated subqueries, implicit cross joins, duplicate table scans)
with guaranteed in-place AST replacement and safe fallback on failure.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlglot import exp

from icepick.llm.client import LLMClient
from icepick.llm.slicer import ContextSlicer

if TYPE_CHECKING:
    from icepick.config import Config
    from icepick.linter.base import DiagnosticIssue

logger = logging.getLogger(__name__)


class AgenticPatcher:
    """Performs targeted LLM-assisted AST transformations based on diagnostic issues.

    Uses ContextSlicer to extract minimal AST subtrees and context prompts, invokes
    LLMClient for syntax-validated rewritten AST nodes, and safely replaces target
    nodes in-place. Optionally verifies rewrites using closed-loop Snowflake bidirectional
    EXCEPT checks with self-correcting feedback and safe fallback.
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
        verifier: Any = None,
        orig_sql: str | None = None,
        config: Config | None = None,
        max_retries: int = 3,
    ) -> tuple[exp.Expression, bool]:
        """Apply an LLM-assisted rewrite for a single diagnostic issue in-place.

        If a verifier is provided, executes a self-correcting verification loop using
        Snowflake bidirectional EXCEPT queries. If differences are detected, verification
        metrics are fed back into the context prompt to retry up to max_retries times.

        Args:
            ast: Root AST Expression.
            issue: DiagnosticIssue requiring LLM rewriting.
            verifier: Optional EquivalenceVerifier for closed-loop verification.
            orig_sql: Optional original SQL string for verification. Derived from ast if None.
            config: Optional Config instance for Snowflake connection settings.
            max_retries: Maximum number of retry attempts for the verification loop (default: 3).

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

        # Standard execution path without verifier loop
        if verifier is None:
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

        # --- Self-Correction Verification Loop ---
        base_orig_sql = orig_sql or ast.sql(dialect=self.dialect, pretty=True)

        try:
            slice_ctx = self.slicer.slice_node(ast, target_node, issue=issue)

            for attempt in range(max(0, max_retries) + 1):
                replacement = self.llm_client.rewrite_fragment(slice_ctx, dialect=self.dialect)

                if replacement is None:
                    logger.warning(
                        "LLM rewrite returned no valid replacement on attempt %d for issue %s.",
                        attempt + 1,
                        issue.rule_id,
                    )
                    if attempt < max_retries:
                        slice_ctx.prompt += (
                            "\n\nPrevious rewrite produced invalid SQL or could not be parsed. "
                            "Please ensure valid syntax and exact semantic equivalence."
                        )
                        continue
                    return ast, False

                # Apply candidate in-place
                is_root = target_node is ast or target_node.parent is None
                rolled_back = False
                res = None
                if is_root:
                    candidate_opt_sql = replacement.sql(dialect=self.dialect, pretty=True)
                else:
                    target_node.replace(replacement)
                    candidate_opt_sql = ast.sql(dialect=self.dialect, pretty=True)

                try:
                    # Verify with Snowflake bidirectional EXCEPT
                    res = verifier.verify_with_snowflake(
                        base_orig_sql, candidate_opt_sql, config=config
                    )

                    if res.is_equivalent is True:
                        logger.info(
                            "Verification passed for issue %s (attempt %d). Rewrite accepted.",
                            issue.rule_id,
                            attempt + 1,
                        )
                        return (replacement if is_root else ast), True

                    # Equivalence failed: rollback candidate replacement if not root
                    if not is_root:
                        replacement.replace(target_node)
                        rolled_back = True
                finally:
                    # Fail-safe guard: ensure rollback if exception occurred or not equivalent
                    if not is_root and not rolled_back and (res is None or not res.is_equivalent):
                        replacement.replace(target_node)

                if res.error_message:
                    feedback_msg = (
                        f"Previous rewrite caused an execution error: {res.error_message}. "
                        f"Please fix syntax and ensure valid Snowflake SQL."
                    )
                else:
                    feedback_msg = (
                        f"Previous rewrite produced differing results: "
                        f"{res.orig_not_in_opt_count} missing rows, {res.opt_not_in_orig_count} extra rows. "
                        f"Please ensure exact semantic equivalence."
                    )
                logger.warning(
                    "Verification difference detected for issue %s (attempt %d/%d): %s",
                    issue.rule_id,
                    attempt + 1,
                    max_retries + 1,
                    feedback_msg,
                )

                if attempt < max_retries:
                    slice_ctx.prompt += f"\n\n{feedback_msg}"
                else:
                    logger.warning(
                        "All %d verification attempts exhausted for issue %s. Keeping original AST.",
                        max_retries + 1,
                        issue.rule_id,
                    )
                    return ast, False

            return ast, False

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Fail-Safe: Exception during verification loop for issue %s: %s",
                issue.rule_id,
                exc,
            )
            return ast, False

    def apply_issue_with_verify_loop(
        self,
        ast: exp.Expression,
        issue: DiagnosticIssue,
        verifier: Any,
        orig_sql: str,
        config: Config | None = None,
        max_retries: int = 3,
    ) -> tuple[exp.Expression, bool]:
        """Apply an LLM-assisted rewrite with closed-loop Snowflake equivalence verification.

        Args:
            ast: Root AST Expression.
            issue: DiagnosticIssue requiring LLM rewriting.
            verifier: Verifier object for bidirectional EXCEPT verification.
            orig_sql: Original SQL query text.
            config: Optional Config instance for Snowflake connection.
            max_retries: Maximum retry attempts for self-correction (default: 3).

        Returns:
            tuple[exp.Expression, bool]: Updated AST and boolean success status.
        """
        return self.apply_issue(
            ast=ast,
            issue=issue,
            verifier=verifier,
            orig_sql=orig_sql,
            config=config,
            max_retries=max_retries,
        )

    def apply_all(
        self,
        ast: exp.Expression,
        issues: Sequence[DiagnosticIssue],
        verifier: Any = None,
        orig_sql: str | None = None,
        config: Config | None = None,
        max_retries: int = 3,
    ) -> tuple[exp.Expression, list[DiagnosticIssue]]:
        """Sequentially apply LLM-assisted rewrites for all applicable issues in-place.

        Args:
            ast: Root AST Expression.
            issues: Sequence of DiagnosticIssues.
            verifier: Optional EquivalenceVerifier for closed-loop verification.
            orig_sql: Optional original SQL query string for verification.
            config: Optional Config instance for Snowflake connection.
            max_retries: Maximum retry attempts for self-correction (default: 3).

        Returns:
            tuple[exp.Expression, list[DiagnosticIssue]]:
                A tuple containing the updated root AST and a list of DiagnosticIssues
                that were successfully rewritten and replaced.
        """
        applied: list[DiagnosticIssue] = []
        current_ast = ast
        current_orig_sql = orig_sql or current_ast.sql(dialect=self.dialect, pretty=True)

        for issue in issues:
            if not issue.requires_llm or issue.target_node is None:
                continue

            current_ast, success = self.apply_issue(
                current_ast,
                issue,
                verifier=verifier,
                orig_sql=current_orig_sql,
                config=config,
                max_retries=max_retries,
            )
            if success:
                applied.append(issue)

        return current_ast, applied
