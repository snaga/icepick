"""SNOW-004: Implicit Cross Join Detection Rule.

Detects comma-separated tables in a FROM clause that produce an implicit
Cartesian product (cross join) without an explicit JOIN keyword or ON/USING
condition.  Accidental cross joins are one of the most common causes of
runaway query costs in Snowflake because they multiply every row in one
table with every row in another before any WHERE-clause filter is applied.

Example of the anti-pattern::

    SELECT a, b FROM t1, t2          -- implicit cross join!

Preferred form::

    SELECT a, b FROM t1 CROSS JOIN t2   -- intent is explicit
    -- or, if a join predicate exists --
    SELECT a, b FROM t1 JOIN t2 ON t1.id = t2.id

How sqlglot represents this
---------------------------
``sqlglot.parse_one("SELECT a FROM t1, t2")`` produces a ``Select`` node
whose ``joins`` list contains a ``Join`` node that has **only** the ``this``
key populated (the right-hand table reference).  All other join-type signals
— ``kind`` (CROSS / INNER / …), ``side`` (LEFT / RIGHT), ``on`` (predicate),
``using`` (column list), ``method`` (NATURAL) — are absent from ``args``.

This distinguishes an implicit comma join from every explicit join form:

* ``CROSS JOIN t2``               → ``kind='CROSS'``
* ``JOIN t2 ON …``                → ``on=<EQ node>``
* ``JOIN t2 USING (id)``         → ``using=<Tuple node>``
* ``LEFT JOIN t2 ON …``          → ``side='LEFT'``
* ``NATURAL JOIN t2``             → ``method='NATURAL'``

A ``suggested_replacement`` is not provided because rewriting a comma join
correctly requires knowledge of the intended join predicate, which cannot
be determined deterministically from the AST alone.  The issue is flagged
for LLM-assisted rewriting (``requires_llm=True``).
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity

# Keys in Join.args that indicate the join type is explicit.
# If ANY of these keys is present (and non-None), the join is not implicit.
_EXPLICIT_JOIN_KEYS: frozenset[str] = frozenset({"kind", "side", "on", "using", "method"})


def _is_lateral_or_table_function(node: exp.Expression | None) -> bool:
    """Return True if *node* is a LATERAL join or table function (e.g. FLATTEN).

    In Snowflake, constructs such as ``TABLE(FLATTEN(...))`` or
    ``LATERAL FLATTEN(...)`` appear as comma-separated joins in the AST,
    but represent correlated table functions rather than unintended
    Cartesian products between base tables.

    Args:
        node: AST expression to inspect.

    Returns:
        True if the expression is a lateral join or table function.
    """
    if node is None:
        return False
    if isinstance(node, (exp.Lateral, exp.TableFromRows, exp.GenerateSeries)):
        return True
    if node.find(exp.Lateral) is not None:
        return True
    if any(
        isinstance(n, exp.Explode) or (isinstance(n, exp.Anonymous) and n.name.upper() == "FLATTEN")
        for n in node.walk()
    ):
        return True
    raw_sql = node.sql(dialect="snowflake").upper()
    return "FLATTEN" in raw_sql or raw_sql.startswith("TABLE(")


def _is_implicit_cross_join(join: exp.Join) -> bool:
    """Return True iff *join* is an implicit comma-separated cross join.

    sqlglot parses ``FROM t1, t2`` as a ``Join`` node whose ``args`` dict
    contains **only** the ``this`` key.  All explicit join forms set at
    least one additional discriminating key (``kind``, ``side``, ``on``,
    ``using``, or ``method``).  Correlated table functions like ``TABLE(FLATTEN(...))``
    or ``LATERAL`` expressions are excluded.

    Args:
        join: A ``sqlglot.exp.Join`` node to inspect.

    Returns:
        True if the join represents an implicit Cartesian product.
    """
    for key in _EXPLICIT_JOIN_KEYS:
        # args.get returns None for absent keys; treat falsy as absent.
        if join.args.get(key):
            return False
    # Exclude LATERAL joins and table functions (e.g. TABLE(FLATTEN(...)))
    return not _is_lateral_or_table_function(join.this)


class ImplicitCrossJoinRule(BaseRule):
    """Rule SNOW-004: Detects implicit cross joins from comma-separated FROM clauses.

    A comma-separated FROM clause (e.g. ``FROM t1, t2``) generates a full
    Cartesian product between all named tables.  This is almost always
    unintentional and is catastrophic for performance at scale.  Snowflake
    has no special optimisation for implicit cross joins — it will materialise
    the full product before applying any WHERE predicate.
    """

    rule_id: str = "SNOW-004"
    rule_name: str = "Implicit Cross Join"
    severity: Severity = Severity.HIGH
    description: str = (
        "Comma-separated tables in FROM produce an implicit Cartesian product. "
        "This is almost always unintentional and extremely expensive at scale. "
        "Rewrite using an explicit JOIN with an appropriate ON or USING clause."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Traverse *ast* and report every implicit cross-join occurrence.

        The method iterates over every ``exp.Select`` node in the tree
        (including nested subqueries and CTEs) and examines each ``Join``
        in the ``joins`` list.  A join is flagged as implicit when its
        ``args`` dict contains only the ``this`` key — the right-hand table
        reference — with no explicit join type or predicate present.

        Because the correct join predicate cannot be inferred from the AST
        alone, ``requires_llm`` is set to ``True`` and no
        ``suggested_replacement`` is offered.  The ``target_node`` is set to
        the offending ``Join`` node so that a downstream agent or user can
        locate it precisely.

        Args:
            ast: Root or subtree AST expression to diagnose.

        Returns:
            list[DiagnosticIssue]: One issue per implicit cross-join ``Join``
            node detected, or an empty list if none are found.
        """
        issues: list[DiagnosticIssue] = []

        for select_node in ast.find_all(exp.Select):
            joins: list[exp.Join] = select_node.args.get("joins") or []
            for join in joins:
                if not isinstance(join, exp.Join):
                    continue
                if not _is_implicit_cross_join(join):
                    continue

                # Retrieve line number from parser metadata when available.
                line_num: int | None = None
                meta_dict: Any = getattr(join, "meta", None)
                if isinstance(meta_dict, dict):
                    line_num = meta_dict.get("line")

                # The right-hand table expression (Table, Subquery, etc.)
                rhs: exp.Expression = join.this
                rhs_name: str = rhs.sql(dialect="snowflake")

                # Try to get the left-hand (FROM) table name for a clearer message.
                # Note: sqlglot stores this under the key 'from_' (with trailing underscore).
                from_node: exp.From | None = select_node.args.get("from_")
                lhs_name: str = "<unknown>"
                if from_node is not None and from_node.this is not None:
                    lhs_name = from_node.this.sql(dialect="snowflake") or "<unknown>"

                description = (
                    f"Implicit cross join detected between "
                    f"'{lhs_name}' and '{rhs_name}'. "
                    f"The comma-separated FROM clause produces a full Cartesian "
                    f"product ({lhs_name} × {rhs_name}) before any WHERE filter "
                    f"is applied, which can be extremely expensive. "
                    f"Rewrite using an explicit JOIN … ON or JOIN … USING clause."
                )

                issues.append(
                    DiagnosticIssue(
                        rule_id=self.rule_id,
                        rule_name=self.rule_name,
                        severity=self.severity,
                        description=description,
                        target_node=join,
                        snippet=join.sql(dialect="snowflake"),
                        line_number=line_num,
                        # Cannot determine the correct predicate automatically;
                        # delegate rewriting to an LLM agent.
                        suggested_replacement=None,
                        requires_llm=True,
                    )
                )

        return issues
