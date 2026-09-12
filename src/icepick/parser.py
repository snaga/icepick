"""Snowflake SQL parser module wrapping sqlglot.

Provides AST parsing for Snowflake dialect, BOM stripping, validation,
and detailed error reporting with line/column information.
"""

from __future__ import annotations

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp

from icepick.exceptions import ParseError

__all__ = ["ParseError", "SQLParser", "parse_snowflake_sql"]


def parse_snowflake_sql(sql: str, dialect: str = "snowflake") -> exp.Expression:
    """Parse a Snowflake SQL query into a sqlglot Expression AST.

    Removes UTF-8 BOM if present, validates that query is non-empty, and returns
    the parsed AST root.

    Args:
        sql: SQL query string.
        dialect: SQL dialect name (default: "snowflake").

    Returns:
        exp.Expression: Root AST node of the parsed query.

    Raises:
        ParseError: If SQL is empty or contains syntax/tokenization errors.
    """
    if not isinstance(sql, str):
        raise ParseError(f"Expected SQL string, got {type(sql).__name__}")

    # Safely strip UTF-8 BOM if present at start without affecting line count
    clean_sql = sql.removeprefix("\ufeff")

    if not clean_sql.strip():
        raise ParseError("Cannot parse empty or whitespace-only SQL query.")

    try:
        ast = sqlglot.parse_one(clean_sql, read=dialect)
        if not isinstance(ast, exp.Expression):
            raise ParseError("No valid Expression node was parsed from query.")
        return ast
    except sqlglot_errors.ParseError as e:
        line: int | None = None
        col: int | None = None
        token: str | None = None
        context: str | None = None
        desc = "SQL syntax error"

        if e.errors:
            first_err = e.errors[0]
            line = first_err.get("line")
            col = first_err.get("col")
            token = first_err.get("highlight")
            desc = first_err.get("description") or desc
            start = first_err.get("start_context", "")
            hl = first_err.get("highlight", "")
            end = first_err.get("end_context", "")
            ctx_candidate = f"{start}{hl}{end}".strip()
            if ctx_candidate:
                context = ctx_candidate

        raise ParseError(
            message=desc,
            line=line,
            col=col,
            token=token,
            context=context,
            raw_error=e,
        ) from e
    except sqlglot_errors.TokenError as e:
        raise ParseError(
            message=f"SQL tokenization error: {e}",
            raw_error=e,
        ) from e
    except sqlglot_errors.SqlglotError as e:
        raise ParseError(
            message=f"SQL parsing failed: {e}",
            raw_error=e,
        ) from e


class SQLParser:
    """Parser class for Snowflake SQL expressions.

    Encapsulates dialect settings and provides an object-oriented parsing interface.
    """

    def __init__(self, dialect: str = "snowflake") -> None:
        """Initialize SQLParser with specified dialect.

        Args:
            dialect: SQL dialect name (default: "snowflake").
        """
        self.dialect = dialect

    def parse(self, sql: str) -> exp.Expression:
        """Parse a SQL string into an AST Expression.

        Args:
            sql: SQL query string.

        Returns:
            exp.Expression: Parsed AST expression.

        Raises:
            ParseError: If parsing fails.
        """
        return parse_snowflake_sql(sql, dialect=self.dialect)
