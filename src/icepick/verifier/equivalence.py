"""Snowflake EXCEPT-based deterministic equivalence verifier module.

Constructs and executes bidirectional EXCEPT queries to mathematically prove that
an optimized SQL query produces identical result sets to the original query.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from icepick.config import Config
from icepick.security.credentials import format_actionable_error, resolve_credential

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

    def verify_with_snowflake(
        self,
        orig_sql: str,
        opt_sql: str,
        config: Config | None = None,
        dialect: str | None = None,
        timeout: int | None = None,
    ) -> VerificationResult:
        """Execute bidirectional EXCEPT verification query directly against Snowflake.

        Resolves Snowflake connection credentials from Config, secure credential provider,
        or environment variables, establishes a session, and executes the mathematical
        equivalence query.

        Args:
            orig_sql: Original SQL query text.
            opt_sql: Optimized SQL query text.
            config: Optional configuration object containing Snowflake connection settings.
            dialect: Optional dialect override (default: verifier's dialect).
            timeout: Optional query timeout in seconds (sets STATEMENT_TIMEOUT_IN_SECONDS).

        Returns:
            VerificationResult: Verification verdict, difference counts, and error message if any.
        """
        target_dialect = dialect or self.dialect
        sql = self.build_verification_query(orig_sql, opt_sql, dialect=target_dialect)

        cfg = config or Config.from_env()

        account = getattr(cfg, "snowflake_account", None) or os.environ.get("SNOWFLAKE_ACCOUNT")
        user = getattr(cfg, "snowflake_user", None) or os.environ.get("SNOWFLAKE_USER")
        database = getattr(cfg, "snowflake_database", None) or os.environ.get("SNOWFLAKE_DATABASE")
        schema = getattr(cfg, "snowflake_schema", None) or os.environ.get("SNOWFLAKE_SCHEMA")
        warehouse = getattr(cfg, "snowflake_warehouse", None) or os.environ.get(
            "SNOWFLAKE_WAREHOUSE"
        )
        role = getattr(cfg, "snowflake_role", None) or os.environ.get("SNOWFLAKE_ROLE")

        password = getattr(cfg, "snowflake_password", None)
        if not password:
            try:
                password, _ = resolve_credential("snowflake_password")
            except Exception:  # noqa: BLE001
                password = os.environ.get("SNOWFLAKE_PASSWORD")

        missing: list[str] = []
        if not account:
            missing.append("account")
        if not user:
            missing.append("user")
        if not password:
            missing.append("password")
        if not database:
            missing.append("database")
        if not warehouse:
            missing.append("warehouse")

        if missing:
            if "password" in missing:
                auth_err = format_actionable_error("snowflake_password")
                if len(missing) == 1:
                    err_msg = auth_err
                else:
                    err_msg = (
                        f"Missing required Snowflake connection parameter(s): {', '.join(missing)}.\n"
                        f"{auth_err}"
                    )
            else:
                err_msg = (
                    f"Missing required Snowflake connection parameter(s): {', '.join(missing)}."
                )
            logger.warning("Snowflake connection skipped: %s", err_msg)
            return VerificationResult(
                is_equivalent=False,
                orig_not_in_opt_count=-1,
                opt_not_in_orig_count=-1,
                verification_sql=sql,
                error_message=err_msg,
            )

        try:
            import importlib

            snowflake_connector = importlib.import_module("snowflake.connector")
        except ImportError:
            err_msg = (
                "snowflake-connector-python is not installed. "
                "Please install it via 'pip install snowflake-connector-python' to run live verification."
            )
            logger.warning(err_msg)
            return VerificationResult(
                is_equivalent=False,
                orig_not_in_opt_count=-1,
                opt_not_in_orig_count=-1,
                verification_sql=sql,
                error_message=err_msg,
            )

        conn_params: dict[str, Any] = {
            "account": account,
            "user": user,
            "password": password,
            "database": database,
            "warehouse": warehouse,
        }
        if schema:
            conn_params["schema"] = schema
        if role:
            conn_params["role"] = role

        session_parameters: dict[str, Any] = {}
        if timeout is not None:
            session_parameters["STATEMENT_TIMEOUT_IN_SECONDS"] = timeout
        if session_parameters:
            conn_params["session_parameters"] = session_parameters

        try:
            conn = snowflake_connector.connect(**conn_params)
            try:
                return self.verify(orig_sql, opt_sql, conn, dialect=target_dialect)
            finally:
                if hasattr(conn, "close") and callable(conn.close):
                    conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Snowflake verification failed: %s", exc)
            return VerificationResult(
                is_equivalent=False,
                orig_not_in_opt_count=-1,
                opt_not_in_orig_count=-1,
                verification_sql=sql,
                error_message=str(exc),
            )
