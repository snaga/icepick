"""Data models for prescription-driven query optimization.

Defines standardized data structures for prescriptions, prescription targets,
and complete prescription plans according to ADR-0005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from icepick.linter.base import Severity


class PrescriptionAction(str, Enum):
    """Operation type for a prescribed code modification."""

    DELETE = "DELETE"
    REPLACE = "REPLACE"
    INSERT = "INSERT"


@dataclass
class PrescriptionTarget:
    """Location and AST metadata for the target code node to modify.

    Attributes:
        cte: Enclosing CTE alias name (None for top-level query).
        node_type: AST node class name (e.g., 'Join', 'Where', 'Order').
        line_range: Inclusive (start_line, end_line) line number tuple, or None if unavailable.
    """

    cte: str | None = None
    node_type: str = ""
    line_range: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert target location metadata to a JSON-serializable dictionary.

        Returns:
            dict[str, Any]: Serialized target dictionary.
        """
        return {
            "cte": self.cte,
            "node_type": self.node_type,
            "line_range": list(self.line_range) if self.line_range is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrescriptionTarget:
        """Create a PrescriptionTarget instance from a dictionary.

        Args:
            data: Serialized target dictionary.

        Returns:
            PrescriptionTarget: Deserialized target instance.
        """
        raw_lr = data.get("line_range")
        line_range: tuple[int, int] | None = None
        if raw_lr is not None and len(raw_lr) == 2:
            line_range = (int(raw_lr[0]), int(raw_lr[1]))

        return cls(
            cte=data.get("cte"),
            node_type=data.get("node_type", ""),
            line_range=line_range,
        )


@dataclass
class Prescription:
    """A discrete, actionable optimization instruction.

    Attributes:
        id: Unique prescription identifier (e.g., 'RX-001').
        rule_id: Diagnostic rule ID that triggered this prescription (e.g., 'SNOW-001').
        severity: Severity level (INFO, LOW, MEDIUM, HIGH, CRITICAL).
        target: Target location and AST metadata.
        action: Operation type (DELETE, REPLACE, INSERT).
        original_sql: The existing SQL code snippet targeted for modification.
        suggested_sql: Recommended replacement SQL snippet (None for DELETE action).
        rationale: Explanation of why this modification is required (Why).
        expected_impact: Expected performance or resource impact (e.g., pruning, spill reduction).
    """

    id: str
    rule_id: str
    severity: Severity
    target: PrescriptionTarget
    action: PrescriptionAction
    original_sql: str
    suggested_sql: str | None = None
    rationale: str = ""
    expected_impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert prescription to a JSON-serializable dictionary.

        Returns:
            dict[str, Any]: Serialized prescription dictionary.
        """
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "target": self.target.to_dict(),
            "action": self.action.value,
            "original_sql": self.original_sql,
            "suggested_sql": self.suggested_sql,
            "rationale": self.rationale,
            "expected_impact": self.expected_impact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Prescription:
        """Create a Prescription instance from a dictionary.

        Args:
            data: Serialized prescription dictionary.

        Returns:
            Prescription: Deserialized prescription instance.
        """
        raw_sev = data["severity"]
        severity = raw_sev if isinstance(raw_sev, Severity) else Severity(str(raw_sev).upper())

        raw_act = data["action"]
        action = (
            raw_act
            if isinstance(raw_act, PrescriptionAction)
            else PrescriptionAction(str(raw_act).upper())
        )

        target_data = data.get("target", {})
        target = (
            target_data
            if isinstance(target_data, PrescriptionTarget)
            else PrescriptionTarget.from_dict(target_data)
        )

        return cls(
            id=data["id"],
            rule_id=data["rule_id"],
            severity=severity,
            target=target,
            action=action,
            original_sql=data["original_sql"],
            suggested_sql=data.get("suggested_sql"),
            rationale=data.get("rationale", ""),
            expected_impact=data.get("expected_impact", ""),
        )


@dataclass
class PrescriptionPlan:
    """Comprehensive diagnostic plan containing a set of prescriptions.

    Attributes:
        schema_version: Schema version for backwards-compatibility (default: '1.0').
        file: Path or identifier of the diagnosed SQL file.
        issues_count: Total number of detected issues / prescriptions.
        prescriptions: List of prescribed optimization instructions.
    """

    schema_version: str = "1.0"
    file: str = ""
    issues_count: int = 0
    prescriptions: list[Prescription] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert plan to a JSON-serializable dictionary.

        Returns:
            dict[str, Any]: Serialized plan dictionary.
        """
        return {
            "schema_version": self.schema_version,
            "file": self.file,
            "issues_count": self.issues_count,
            "prescriptions": [p.to_dict() for p in self.prescriptions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrescriptionPlan:
        """Create a PrescriptionPlan instance from a dictionary.

        Args:
            data: Serialized plan dictionary.

        Returns:
            PrescriptionPlan: Deserialized plan instance.
        """
        raw_rx = data.get("prescriptions", [])
        prescriptions = [
            p if isinstance(p, Prescription) else Prescription.from_dict(p)
            for p in raw_rx
        ]
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            file=data.get("file", ""),
            issues_count=data.get("issues_count", len(prescriptions)),
            prescriptions=prescriptions,
        )
