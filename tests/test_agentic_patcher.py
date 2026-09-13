"""Unit tests for AgenticPatcher module (London school / mock verification)."""

from unittest.mock import MagicMock

import sqlglot

from icepick.linter.base import DiagnosticIssue, Severity
from icepick.linter.rules.snow_002_correlated import CorrelatedSubqueryRule
from icepick.llm.client import LLMClient
from icepick.llm.slicer import ContextSlicer, SliceContext
from icepick.parser import parse_snowflake_sql
from icepick.patcher.agentic import AgenticPatcher
from icepick.verifier.equivalence import EquivalenceVerifier, VerificationResult


def test_agentic_patcher_init_defaults() -> None:
    """Test default initialization of AgenticPatcher and lazy property loading."""
    patcher = AgenticPatcher()
    assert patcher.dialect == "snowflake"
    assert isinstance(patcher.llm_client, LLMClient)
    assert isinstance(patcher.slicer, ContextSlicer)
    assert patcher.slicer.dialect == "snowflake"


def test_agentic_patcher_init_custom() -> None:
    """Test custom dependency injection in AgenticPatcher."""
    mock_client = MagicMock(spec=LLMClient)
    mock_slicer = MagicMock(spec=ContextSlicer)
    patcher = AgenticPatcher(llm_client=mock_client, slicer=mock_slicer, dialect="postgres")

    assert patcher.dialect == "postgres"
    assert patcher.llm_client is mock_client
    assert patcher.slicer is mock_slicer


