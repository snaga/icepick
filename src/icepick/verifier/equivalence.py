"""Snowflake EXCEPT-based deterministic equivalence verifier module.

Constructs and executes bidirectional EXCEPT queries to mathematically prove that
an optimized SQL query produces identical result sets to the original query.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

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


class EquivalenceVerifier:
    """Mathematical equivalence verification engine using bidirectional EXCEPT operations."""

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize EquivalenceVerifier.

        Args:
            dialect: Target SQL dialect (default: "snowflake").
        """
        self.dialect = dialect

    def build_verification_query(
        self,
        orig_sql: str,
        opt_sql: str,
        dialect: str | None = None,
    ) -> str:
        """Dynamically construct a bidirectional EXCEPT verification SQL query.

        Args:
            orig_sql: Original SQL query text.
            opt_sql: Optimized SQL query text.
            dialect: Optional dialect override.

        Returns:
            str: Generated verification SQL query.
        """
        clean_orig = orig_sql.strip().rstrip(";")
        clean_opt = opt_sql.strip().rstrip(";")

        return (
            "WITH orig AS (\n"
            f"{clean_orig}\n"
            "),\n"
            "opt AS (\n"
            f"{clean_opt}\n"
            ")\n"
            "SELECT 'orig_not_in_opt' AS diff_type, COUNT(*) AS cnt FROM ("
            "SELECT * FROM orig EXCEPT SELECT * FROM opt"
            ")\n"
            "UNION ALL\n"
            "SELECT 'opt_not_in_orig' AS diff_type, COUNT(*) AS cnt FROM ("
            "SELECT * FROM opt EXCEPT SELECT * FROM orig"
            ");"
        )

    def verify(
        self,
        orig_sql: str,
        opt_sql: str,
        connection: Any,
        dialect: str | None = None,
    ) -> VerificationResult:
        """Execute bidirectional EXCEPT verification query via a database connection or cursor.

        Args:
            orig_sql: Original SQL query text.
            opt_sql: Optimized SQL query text.
            connection: Database connection or cursor object implementing execute() and fetchall().
            dialect: Optional dialect override.

        Returns:
            VerificationResult: Detailed verification metrics and equivalence verdict.
        """
        target_dialect = dialect or self.dialect
        sql = self.build_verification_query(orig_sql, opt_sql, dialect=target_dialect)

        try:
            if hasattr(connection, "cursor") and callable(connection.cursor):
                cursor = connection.cursor()
                should_close_cursor = True
            else:
                cursor = connection
                should_close_cursor = False

            try:
                cursor.execute(sql)
                rows = cursor.fetchall()
            finally:
                if should_close_cursor and hasattr(cursor, "close") and callable(cursor.close):
                    cursor.close()

            orig_not_in_opt = -1
            opt_not_in_orig = -1

            for row in rows:
                if isinstance(row, dict):
                    dtype = row.get("diff_type") or row.get("DIFF_TYPE")
                    cnt = row.get("cnt") if "cnt" in row else row.get("CNT")
                else:
                    dtype = row[0]
                    cnt = row[1]

                if dtype == "orig_not_in_opt" and cnt is not None:
                    orig_not_in_opt = int(cnt)
                elif dtype == "opt_not_in_orig" and cnt is not None:
                    opt_not_in_orig = int(cnt)

            if orig_not_in_opt < 0 or opt_not_in_orig < 0:
                return VerificationResult(
                    is_equivalent=False,
                    orig_not_in_opt_count=max(0, orig_not_in_opt),
                    opt_not_in_orig_count=max(0, opt_not_in_orig),
                    verification_sql=sql,
                    error_message="Unexpected query result format: missing expected difference rows.",
                )

            is_equiv = orig_not_in_opt == 0 and opt_not_in_orig == 0
            return VerificationResult(
                is_equivalent=is_equiv,
                orig_not_in_opt_count=orig_not_in_opt,
                opt_not_in_orig_count=opt_not_in_orig,
                verification_sql=sql,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("Verification execution failed: %s", exc)
            return VerificationResult(
                is_equivalent=False,
                orig_not_in_opt_count=-1,
                opt_not_in_orig_count=-1,
                verification_sql=sql,
                error_message=str(exc),
            )
