"""Linter execution engine for Icepick.

Coordinates rule registration, configuration-based rule filtering,
and aggregation of diagnostic issues across an AST.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlglot import exp

from icepick.config import Config
from icepick.linter.base import BaseRule, DiagnosticIssue


class LinterEngine:
    """Orchestrates AST lint rules against Snowflake SQL queries.

    Attributes:
        rules: List of registered BaseRule implementations.
        config: Icepick configuration controlling rule enablement and options.
    """

    def __init__(
        self,
        rules: Sequence[BaseRule] | None = None,
        config: Config | None = None,
    ) -> None:
        """Initialize LinterEngine with rules and optional configuration.

        Args:
            rules: Initial sequence of rules to register.
            config: Icepick Config instance (defaults to default Config()).
        """
        self.rules: list[BaseRule] = list(rules) if rules is not None else []
        self.config: Config = config or Config()

    def register_rule(self, rule: BaseRule) -> None:
        """Register a new diagnostic rule.

        Args:
            rule: BaseRule instance to add to the engine.
        """
        self.rules.append(rule)

    def diagnose(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Run all enabled diagnostic rules on the provided AST.

        Evaluates only rules permitted by self.config.is_rule_enabled().

        Args:
            ast: Root AST Expression to analyze.

        Returns:
            list[DiagnosticIssue]: Consolidated list of all detected issues.
        """
        issues: list[DiagnosticIssue] = []
        for rule in self.rules:
            if self.config.is_rule_enabled(rule.rule_id):
                detected = rule.check(ast)
                issues.extend(detected)
        return issues