def test_apply_issue_correlated_subquery_success() -> None:
    """Test successful in-place replacement of a correlated subquery (SNOW-002)."""
    sql = """
    SELECT p.id, p.name
    FROM parent p
    WHERE EXISTS (
        SELECT 1
        FROM child c
        WHERE c.parent_id = p.id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.requires_llm is True

    # Replacement node: e.g. replacing EXISTS (...) with a dummy expression or join condition
    replacement_expr = sqlglot.parse_one("p.has_active_child = TRUE", read="snowflake")

    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.return_value = replacement_expr

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is True
    assert updated_ast is ast
    # Verify rewrite_fragment was called with SliceContext
    mock_llm_client.rewrite_fragment.assert_called_once()
    slice_arg = mock_llm_client.rewrite_fragment.call_args[0][0]
    assert isinstance(slice_arg, SliceContext)
    assert "EXISTS" in slice_arg.target_sql
    assert "parent" in slice_arg.referenced_tables or "child" in slice_arg.referenced_tables

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "p.has_active_child = TRUE" in updated_sql
    assert "EXISTS" not in updated_sql
    assert "SELECT p.id, p.name FROM parent AS p" in updated_sql


def test_apply_issue_root_node_replacement() -> None:
    """Test replacing the root AST node directly when target_node has no parent."""
    sql = "SELECT 1"
    ast = parse_snowflake_sql(sql)

    issue = DiagnosticIssue(
        rule_id="SNOW-TEST",
        rule_name="Root Replace Test",
        severity=Severity.HIGH,
        description="Replace root expression",
        target_node=ast,
        snippet="SELECT 1",
        requires_llm=True,
    )

    replacement = parse_snowflake_sql("SELECT 2")
    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.return_value = replacement

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is True
    assert updated_ast is replacement
    assert updated_ast.sql(dialect="snowflake") == "SELECT 2"


def test_apply_issue_none_replacement_failsafe() -> None:
    """Test fail-safe behavior when LLM returns None (syntax error or refusal)."""
    sql = """
    SELECT *
    FROM orders o
    WHERE EXISTS (
        SELECT 1 FROM items i WHERE i.order_id = o.id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    issue = issues[0]

    orig_sql = ast.sql(dialect="snowflake")

    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.return_value = None

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is False
    assert updated_ast is ast
    # Ensure AST was untouched
    assert updated_ast.sql(dialect="snowflake") == orig_sql


def test_apply_issue_exception_failsafe() -> None:
    """Test fail-safe fallback when LLMClient or slicer raises an unexpected exception."""
    sql = "SELECT * FROM parent p WHERE EXISTS (SELECT 1 FROM child c WHERE c.pid = p.id)"
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    issue = issues[0]

    orig_sql = ast.sql(dialect="snowflake")

    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.side_effect = RuntimeError("Network timeout to LLM provider")

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is False
    assert updated_ast is ast
    assert updated_ast.sql(dialect="snowflake") == orig_sql


def test_apply_issue_invalid_target_node() -> None:
    """Test handling of issue with missing or invalid target_node."""
    sql = "SELECT 1"
    ast = parse_snowflake_sql(sql)

    issue = DiagnosticIssue(
        rule_id="SNOW-TEST",
        rule_name="Invalid Node Test",
        severity=Severity.LOW,
        description="No target node",
        target_node=None,  # type: ignore[arg-type]
        snippet="",
        requires_llm=True,
    )

    mock_llm_client = MagicMock(spec=LLMClient)
    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is False
    assert updated_ast is ast
    mock_llm_client.rewrite_fragment.assert_not_called()


def test_apply_all_mixed_issues() -> None:
    """Test apply_all with a mix of LLM-required, non-LLM, invalid, and failed issues."""
    sql = """
    SELECT p.id
    FROM parent p
    WHERE EXISTS (
        SELECT 1 FROM child c WHERE c.parent_id = p.id
    )
    AND EXISTS (
        SELECT 1 FROM logs l WHERE l.parent_id = p.id
    )
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    assert len(issues) == 2

    # Issue 1: Valid LLM issue (success)
    issue1 = issues[0]

    # Issue 2: Valid LLM issue (failure)
    issue2 = issues[1]

    # Issue 3: Non-LLM issue (should be skipped)
    issue3 = DiagnosticIssue(
        rule_id="SNOW-001",
        rule_name="Non-Sargable",
        severity=Severity.MEDIUM,
        description="Non-LLM rule",
        target_node=ast,
        snippet="SELECT p.id",
        requires_llm=False,
    )

    # Issue 4: requires_llm=True but target_node is None
    issue4 = DiagnosticIssue(
        rule_id="SNOW-002",
        rule_name="Correlated Subquery",
        severity=Severity.CRITICAL,
        description="Missing node",
        target_node=None,  # type: ignore[arg-type]
        snippet="",
        requires_llm=True,
    )

    all_issues = [issue1, issue3, issue4, issue2]

    # Mock LLM to succeed on issue1 and fail (None) on issue2
    repl1 = sqlglot.parse_one("p.child_status = 'VALID'", read="snowflake")
    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.side_effect = [repl1, None]

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, applied = patcher.apply_all(ast, all_issues)

    # Only issue1 should be successfully applied
    assert len(applied) == 1
    assert applied[0] is issue1
    assert "p.child_status = 'VALID'" in updated_ast.sql(dialect="snowflake")


def test_slicing_integration_with_real_slicer_and_mock_generate_text() -> None:
    """Test end-to-end slice and rewrite using real ContextSlicer and mocked LLM API call."""
    sql = """
    WITH sales AS (
        SELECT p.id, p.amount
        FROM purchases p
        WHERE EXISTS (
            SELECT 1
            FROM refunds r
            WHERE r.purchase_id = p.id
        )
    )
    SELECT * FROM sales
    """
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    assert len(issues) == 1
    issue = issues[0]

    # Use a real LLMClient with mocked generate_text
    llm_client = LLMClient(api_key="fake-key")
    llm_response_text = """
    Here is the optimized SQL:
    ```sql
    p.has_refund = TRUE
    ```
    """
    llm_client.generate_text = MagicMock(return_value=llm_response_text)  # type: ignore[method-assign]

    patcher = AgenticPatcher(llm_client=llm_client)
    updated_ast, success = patcher.apply_issue(ast, issue)

    assert success is True
    # Verify the prompt generated by ContextSlicer contains relevant scope and rules
    call_args = llm_client.generate_text.call_args[0]
    prompt_sent = call_args[0]
    assert "CTE: sales" in prompt_sent
    assert "SNOW-002" in prompt_sent
    assert "refunds" in prompt_sent

    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "p.has_refund = TRUE" in updated_sql
    assert "EXISTS" not in updated_sql


def test_agentic_patcher_verify_loop_success_after_retry() -> None:
    """Test verify loop self-correction succeeds on second attempt after feedback."""
    sql = (
        "SELECT p.id, p.name FROM parent p "
        "WHERE EXISTS (SELECT 1 FROM child c WHERE c.parent_id = p.id)"
    )
    ast = parse_snowflake_sql(sql)
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    assert len(issues) == 1
    issue = issues[0]

    # Attempt 1 returns imperfect rewrite; Attempt 2 returns valid rewrite
    repl1 = sqlglot.parse_one("p.has_child = 1", read="snowflake")
    repl2 = sqlglot.parse_one("p.has_child = TRUE", read="snowflake")

    mock_llm_client = MagicMock(spec=LLMClient)
    mock_llm_client.rewrite_fragment.side_effect = [repl1, repl2]

    # Mock verifier: first attempt fails (difference), second attempt passes (equivalent)
    mock_verifier = MagicMock(spec=EquivalenceVerifier)
    mock_verifier.verify_with_snowflake.side_effect = [
        VerificationResult(
            is_equivalent=False,
            orig_not_in_opt_count=2,
            opt_not_in_orig_count=1,
            verification_sql="EXCEPT SQL 1",
        ),
        VerificationResult(
            is_equivalent=True,
            orig_not_in_opt_count=0,
            opt_not_in_orig_count=0,
            verification_sql="EXCEPT SQL 2",
        ),
    ]

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    updated_ast, success = patcher.apply_issue(
        ast,
        issue,
        verifier=mock_verifier,
        max_retries=3,
    )

    assert success is True
    assert mock_llm_client.rewrite_fragment.call_count == 2
    assert mock_verifier.verify_with_snowflake.call_count == 2

    # Second LLM call must include feedback from the first failed attempt
    second_slice = mock_llm_client.rewrite_fragment.call_args_list[1][0][0]
    expected_feedback = (
        "Previous rewrite produced differing results: 2 missing rows, 1 extra rows. "
        "Please ensure exact semantic equivalence."
    )
    assert expected_feedback in second_slice.prompt

    # Verify replacement 2 was accepted
    updated_sql = updated_ast.sql(dialect="snowflake")
    assert "p.has_child = TRUE" in updated_sql
    assert "EXISTS" not in updated_sql


def test_agentic_patcher_verify_loop_exhausted_retries_falls_back() -> None:
    """Test verify loop safely falls back to original AST when all retries fail."""
    sql = (
        "SELECT p.id, p.name FROM parent p "
        "WHERE EXISTS (SELECT 1 FROM child c WHERE c.parent_id = p.id)"
    )
    ast = parse_snowflake_sql(sql)
    orig_sql = ast.sql(dialect="snowflake")
    rule = CorrelatedSubqueryRule()
    issues = rule.check(ast)
    issue = issues[0]

    mock_llm_client = MagicMock(spec=LLMClient)
    # Always return candidate expressions
    mock_llm_client.rewrite_fragment.side_effect = [
        sqlglot.parse_one("p.bad_col_1 = TRUE", read="snowflake"),
        sqlglot.parse_one("p.bad_col_2 = TRUE", read="snowflake"),
        sqlglot.parse_one("p.bad_col_3 = TRUE", read="snowflake"),
    ]

    # Mock verifier: all attempts fail verification
    mock_verifier = MagicMock(spec=EquivalenceVerifier)
    mock_verifier.verify_with_snowflake.return_value = VerificationResult(
        is_equivalent=False,
        orig_not_in_opt_count=5,
        opt_not_in_orig_count=3,
        verification_sql="EXCEPT SQL",
    )

    patcher = AgenticPatcher(llm_client=mock_llm_client)
    # max_retries=2 -> 1 initial attempt + 2 retries = 3 total attempts
    updated_ast, success = patcher.apply_issue(
        ast,
        issue,
        verifier=mock_verifier,
        max_retries=2,
    )

    assert success is False
    assert mock_llm_client.rewrite_fragment.call_count == 3
    assert mock_verifier.verify_with_snowflake.call_count == 3

    # Fail-Safe: Original AST must be preserved intact
    assert updated_ast.sql(dialect="snowflake") == orig_sql
    assert "EXISTS" in updated_ast.sql(dialect="snowflake")
