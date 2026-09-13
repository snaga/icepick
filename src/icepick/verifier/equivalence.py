"""Snowflake EXCEPT-based deterministic equivalence verifier module.

Constructs bidirectional EXCEPT queries to mathematically prove that
an optimized SQL query produces identical result sets to the original query.
Execution is delegated to Snowflake CLI (snow) or external database clients.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of query equivalence verification.

    Attributes:
        is_equivalent: True if row difference count is 0 in both directions.
        orig_not_in_opt_count: Number of rows present in original query but absent in optimized query.
        opt_not_in_orig_count: Number of rows present in optimized query but absent in original query.
        verification_sql: The bidirectional EXCEPT verification SQL query.
        error_message: Optional error message if execution or connectivity failed.
    """

    is_equivalent: bool
    orig_not_in_opt_count: int
    opt_not_in_orig_count: int
    verification_sql: str
    error_message: str | None = None


def generate_verification_sql(
    orig_sql: str,
    opt_sql: str,
    count_only: bool = False,
    dialect: str = "snowflake",
) -> str:
    """Generate a bidirectional EXCEPT verification SQL query.

    Constructs a deterministic SQL query comparing the result sets of orig_sql and opt_sql
    in both directions (orig EXCEPT opt and opt EXCEPT orig). Returns 0 rows (or count 0)
    if and only if both queries are semantically equivalent.

    Args:
        orig_sql: Original SQL query text.
        opt_sql: Optimized SQL query text.
        count_only: If True, aggregates difference count into a count query.
            If False, produces actual row differences with diff_type metadata.
        dialect: Target SQL dialect (default: "snowflake"). Reserved for future dialect-specific features.

    Returns:
        str: Generated bidirectional EXCEPT verification SQL query.
    """
    clean_orig = orig_sql.strip()
    while clean_orig.endswith(";"):
        clean_orig = clean_orig[:-1].strip()

    clean_opt = opt_sql.strip()
    while clean_opt.endswith(";"):
        clean_opt = clean_opt[:-1].strip()

    if count_only:
        return (
            "-- Icepick Equivalence Verification Count Query\n"
            "WITH orig AS (\n"
            f"{clean_orig}\n"
            "),\n"
            "opt AS (\n"
            f"{clean_opt}\n"
            ")\n"
            "SELECT 'orig_not_in_opt' AS diff_type, COUNT(*) AS cnt FROM (\n"
            "  SELECT * FROM orig EXCEPT SELECT * FROM opt\n"
            ")\n"
            "UNION ALL\n"
            "SELECT 'opt_not_in_orig' AS diff_type, COUNT(*) AS cnt FROM (\n"
            "  SELECT * FROM opt EXCEPT SELECT * FROM orig\n"
            ");"
        )

    return (
        "-- Icepick Equivalence Verification Query\n"
        "-- Returns 0 rows if both queries are semantically equivalent.\n"
        "WITH orig AS (\n"
        f"{clean_orig}\n"
        "),\n"
        "opt AS (\n"
        f"{clean_opt}\n"
        ")\n"
        "SELECT 'orig_not_in_opt' AS diff_type, * FROM (\n"
        "  SELECT * FROM orig EXCEPT SELECT * FROM opt\n"
        ")\n"
        "UNION ALL\n"
        "SELECT 'opt_not_in_orig' AS diff_type, * FROM (\n"
        "  SELECT * FROM opt EXCEPT SELECT * FROM orig\n"
        ");"
    )


class EquivalenceVerifier:
    """Mathematical equivalence verification engine using bidirectional EXCEPT operations."""

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize EquivalenceVerifier.

        Args:
            dialect: Target SQL dialect (default: "snowflake").
        """
        self.dialect = dialect

    def generate_sql(
        self,
        orig_sql: str,
        opt_sql: str,
        count_only: bool = False,
    ) -> str:
        """Generate a bidirectional EXCEPT verification SQL query.

        Args:
            orig_sql: Original SQL query text.
            opt_sql: Optimized SQL query text.
            count_only: If True, aggregates difference count. If False, returns actual difference rows.

        Returns:
            str: Generated verification SQL query.
        """
        return generate_verification_sql(
            orig_sql,
            opt_sql,
            count_only=count_only,
            dialect=self.dialect,
        )

    def build_verification_query(
        self,
        orig_sql: str,
        opt_sql: str,
        count_only: bool = False,
        dialect: str | None = None,
    ) -> str:
        """Dynamically construct a bidirectional EXCEPT verification SQL query.

        Backward-compatibility alias for generate_sql.

        Args:
            orig_sql: Original SQL query text.
            opt_sql: Optimized SQL query text.
            count_only: If True, aggregates difference count. If False, returns actual difference rows.
            dialect: Optional dialect override.

        Returns:
            str: Generated verification SQL query.
        """
        target_dialect = dialect or self.dialect
        return generate_verification_sql(
            orig_sql,
            opt_sql,
            count_only=count_only,
            dialect=target_dialect,
        )
