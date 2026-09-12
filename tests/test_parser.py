"""Unit tests for Snowflake SQL parser module."""

import pytest
from sqlglot import exp

from icepick.exceptions import IcepickError
from icepick.parser import ParseError, SQLParser, parse_snowflake_sql


def test_parse_error_hierarchy() -> None:
    """Test ParseError inherits from IcepickError."""
    assert issubclass(ParseError, IcepickError)
    assert issubclass(ParseError, Exception)
    err = ParseError("test error")
    assert isinstance(err, IcepickError)


def test_parse_simple_select() -> None:
    """Test parsing simple SELECT statement."""
    sql = "SELECT id, name FROM users WHERE active = true"
    ast = parse_snowflake_sql(sql)
    assert isinstance(ast, exp.Select)
    assert len(ast.expressions) == 2


def test_parse_cte_chain() -> None:
    """Test parsing complex Snowflake query with CTE chain."""
    sql = """
    WITH regional_sales AS (
        SELECT region, SUM(amount) AS total_sales
        FROM orders
        GROUP BY region
    ),
    top_regions AS (
        SELECT region
        FROM regional_sales
        WHERE total_sales > 100000
    )
    SELECT o.order_id, o.amount, o.region
    FROM orders o
    JOIN top_regions t ON o.region = t.region
    """
    ast = parse_snowflake_sql(sql)
    assert isinstance(ast, exp.Select)
    with_clause = ast.args.get("with_")
    assert with_clause is not None
    assert len(with_clause.expressions) == 2


def test_parse_qualify_clause() -> None:
    """Test parsing Snowflake QUALIFY clause."""
    sql = """
    SELECT
        user_id,
        session_id,
        timestamp,
        ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY timestamp DESC) AS rn
    FROM user_events
    QUALIFY rn = 1
    """
    ast = parse_snowflake_sql(sql)
    assert isinstance(ast, exp.Select)
    assert "qualify" in ast.args


def test_parse_snowflake_specific_functions() -> None:
    """Test parsing queries with Snowflake-specific functions."""
    sql = """
    SELECT
        DATEADD('day', 7, CURRENT_TIMESTAMP()) AS next_week,
        DATEDIFF('month', start_date, end_date) AS elapsed_months,
        PARSE_JSON(raw_payload):user:id::STRING AS payload_user_id,
        TRY_TO_DATE(date_str, 'YYYY-MM-DD') AS parsed_date
    FROM events
    """
    ast = parse_snowflake_sql(sql)
    assert isinstance(ast, exp.Select)
    assert len(ast.expressions) == 4


def test_parse_utf8_bom() -> None:
    """Test parsing SQL with UTF-8 BOM prefix."""
    sql_with_bom = "\ufeffSELECT user_id, count(*) FROM logs GROUP BY user_id"
    ast = parse_snowflake_sql(sql_with_bom)
    assert isinstance(ast, exp.Select)

    cte_with_bom = "\ufeffWITH data AS (SELECT 1 AS n) SELECT n FROM data"
    ast_cte = parse_snowflake_sql(cte_with_bom)
    assert isinstance(ast_cte, exp.Select)


def test_syntax_error_with_line_and_token() -> None:
    """Test that syntax errors produce ParseError with line, col, and token details."""
    invalid_sql = """SELECT
    col1,
    col2
FORM
    my_table"""

    with pytest.raises(ParseError) as exc_info:
        parse_snowflake_sql(invalid_sql)

    err = exc_info.value
    assert err.line == 5
    assert err.col is not None
    assert err.token == "my_table"
    assert "Line 5" in str(err)
    assert "my_table" in str(err)


def test_tokenization_error() -> None:
    """Test tokenization error handling, such as unclosed literal."""
    unterminated_sql = "SELECT 'unclosed literal string"
    with pytest.raises(ParseError) as exc_info:
        parse_snowflake_sql(unterminated_sql)

    assert "token" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()


def test_empty_sql_validation() -> None:
    """Test that empty or whitespace-only SQL raises ParseError."""
    with pytest.raises(ParseError, match="empty or whitespace-only"):
        parse_snowflake_sql("")

    with pytest.raises(ParseError, match="empty or whitespace-only"):
        parse_snowflake_sql("   \n\t  \r  ")

    with pytest.raises(ParseError, match="empty or whitespace-only"):
        parse_snowflake_sql("\ufeff   ")


def test_invalid_type_input() -> None:
    """Test that non-string input raises ParseError."""
    with pytest.raises(ParseError, match="Expected SQL string"):
        parse_snowflake_sql(None)  # type: ignore[arg-type]

    with pytest.raises(ParseError, match="Expected SQL string"):
        parse_snowflake_sql(12345)  # type: ignore[arg-type]


def test_sql_parser_class() -> None:
    """Test SQLParser class wrapper."""
    parser = SQLParser(dialect="snowflake")
    ast = parser.parse("SELECT 42 AS answer")
    assert isinstance(ast, exp.Select)
    assert parser.dialect == "snowflake"

    with pytest.raises(ParseError):
        parser.parse("INVALID SQL @@@ @@@")


def test_parse_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test fallback when sqlglot parse_one returns None."""
    import sqlglot

    monkeypatch.setattr(sqlglot, "parse_one", lambda *args, **kwargs: None)
    with pytest.raises(ParseError, match="No valid Expression node"):
        parse_snowflake_sql("SELECT 1")


def test_generic_sqlglot_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test fallback when sqlglot raises generic SqlglotError."""
    import sqlglot
    from sqlglot.errors import SqlglotError

    def _raise_error(*args: object, **kwargs: object) -> None:
        raise SqlglotError("Internal parser error")

    monkeypatch.setattr(sqlglot, "parse_one", _raise_error)
    with pytest.raises(ParseError, match="SQL parsing failed"):
        parse_snowflake_sql("SELECT 1")

