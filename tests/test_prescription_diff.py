"""Unit tests for prescription-targeted diff generation engine.

Tests PrescriptionEngine.generate_diff according to ADR-0005 and requirement G-2.
Validates Detroit-school state transitions: surgical patching of raw SQL text
matching only selected prescription IDs.
"""

from __future__ import annotations

import pytest

from icepick.prescription.engine import PrescriptionEngine
from icepick.prescription.models import PrescriptionPlan


class TestPrescriptionDiffEngine:
    """Test suite for PrescriptionEngine.generate_diff."""

    @pytest.fixture
    def engine(self) -> PrescriptionEngine:
        """Create a default PrescriptionEngine instance."""
        return PrescriptionEngine()

    @pytest.fixture
    def multi_issue_sql(self) -> str:
        """SQL query with redundant sort in subquery and non-sargable predicate."""
        return (
            "SELECT * FROM (\n"
            "    SELECT id, name FROM raw_data ORDER BY id\n"
            ") sub\n"
            "WHERE DATE(created_at) = '2023-01-01';\n"
        )

    def test_generate_diff_all_prescriptions(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify generate_diff without selected_ids applies all detected prescriptions."""
        diff = engine.generate_diff(multi_issue_sql, filename="test.sql")

        assert diff != ""
        assert "--- a/test.sql" in diff
        assert "+++ b/test.sql" in diff
        # Redundant sort removed
        assert "-    SELECT id, name FROM raw_data ORDER BY id" in diff
        assert "+    SELECT id, name FROM raw_data" in diff
        # Non-sargable replaced with range condition
        assert "-WHERE DATE(created_at) = '2023-01-01';" in diff
        assert (
            "+WHERE created_at >= '2023-01-01' AND created_at < DATEADD(DAY, 1, '2023-01-01');"
            in diff
        )

    def test_generate_diff_selected_rx_id(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify specifying a single prescription ID applies only that fix and preserves all other code."""
        plan = engine.diagnose(multi_issue_sql)
        assert len(plan.prescriptions) >= 2

        # 1. Apply only RX-001 (NonSargableRule)
        diff_rx1 = engine.generate_diff(multi_issue_sql, plan=plan, selected_ids=["RX-001"])
        assert "created_at >= '2023-01-01'" in diff_rx1
        # ORDER BY must remain untouched (no diff line with + or - modifies ORDER BY)
        assert not any(
            line.startswith(("-", "+")) and "ORDER BY" in line for line in diff_rx1.splitlines()
        )

        # 2. Apply only RX-002 (RedundantSortRule)
        diff_rx2 = engine.generate_diff(multi_issue_sql, plan=plan, selected_ids=["RX-002"])
        assert "-    SELECT id, name FROM raw_data ORDER BY id" in diff_rx2
        assert "+    SELECT id, name FROM raw_data" in diff_rx2
        # DATE(created_at) predicate must remain untouched: not in added/removed lines, but preserved as context
        assert not any(
            line.startswith(("-", "+")) and "created_at" in line for line in diff_rx2.splitlines()
        )
        assert " WHERE DATE(created_at) = '2023-01-01';" in diff_rx2

    def test_generate_diff_multiple_selected_rx_ids(self, engine: PrescriptionEngine) -> None:
        """Verify selecting a subset of prescriptions synthesizes diffs only for specified IDs."""
        sql = (
            "SELECT id, val FROM t1\n"
            "UNION\n"
            "SELECT id, val FROM (\n"
            "    SELECT id, val FROM t2 ORDER BY id\n"
            ")\n"
            "WHERE DATE(created_at) = '2023-01-01';\n"
        )
        plan = engine.diagnose(sql)
        rx_ids = [p.id for p in plan.prescriptions]
        assert len(rx_ids) >= 3

        # Select only RX-001 (sargable) and RX-003 (union)
        target_ids = ["RX-001", "RX-003"]
        diff = engine.generate_diff(sql, plan=plan, selected_ids=target_ids)

        assert diff != ""
        # The selected fixes should be present
        for rid in target_ids:
            rx = next(p for p in plan.prescriptions if p.id == rid)
            if rx.suggested_sql:
                assert rx.suggested_sql in diff or "UNION ALL" in diff or "created_at >=" in diff

    def test_generate_diff_invalid_rx_id_raises_error(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify invalid prescription IDs raise ValueError with actionable error message."""
        with pytest.raises(ValueError) as exc_info:
            engine.generate_diff(multi_issue_sql, selected_ids=["RX-999"])

        err_msg = str(exc_info.value)
        assert "Invalid prescription ID(s): RX-999" in err_msg
        assert "Available IDs:" in err_msg
        assert "RX-001" in err_msg

    def test_generate_diff_multiple_invalid_rx_ids(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify multiple invalid IDs are all reported in the error message."""
        with pytest.raises(ValueError) as exc_info:
            engine.generate_diff(multi_issue_sql, selected_ids=["RX-888", "RX-999"])

        err_msg = str(exc_info.value)
        assert "RX-888" in err_msg
        assert "RX-999" in err_msg

    def test_generate_diff_clean_query_returns_empty(self, engine: PrescriptionEngine) -> None:
        """Verify clean SQL query produces an empty diff string."""
        clean_sql = "SELECT id, name FROM users WHERE id = 1;"
        diff = engine.generate_diff(clean_sql)
        assert diff == ""

    def test_generate_diff_empty_selected_ids_returns_empty(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify empty selected_ids list produces an empty diff string."""
        diff = engine.generate_diff(multi_issue_sql, selected_ids=[])
        assert diff == ""

    def test_generate_diff_with_explicit_plan(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify passing an explicit PrescriptionPlan produces the identical diff as None."""
        plan = engine.diagnose(multi_issue_sql)
        diff_with_plan = engine.generate_diff(multi_issue_sql, plan=plan)
        diff_without_plan = engine.generate_diff(multi_issue_sql)

        assert diff_with_plan != ""
        assert diff_with_plan == diff_without_plan

    def test_generate_diff_with_deserialized_plan_fallback(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify diff generation works even when PrescriptionPlan was deserialized from JSON (no _issue)."""
        plan = engine.diagnose(multi_issue_sql)
        plan_dict = plan.to_dict()
        deserialized_plan = PrescriptionPlan.from_dict(plan_dict)

        # Confirm _issue is None on deserialized prescriptions
        for rx in deserialized_plan.prescriptions:
            assert rx._issue is None

        diff = engine.generate_diff(
            multi_issue_sql, plan=deserialized_plan, selected_ids=["RX-001"]
        )
        assert diff != ""
        assert "created_at >= '2023-01-01'" in diff

    def test_generate_diff_custom_filename(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify custom filename is properly reflected in diff header lines."""
        custom_file = "reports/daily_orders.sql"
        diff = engine.generate_diff(multi_issue_sql, filename=custom_file)

        assert f"--- a/{custom_file}" in diff
        assert f"+++ b/{custom_file}" in diff
