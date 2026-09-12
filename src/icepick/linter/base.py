"""Base definitions and data structures for the AST linter engine.

Defines the DiagnosticIssue representation, Severity levels, and the BaseRule
abstract contract for deterministic static diagnosis rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlglot import exp


class Severity(str, Enum):
    """Severity levels for diagnostic issues."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DiagnosticIssue:
    """Represents a single detected SQL anti-pattern or optimization opportunity.

    Attributes:
        rule_id: Unique rule identifier (e.g. 'SNOW-001').
        rule_name: Human-readable name of the rule.
        severity: Severity level (INFO, LOW, MEDIUM, HIGH, CRITICAL).
        description: Detailed explanation of the diagnosed issue and its performance impact.
        target_node: The offending AST expression node to inspect or replace.
        snippet: SQL code snippet representing the target node.
        line_number: 1-indexed line number where the issue was detected, if available.
        suggested_replacement: Optional proposed replacement AST node.
            If None and requires_llm is False, indicates recommendation to remove (pop) target_node.
        requires_llm: True if resolving this issue requires LLM rewriting.
    """

    rule_id: str
    rule_name: str
    severity: Severity | str
    description: str
    target_node: exp.Expression
    snippet: str
    line_number: int | None = None
    suggested_replacement: exp.Expression | None = None
    requires_llm: bool = False

    def __post_init__(self) -> None:
        """Normalize severity string to Severity enum when possible."""
        if isinstance(self.severity, str):
            try:
                self.severity = Severity(self.severity.upper())
            except ValueError:
                pass

    @property
    def is_replaceable(self) -> bool:
        """Whether this issue provides an automated replacement AST node."""
        return self.suggested_replacement is not None

    @property
    def is_deletable(self) -> bool:
        """Whether this issue recommends node removal (pop) without LLM intervention."""
        return self.suggested_replacement is None and not self.requires_llm

    @property
    def can_auto_fix(self) -> bool:
        """Whether this issue can be fixed deterministically without LLM assistance."""
        return not self.requires_llm

    def to_dict(self) -> dict[str, Any]:
        """Convert DiagnosticIssue to a dictionary representation.

        Returns:
            dict[str, Any]: Serialized dictionary representation of the issue.
        """
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": (
                self.severity.value if isinstance(self.severity, Severity) else str(self.severity)
            ),
            "line": self.line_number,
            "description": self.description,
            "snippet": self.snippet,
            "can_auto_fix": self.can_auto_fix,
        }


class BaseRule(ABC):
    """Abstract base class for all deterministic AST diagnostic rules.

    Attributes:
        rule_id: Unique rule identifier (e.g. 'SNOW-001').
        rule_name: Human-readable rule title.
        severity: Default severity level.
        description: Summary of the anti-pattern detected by this rule.
    """

    rule_id: str
    rule_name: str
    severity: Severity | str
    description: str

    @abstractmethod
    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Analyze an AST and detect occurrences of this rule's anti-pattern.

        Args:
            ast: The root AST expression to diagnose.

        Returns:
            list[DiagnosticIssue]: List of detected issues, or empty list if none found.
        """
