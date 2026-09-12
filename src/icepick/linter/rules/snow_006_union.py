"""SNOW-006: UNION to UNION ALL Rule.

Detects ``UNION`` (with implicit ``DISTINCT``) and recommends rewriting it
as ``UNION ALL`` when duplicate elimination is not semantically required.
Snowflake evaluates ``UNION`` by sorting or hashing all rows to remove
duplicates, which is significantly more expensive than ``UNION ALL``.

References:
    Snowflake best-practice: prefer UNION ALL unless deduplication is required.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


class UnionToUnionAllRule(BaseRule):
    """Rule SNOW-006: Detects UNION (distinct) and suggests UNION ALL instead.

    ``UNION`` internally deduplicates rows via a sort or hash-aggregate pass.
    If the caller does not actually require duplicate elimination, replacing
    ``UNION`` with ``UNION ALL`` avoids that overhead entirely.
    """

    rule_id: str = "SNOW-006"
    rule_name: str = "UNION to UNION ALL"
    severity: Severity = Severity.LOW
    description: str = (
        "UNION deduplicates rows via an implicit DISTINCT, incurring sort/hash "
        "overhead. Prefer UNION ALL when duplicate removal is not required."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse AST for UNION (distinct) nodes and emit optimization hints.

        For each ``exp.Union`` node encountered, the ``distinct`` arg is
        inspected.  Only nodes where ``distinct is True`` represent a
        ``UNION`` with deduplication; ``UNION ALL`` nodes have ``distinct``
        set to ``False`` and are skipped.

        The ``suggested_replacement`` is a deep-copy of the offending node
        with ``distinct`` flipped to ``False``, enabling ``ASTPatcher`` to
        perform an automated in-place rewrite.

        Args:
            ast: Root or subtree AST expression to diagnose.

        Returns:
            list[DiagnosticIssue]: One issue per detected ``UNION``
            (distinct-mode) node, or an empty list if none found.
        """
        issues: list[DiagnosticIssue] = []

        for union_node in ast.find_all(exp.Union):
            # distinct=True  →  UNION   (deduplicate — expensive)
            # distinct=False →  UNION ALL (pass-through — cheap)
            # We treat absent/None as non-distinct (safe fallback).
            if union_node.args.get("distinct") is not True:
                continue

            # Build a replacement node: same structure, but distinct=False.
            # Also reset distinct on any nested Union nodes to ensure chained
            # UNION queries are fully converted to UNION ALL without retaining
            # distinct=True in any child union subtrees.
            replacement = union_node.copy()
            replacement.set("distinct", False)
            for child_union in replacement.find_all(exp.Union):
                child_union.set("distinct", False)

            line_num: int | None = None
            meta_dict: Any = getattr(union_node, "meta", None)
            if isinstance(meta_dict, dict):
                line_num = meta_dict.get("line")

            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=(
                        "UNION with implicit DISTINCT detected. Replacing with UNION ALL "
                        "eliminates the sort/hash-deduplication pass and can significantly "
                        "reduce query execution time and memory pressure."
                    ),
                    target_node=union_node,
                    snippet=union_node.sql(dialect="snowflake"),
                    line_number=line_num,
                    suggested_replacement=replacement,
                    requires_llm=False,
                )
            )

        return issues
