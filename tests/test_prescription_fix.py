"""Unit tests for prescription in-place fix application engine.

Tests PrescriptionEngine.apply_fixes according to ADR-0005 and requirement G-3.
Validates Detroit-school state transitions: surgical patching of raw SQL text
matching only selected prescription IDs while preserving 100% of comments, formatting,
casing, and untouched code.
"""

from __future__ import annotations

import pytest

from icepick.prescription.engine import PrescriptionEngine
from icepick.prescription.models import PrescriptionPlan


class TestPrescriptionFixEngine:
    """Test suite for PrescriptionEngine.apply_fixes."""

    @pytest.fixture
    def engine(self) -> PrescriptionEngine:
        """Create a default PrescriptionEngine instance."""
        return PrescriptionEngine()

    @pytest.fixture
    def multi_issue_sql(self) -> str:
        """SQL query with redundant sort in subquery and non-sargable predicate with comments."""
        return (
            "-- Crucial business header comment\n"
            "SELECT\n"
            "    sub.id,\n"
            "    sub.name\n"
            "FROM (\n"
            "    -- Sorting inside subquery is redundant\n"
            "    SELECT id, name FROM raw_data ORDER BY id\n"
            ") sub\n"
            "-- Sargable filter condition\n"
            "WHERE DATE(created_at) = '2023-01-01';\n"
        )

    def test_apply_fixes_all(self, engine: PrescriptionEngine, multi_issue_sql: str) -> None:
        """Verify apply_fixes without selected_ids applies all detected prescriptions.

        All identified anti-patterns should be surgically corrected while maintaining
        all untouched formatting and user comments intact.
        """
        modified_sql, applied_ids = engine.apply_fixes(multi_issue_sql)

        # 1. Verify applied IDs
        assert len(applied_ids) >= 2
        assert "RX-001" in applied_ids
        assert "RX-002" in applied_ids

        # 2. Verify comments are 100% preserved
        assert "-- Crucial business header comment" in modified_sql
        assert "-- Sorting inside subquery is redundant" in modified_sql
        assert "-- Sargable filter condition" in modified_sql

        # 3. Verify redundant sort was removed surgically
        assert "ORDER BY id" not in modified_sql
        assert "SELECT id, name FROM raw_data" in modified_sql

        # 4. Verify non-sargable date filter was transformed into a range condition
        assert "DATE(created_at)" not in modified_sql
        assert "created_at >= '2023-01-01' AND created_at < DATEADD(DAY, 1, '2023-01-01')" in modified_sql

    def test_apply_fixes_selected_rx_only(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify specifying a single prescription ID applies only that fix and leaves others untouched."""
        plan = engine.diagnose(multi_issue_sql)
        assert len(plan.prescriptions) >= 2

        # 1. Apply only RX-001 (NonSargableRule)
        modified_rx1, applied_rx1 = engine.apply_fixes(
            multi_issue_sql, plan=plan, selected_ids=["RX-001"]
        )
        assert applied_rx1 == ["RX-001"]
        assert "created_at >= '2023-01-01'" in modified_rx1
        # ORDER BY must remain untouched in the text
        assert "ORDER BY id" in modified_rx1

        # 2. Apply only RX-002 (RedundantSortRule)
        modified_rx2, applied_rx2 = engine.apply_fixes(
            multi_issue_sql, plan=plan, selected_ids=["RX-002"]
        )
        assert applied_rx2 == ["RX-002"]
        assert "ORDER BY id" not in modified_rx2
        # DATE(created_at) must remain untouched in the text
        assert "DATE(created_at) = '2023-01-01'" in modified_rx2

    def test_apply_fixes_multiple_selected(self, engine: PrescriptionEngine) -> None:
        """Verify selecting a subset of prescriptions applies only specified IDs in composition."""
        sql = (
            "/* header */\n"
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

        # Select a subset: RX-001 and RX-003
        target_ids = ["RX-001", "RX-003"]
        modified_sql, applied_ids = engine.apply_fixes(sql, plan=plan, selected_ids=target_ids)

        assert set(applied_ids) == set(target_ids)
        # Header comment preserved
        assert "/* header */" in modified_sql
        # Selected fixes applied
        assert "created_at >= '2023-01-01'" in modified_sql
        assert "UNION ALL" in modified_sql
        # Redundant sort (RX-002) was NOT selected, so it must remain intact
        assert "ORDER BY id" in modified_sql

    def test_apply_fixes_invalid_rx_raises_error(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify invalid prescription IDs raise ValueError with actionable error message."""
        with pytest.raises(ValueError) as exc_info:
            engine.apply_fixes(multi_issue_sql, selected_ids=["RX-999"])

        err_msg = str(exc_info.value)
        assert "Invalid prescription ID(s): RX-999" in err_msg
        assert "Available IDs:" in err_msg
        assert "RX-001" in err_msg

    def test_apply_fixes_clean_query_unchanged(self, engine: PrescriptionEngine) -> None:
        """Verify clean SQL query returns original text and empty applied IDs list."""
        clean_sql = "-- Pristine query\nSELECT id, name FROM users WHERE id = 1;\n"
        modified_sql, applied_ids = engine.apply_fixes(clean_sql)

        assert modified_sql == clean_sql
        assert applied_ids == []

    def test_apply_fixes_empty_selected_ids_unchanged(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify empty selected_ids list produces no changes and returns empty applied IDs."""
        modified_sql, applied_ids = engine.apply_fixes(multi_issue_sql, selected_ids=[])

        assert modified_sql == multi_issue_sql
        assert applied_ids == []

    def test_apply_fixes_with_explicit_plan(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify passing an explicit PrescriptionPlan produces the identical result as None."""
        plan = engine.diagnose(multi_issue_sql)
        mod_with_plan, ids_with_plan = engine.apply_fixes(multi_issue_sql, plan=plan)
        mod_without_plan, ids_without_plan = engine.apply_fixes(multi_issue_sql)

        assert mod_with_plan == mod_without_plan
        assert ids_with_plan == ids_without_plan

    def test_apply_fixes_with_deserialized_plan_fallback(
        self, engine: PrescriptionEngine, multi_issue_sql: str
    ) -> None:
        """Verify in-place fixing works safely when PrescriptionPlan was deserialized from JSON (no _issue)."""
        plan = engine.diagnose(multi_issue_sql)
        plan_dict = plan.to_dict()
        deserialized_plan = PrescriptionPlan.from_dict(plan_dict)

        # Confirm _issue is None on all deserialized prescriptions
        for rx in deserialized_plan.prescriptions:
            assert rx._issue is None

        modified_sql, applied_ids = engine.apply_fixes(
            multi_issue_sql, plan=deserialized_plan, selected_ids=["RX-001"]
        )

        assert applied_ids == ["RX-001"]
        assert "created_at >= '2023-01-01'" in modified_sql
        # Unselected sort remains intact
        assert "ORDER BY id" in modified_sql
