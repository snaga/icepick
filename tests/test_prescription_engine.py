"""Tests for Prescription data models and PrescriptionEngine."""

from __future__ import annotations

import pytest
from sqlglot.errors import ParseError

from icepick.linter.base import Severity
from icepick.prescription import (
    Prescription,
    PrescriptionAction,
    PrescriptionEngine,
    PrescriptionPlan,
    PrescriptionTarget,
)


class TestPrescriptionModels:
    """Tests for Prescription models serialization and deserialization."""

    def test_prescription_models_serialization(self) -> None:
        """Verify round-trip serialization between objects and dictionaries."""
        target = PrescriptionTarget(
            cte="cte_sales",
            node_type="Order",
            line_range=(12, 14),
        )
        rx = Prescription(
            id="RX-001",
            rule_id="SNOW-003",
            severity=Severity.LOW,
            target=target,
            action=PrescriptionAction.DELETE,
            original_sql="ORDER BY created_at DESC",
            suggested_sql=None,
            rationale="Redundant ORDER BY in CTE without LIMIT",
            expected_impact="Reduces memory consumption and spill to storage by eliminating redundant sorting",
        )
        plan = PrescriptionPlan(
            schema_version="1.0",
            file="queries/sales_report.sql",
            issues_count=1,
            prescriptions=[rx],
        )

        plan_dict = plan.to_dict()
        assert plan_dict["schema_version"] == "1.0"
        assert plan_dict["file"] == "queries/sales_report.sql"
        assert plan_dict["issues_count"] == 1
        assert len(plan_dict["prescriptions"]) == 1

        rx_dict = plan_dict["prescriptions"][0]
        assert rx_dict["id"] == "RX-001"
        assert rx_dict["rule_id"] == "SNOW-003"
        assert rx_dict["severity"] == "LOW"
        assert rx_dict["action"] == "DELETE"
        assert rx_dict["original_sql"] == "ORDER BY created_at DESC"
        assert rx_dict["suggested_sql"] is None
        assert rx_dict["rationale"] == "Redundant ORDER BY in CTE without LIMIT"
        assert (
            rx_dict["expected_impact"]
            == "Reduces memory consumption and spill to storage by eliminating redundant sorting"
        )
        assert rx_dict["target"]["cte"] == "cte_sales"
        assert rx_dict["target"]["node_type"] == "Order"
        assert rx_dict["target"]["line_range"] == [12, 14]

        # Reconstruct from dict
        reconstructed = PrescriptionPlan.from_dict(plan_dict)
        assert reconstructed.schema_version == "1.0"
        assert reconstructed.file == "queries/sales_report.sql"
        assert reconstructed.issues_count == 1
        assert len(reconstructed.prescriptions) == 1

        rec_rx = reconstructed.prescriptions[0]
        assert rec_rx.id == "RX-001"
        assert rec_rx.rule_id == "SNOW-003"
        assert rec_rx.severity == Severity.LOW
        assert rec_rx.action == PrescriptionAction.DELETE
        assert rec_rx.original_sql == "ORDER BY created_at DESC"
        assert rec_rx.suggested_sql is None
        assert rec_rx.rationale == "Redundant ORDER BY in CTE without LIMIT"
        assert (
            rec_rx.expected_impact
            == "Reduces memory consumption and spill to storage by eliminating redundant sorting"
        )
        assert rec_rx.target.cte == "cte_sales"
        assert rec_rx.target.node_type == "Order"
        assert rec_rx.target.line_range == (12, 14)

    def test_prescription_target_from_dict_without_line_range(self) -> None:
        """Verify PrescriptionTarget handles missing line_range gracefully."""
        target_dict = {"cte": None, "node_type": "Select", "line_range": None}
        target = PrescriptionTarget.from_dict(target_dict)
        assert target.cte is None
        assert target.node_type == "Select"
        assert target.line_range is None

        # Verify serialization back to dict preserves None
        assert target.to_dict()["line_range"] is None


class TestPrescriptionEngine:
    """Tests for PrescriptionEngine diagnosis logic."""

    def test_prescription_engine_diagnose_assigns_unique_rx_ids(self) -> None:
        """Verify multiple issues receive sequential, unique RX-xxx IDs."""
        engine = PrescriptionEngine()
        # Query with multiple anti-patterns:
        # 1. Redundant ORDER BY in CTE (SNOW-003)
        # 2. UNION instead of UNION ALL (SNOW-006)
        sql = """
        WITH cte_users AS (
            SELECT id, email FROM users ORDER BY id
        )
        SELECT id, email FROM cte_users
        UNION
        SELECT id, email FROM archive_users
        """
        plan = engine.diagnose(sql, file_path="multi_issues.sql")
        assert plan.file == "multi_issues.sql"
        assert plan.issues_count >= 2
        assert len(plan.prescriptions) >= 2

        # Verify unique, sequential RX IDs
        rx_ids = [rx.id for rx in plan.prescriptions]
        expected_prefix = ["RX-001", "RX-002"]
        assert rx_ids[:2] == expected_prefix
        assert len(rx_ids) == len(set(rx_ids))

    def test_prescription_engine_preserves_target_and_rationale(self) -> None:
        """Verify CTE alias, node type, original SQL, rationale, and impact are captured."""
        engine = PrescriptionEngine()
        sql = """
        WITH subquery_cte AS (
            SELECT user_id, amount FROM transactions ORDER BY amount DESC
        )
        SELECT user_id, amount FROM subquery_cte
        """
        plan = engine.diagnose(sql, file_path="cte_sort.sql")
        assert plan.issues_count == 1
        rx = plan.prescriptions[0]

        assert rx.id == "RX-001"
        assert rx.rule_id == "SNOW-003"
        assert rx.target.cte == "subquery_cte"
        assert rx.target.node_type == "Order"
        assert rx.action == PrescriptionAction.DELETE
        assert rx.suggested_sql is None
        assert "ORDER BY amount DESC" in rx.original_sql
        assert len(rx.rationale) > 0
        assert "redundant sorting" in rx.expected_impact.lower()

    def test_prescription_engine_top_level_query_target(self) -> None:
        """Verify target.cte is None for top-level query issues."""
        engine = PrescriptionEngine()
        sql = """
        SELECT id FROM users
        UNION
        SELECT id FROM legacy_users
        """
        plan = engine.diagnose(sql)
        assert plan.issues_count >= 1
        union_rx = next(rx for rx in plan.prescriptions if rx.rule_id == "SNOW-006")
        assert union_rx.target.cte is None
        assert union_rx.action == PrescriptionAction.REPLACE
        assert union_rx.suggested_sql is not None
        assert "UNION ALL" in union_rx.suggested_sql
        assert "UNION" in union_rx.original_sql

    def test_prescription_engine_clean_query(self) -> None:
        """Verify clean query produces 0 issues and an empty prescription list."""
        engine = PrescriptionEngine()
        sql = "SELECT id, email FROM users WHERE id = 123"
        plan = engine.diagnose(sql, file_path="clean.sql")

        assert plan.schema_version == "1.0"
        assert plan.file == "clean.sql"
        assert plan.issues_count == 0
        assert plan.prescriptions == []

    def test_prescription_engine_syntax_error(self) -> None:
        """Verify invalid SQL raises ParseError."""
        engine = PrescriptionEngine()
        bad_sql = "SELECT * WHERE"

        with pytest.raises(ParseError):
            engine.diagnose(bad_sql)
