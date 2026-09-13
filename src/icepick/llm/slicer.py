"""Context slicer module for targeted LLM rewriting.

Extracts localized AST fragments and constructs minimal, high-signal Markdown prompts
for LLM-assisted SQL refactoring, preventing hallucinations and reducing token usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sqlglot import exp

if TYPE_CHECKING:
    from icepick.linter.base import DiagnosticIssue


@dataclass
class SliceContext:
    """Represents the extracted context of a targeted AST node for LLM rewriting.

    Attributes:
        target_sql: Formatted SQL string of the target AST node.
        parent_info: Summary of the parent context (e.g. parent CTE name or query scope).
        referenced_tables: Deduplicated, sorted list of table names referenced in target_node.
        referenced_columns: Deduplicated, sorted list of column names referenced in target_node.
        prompt: Full structured Markdown prompt ready to be sent to an LLM.
    """

    target_sql: str
    parent_info: str
    referenced_tables: list[str]
    referenced_columns: list[str]
    prompt: str


class ContextSlicer:
    """Extracts localized AST contexts and builds targeted LLM rewrite prompts.

    Slices a target AST node along with minimal surrounding context (parent CTE / SELECT,
    referenced tables and columns) to minimize LLM token consumption and prevent hallucinations.
    """

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize the context slicer.

        Args:
            dialect: SQL dialect for rendering formatted queries (default: "snowflake").
        """
        self.dialect = dialect

    def slice_node(
        self,
        root_ast: exp.Expression,
        target_node: exp.Expression,
        issue: DiagnosticIssue | None = None,
    ) -> SliceContext:
        """Extract localized context for a target AST node and generate a rewrite prompt.

        Args:
            root_ast: The root query AST containing target_node.
            target_node: The specific AST node requiring rewriting.
            issue: Optional DiagnosticIssue detailing the detected anti-pattern.

        Returns:
            SliceContext: The extracted context and Markdown prompt.

        Raises:
            TypeError: If root_ast or target_node is not an exp.Expression.
        """
        if not isinstance(root_ast, exp.Expression):
            msg = f"Expected root_ast to be an exp.Expression, got {type(root_ast).__name__}"
            raise TypeError(msg)
        if not isinstance(target_node, exp.Expression):
            msg = f"Expected target_node to be an exp.Expression, got {type(target_node).__name__}"
            raise TypeError(msg)

        target_sql = target_node.sql(dialect=self.dialect, pretty=True)
        parent_info = self._extract_parent_info(root_ast, target_node)
        referenced_tables = self._extract_referenced_tables(target_node)
        referenced_columns = self._extract_referenced_columns(target_node)

        prompt = self._build_prompt(
            target_sql=target_sql,
            parent_info=parent_info,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            issue=issue,
        )

        return SliceContext(
            target_sql=target_sql,
            parent_info=parent_info,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            prompt=prompt,
        )

    def _extract_parent_info(self, root_ast: exp.Expression, target_node: exp.Expression) -> str:
        """Identify the enclosing scope or CTE name of the target node."""
        if target_node is root_ast or target_node.parent is None:
            return "Root query"

        curr: exp.Expr | None = target_node.parent
        while curr is not None:
            if isinstance(curr, exp.CTE):
                cte_name = curr.alias
                return f"CTE: {cte_name}" if cte_name else "CTE"
            curr = curr.parent

        if isinstance(root_ast, exp.Select):
            return "Top-level SELECT"

        key = getattr(root_ast, "key", None)
        statement_name = key.upper() if isinstance(key, str) and key else "Statement"
        return f"Top-level {statement_name}"

    def _extract_referenced_tables(self, target_node: exp.Expression) -> list[str]:
        """Extract deduplicated and sorted table names referenced inside target_node."""
        tables: set[str] = set()
        for tbl in target_node.find_all(exp.Table):
            if tbl.name:
                tables.add(tbl.name)
        return sorted(tables)

    def _extract_referenced_columns(self, target_node: exp.Expression) -> list[str]:
        """Extract deduplicated and sorted column names referenced inside target_node."""
        columns: set[str] = set()
        for col in target_node.find_all(exp.Column):
            if col.name:
                columns.add(col.name)
        return sorted(columns)

    def _build_prompt(
        self,
        target_sql: str,
        parent_info: str,
        referenced_tables: list[str],
        referenced_columns: list[str],
        issue: DiagnosticIssue | None = None,
    ) -> str:
        """Construct a structured Markdown prompt for the LLM."""
        tables_str = ", ".join(referenced_tables) if referenced_tables else "None"
        columns_str = ", ".join(referenced_columns) if referenced_columns else "None"

        if issue is not None:
            severity_val = (
                issue.severity.value if isinstance(issue.severity, Enum) else str(issue.severity)
            )
            issue_section = (
                f"- **Rule ID**: {issue.rule_id} ({issue.rule_name})\n"
                f"- **Severity**: {severity_val}\n"
                f"- **Description**: {issue.description}"
            )
        else:
            issue_section = (
                "No specific static diagnostic rule attached. "
                "Optimize this SQL fragment for Snowflake performance and best practices."
            )

        return (
            "# Role and Task\n"
            "You are an expert Snowflake SQL optimization engineer.\n"
            "Your task is to rewrite the provided target SQL fragment to resolve the diagnosed issue, "
            "optimizing query performance while strictly preserving query semantics.\n\n"
            "## Context\n"
            f"- **Dialect**: Snowflake SQL (`{self.dialect}`)\n"
            f"- **Parent Scope**: {parent_info}\n"
            f"- **Referenced Tables**: {tables_str}\n"
            f"- **Referenced Columns**: {columns_str}\n\n"
            "## Diagnosed Issue\n"
            f"{issue_section}\n\n"
            "## Target SQL Fragment to Rewrite\n"
            "```sql\n"
            f"{target_sql}\n"
            "```\n\n"
            "## Constraints\n"
            "1. Output ONLY the replacement Snowflake SQL fragment wrapped in a single ```sql ... ``` block.\n"
            "2. Do NOT include surrounding query clauses, explanations, or commentary outside or inside the code block.\n"
            "3. Preserve all column aliases, types, and logical semantics.\n"
            "4. Ensure the output SQL is valid Snowflake SQL syntax that can directly replace the target fragment.\n"
        )
