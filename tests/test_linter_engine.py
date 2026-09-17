"""Unit tests for LinterEngine and BaseRule interface."""

from sqlglot import exp

from icepick.config import Config
from icepick.linter import DEFAULT_RULES, BaseRule, DiagnosticIssue, LinterEngine, Severity
from icepick.parser import parse_snowflake_sql


class DummySargableRule(BaseRule):
    """Test dummy rule for Sargable predicates."""

    rule_id = "SNOW-001"
    rule_name = "Non-Sargable Predicate"
    severity = Severity.HIGH
    description = "Function wrapping column in WHERE clause"

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        for where_node in ast.find_all(exp.Where):
            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=self.description,
                    target_node=where_node,
                    snippet=where_node.sql(dialect="snowflake"),
                    line_number=1,
                    suggested_replacement=exp.true(),
                    requires_llm=False,
                )
            )
        return issues


class DummyRedundantSortRule(BaseRule):
    """Test dummy rule for redundant ORDER BY."""

    rule_id = "SNOW-003"
    rule_name = "Redundant Sort"
    severity = Severity.LOW
    description = "Unnecessary ORDER BY in subquery"

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        for order_node in ast.find_all(exp.Order):
            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=self.description,
                    target_node=order_node,
                    snippet=order_node.sql(dialect="snowflake"),
                    line_number=2,
                    suggested_replacement=None,  # Deletable (pop)
                    requires_llm=False,
                )
            )
        return issues


class DummyCorrelatedRule(BaseRule):
    """Test dummy rule for correlated subquery."""

    rule_id = "SNOW-002"
    rule_name = "Correlated Subquery"
    severity = Severity.CRITICAL
    description = "Requires LLM rewriting"

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        for subq in ast.find_all(exp.Subquery):
            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=self.description,
                    target_node=subq,
                    snippet=subq.sql(dialect="snowflake"),
                    line_number=3,
                    suggested_replacement=None,
                    requires_llm=True,
                )
            )
        return issues


def test_diagnostic_issue_properties() -> None:
    """Test helper properties and severity normalization of DiagnosticIssue."""
    node = exp.var("dummy_col")

    # 1. Replaceable issue
    issue_rep = DiagnosticIssue(
        rule_id="SNOW-001",
        rule_name="Test Rule",
        severity="high",  # tests string normalization
        description="desc",
        target_node=node,
        snippet="snippet",
        suggested_replacement=exp.var("new_col"),
        requires_llm=False,
    )
    assert issue_rep.severity == Severity.HIGH
    assert issue_rep.is_replaceable is True
    assert issue_rep.is_deletable is False
    assert issue_rep.can_auto_fix is True

    # 2. Deletable issue (pop)
    issue_del = DiagnosticIssue(
        rule_id="SNOW-003",
        rule_name="Test Sort",
        severity=Severity.LOW,
        description="desc",
        target_node=node,
        snippet="snippet",
        suggested_replacement=None,
        requires_llm=False,
    )
    assert issue_del.is_replaceable is False
    assert issue_del.is_deletable is True
    assert issue_del.can_auto_fix is True

    # 3. LLM required issue
    issue_llm = DiagnosticIssue(
        rule_id="SNOW-002",
        rule_name="Test LLM",
        severity=Severity.CRITICAL,
        description="desc",
        target_node=node,
        snippet="snippet",
        suggested_replacement=None,
        requires_llm=True,
    )
    assert issue_llm.is_replaceable is False
    assert issue_llm.is_deletable is False
    assert issue_llm.can_auto_fix is False

    # 4. Unknown severity string does not raise error
    issue_custom = DiagnosticIssue(
        rule_id="CUSTOM-001",
        rule_name="Custom",
        severity="CUSTOM_SEVERITY",
        description="desc",
        target_node=node,
        snippet="snippet",
    )
    assert issue_custom.severity == "CUSTOM_SEVERITY"


def test_linter_engine_diagnose_aggregation() -> None:
    """Test LinterEngine registers rules and aggregates issues across AST."""
    sql = "SELECT id FROM users WHERE id > 100 ORDER BY created_at"
    ast = parse_snowflake_sql(sql)

    engine = LinterEngine(rules=[DummySargableRule()])
    engine.register_rule(DummyRedundantSortRule())

    assert len(engine.rules) == 2

    issues = engine.diagnose(ast)
    assert len(issues) == 2
    rule_ids = [issue.rule_id for issue in issues]
    assert "SNOW-001" in rule_ids
    assert "SNOW-003" in rule_ids


def test_linter_engine_filtering_disabled_rule() -> None:
    """Test that disabled rules in Config are skipped during diagnosis."""
    sql = "SELECT id FROM users WHERE id > 100 ORDER BY created_at"
    ast = parse_snowflake_sql(sql)

    config = Config(disabled_rules=["SNOW-001"])
    engine = LinterEngine(
        rules=[DummySargableRule(), DummyRedundantSortRule()],
        config=config,
    )

    issues = engine.diagnose(ast)
    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-003"


def test_linter_engine_filtering_enabled_rule() -> None:
    """Test that only whitelisted rules in enabled_rules are executed."""
    sql = "SELECT id FROM (SELECT * FROM users WHERE active = true ORDER BY id) sub"
    ast = parse_snowflake_sql(sql)

    config = Config(enabled_rules=["SNOW-002"])
    engine = LinterEngine(
        rules=[DummySargableRule(), DummyRedundantSortRule(), DummyCorrelatedRule()],
        config=config,
    )

    issues = engine.diagnose(ast)
    assert len(issues) == 1
    assert issues[0].rule_id == "SNOW-002"


def test_linter_engine_clean_query() -> None:
    """Test diagnose returns empty list when query contains no violations."""
    sql = "SELECT 1"
    ast = parse_snowflake_sql(sql)

    engine = LinterEngine(rules=[DummySargableRule(), DummyRedundantSortRule()])
    issues = engine.diagnose(ast)
    assert issues == []


def test_linter_engine_default_rules() -> None:
    """Test LinterEngine initializes with all 11 default rules when none are provided."""
    engine = LinterEngine()
    assert len(engine.rules) == 11
    assert len(DEFAULT_RULES) == 11

    rule_ids = {rule.rule_id for rule in engine.rules}
    expected_ids = {
        "SNOW-001",
        "SNOW-002",
        "SNOW-003",
        "SNOW-004",
        "SNOW-005",
        "SNOW-006",
        "SNOW-007",
        "SNOW-008",
        "SNOW-009",
        "SNOW-010",
        "SNOW-011",
    }
    assert rule_ids == expected_ids

