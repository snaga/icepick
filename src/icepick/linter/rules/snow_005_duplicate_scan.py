"""SNOW-005: Duplicate Table Scan Detection Rule.

Detects cases where the same base table is referenced in two or more CTE
definitions within the same query.  Each redundant scan forces Snowflake to
read and process the table's micro-partitions independently for every CTE,
multiplying I/O and compute costs unnecessarily.

Example of the anti-pattern::

    WITH
      cte1 AS (SELECT * FROM orders),
      cte2 AS (SELECT id FROM orders WHERE status = 'open')
    SELECT *
    FROM cte1
    JOIN cte2 ON cte1.id = cte2.id

Both ``cte1`` and ``cte2`` scan the ``orders`` table independently.

Preferred form::

    WITH
      all_orders AS (SELECT * FROM orders),          -- single scan
      open_orders AS (SELECT id FROM all_orders WHERE status = 'open')
    SELECT *
    FROM all_orders
    JOIN open_orders ON all_orders.id = open_orders.id

Because rewriting overlapping CTE scans into a shared base CTE requires
knowledge of the query's full semantics, this rule sets ``requires_llm=True``
and leaves ``suggested_replacement=None``.

How sqlglot represents this
---------------------------
``sqlglot.parse_one(sql)`` for a WITH-query returns a ``Select`` node whose
``with`` argument is a ``exp.With`` node.  The ``With`` node's ``expressions``
list contains one ``exp.CTE`` node per CTE definition.

Each ``exp.CTE`` node has:

* ``alias``   — the CTE name (a string, e.g. ``'cte1'``)
* ``this``    — the body ``exp.Select`` (or ``exp.Union``) expression

Tables referenced *inside* the body are found via ``cte.find_all(exp.Table)``.
The CTE alias itself does **not** appear as an ``exp.Table`` node inside the
body — it lives in ``cte.args['alias']`` (a ``TableAlias`` node).
"""

from __future__ import annotations

from collections import defaultdict

from sqlglot import exp

from icepick.linter.base import BaseRule, DiagnosticIssue, Severity


def _table_key(table: exp.Table) -> str:
    """Return a normalised, case-insensitive key for *table* without aliases.

    Uses ``exp.table_name(table)`` to produce a fully-qualified name (including
    schema and catalog when present, but excluding table aliases), then converts
    it to lower-case so that ``orders o1``, ``orders AS o2``, ``ORDERS``, and
    ``orders`` are treated as the same table.

    Args:
        table: An ``exp.Table`` AST node to normalise.

    Returns:
        Lower-cased SQL representation of the table reference without alias.
    """
    name = exp.table_name(table)
    if not name:
        return table.name.lower() if table.name else table.sql().lower()
    return name.lower()


class DuplicateTableScanRule(BaseRule):
    """Rule SNOW-005: Detects the same base table scanned in multiple CTEs.

    When two or more CTE definitions within the same ``WITH`` clause reference
    the same underlying table, Snowflake executes a separate scan for each
    CTE.  Consolidating duplicate scans into a single shared base CTE
    eliminates redundant I/O and reduces overall query cost.

    Detection logic
    ---------------
    1. Locate the top-level ``exp.With`` node.
    2. For each ``exp.CTE`` in that ``With``, collect every ``exp.Table``
       referenced inside the CTE body (via ``find_all``).
    3. Build a mapping from (normalised) table name → list of CTE names that
       reference it.
    4. Emit one ``DiagnosticIssue`` per table that is referenced by ≥ 2 CTEs.
    """

    rule_id: str = "SNOW-005"
    rule_name: str = "Duplicate Table Scan"
    severity: Severity = Severity.MEDIUM
    description: str = (
        "The same base table is scanned independently in multiple CTE definitions. "
        "Each redundant scan incurs separate I/O and compute overhead. "
        "Consider refactoring into a single shared base CTE that the other CTEs "
        "reference, so the table is scanned only once."
    )

    def check(self, ast: exp.Expression) -> list[DiagnosticIssue]:
        """Analyse *ast* and report base tables scanned in multiple CTEs.

        The check is scoped to the top-level ``exp.With`` node only, so
        nested sub-queries with their own ``WITH`` clauses are not conflated
        with the outer query's CTEs.  This avoids false positives from
        independent sub-query scopes.

        One ``DiagnosticIssue`` is generated **per duplicate table**, not per
        CTE occurrence, to keep the report concise.  The ``target_node`` is
        set to the first ``exp.CTE`` node that references the duplicated table.

        Args:
            ast: Root or subtree AST expression to diagnose.

        Returns:
            list[DiagnosticIssue]: One issue per base table scanned by ≥ 2
            CTEs, or an empty list if no duplicates are detected.
        """
        issues: list[DiagnosticIssue] = []

        # Locate the top-level With node.  find() performs a depth-first
        # search and returns the first match, which is the outermost WITH.
        with_node = ast.find(exp.With)
        if with_node is None:
            return issues

        # Collect all CTE definitions from the With node.
        cte_list: list[exp.CTE] = [
            node for node in with_node.expressions if isinstance(node, exp.CTE)
        ]
        if len(cte_list) < 2:
            # With only one CTE there can be no duplication.
            return issues

        # Build a set of CTE alias names (lower-cased) so that table references
        # to a sibling CTE are not mistakenly flagged as base-table scans.
        cte_aliases: set[str] = {cte.alias.lower() for cte in cte_list}

        # Map: normalised table key → list of (cte_name, cte_node) pairs
        table_to_ctes: dict[str, list[tuple[str, exp.CTE]]] = defaultdict(list)

        for cte in cte_list:
            # find_all(exp.Table) on the CTE body collects every table reference
            # inside that CTE, including those in sub-selects.  We deliberately
            # do NOT descend into nested WITH bodies to avoid conflation.
            for table in cte.find_all(exp.Table):
                key = _table_key(table)
                # Skip references to sibling CTE aliases — those are not
                # independent base-table scans.
                if key in cte_aliases:
                    continue
                # Record that this CTE references `key`.
                # Use a set to avoid counting the same table twice within a
                # single CTE (e.g. a self-join inside a CTE body).
                existing_cte_names = [name for name, _ in table_to_ctes[key]]
                if cte.alias not in existing_cte_names:
                    table_to_ctes[key].append((cte.alias, cte))

        # Emit one issue per table referenced by 2+ CTEs.
        for table_key, cte_refs in table_to_ctes.items():
            if len(cte_refs) < 2:
                continue

            cte_names = [name for name, _ in cte_refs]
            first_cte_node = cte_refs[0][1]

            # Try to recover a line number from AST metadata.
            line_num: int | None = None
            meta = getattr(first_cte_node, "meta", None)
            if isinstance(meta, dict):
                line = meta.get("line")
                if isinstance(line, int):
                    line_num = line

            description = (
                f"Base table '{table_key}' is scanned independently in "
                f"{len(cte_refs)} CTEs: "
                f"{', '.join(repr(n) for n in cte_names)}. "
                f"Each CTE triggers a separate table scan, multiplying I/O and "
                f"compute costs. Refactor by extracting a single shared base CTE "
                f"that reads '{table_key}' once, then have the other CTEs filter "
                f"or transform from that shared CTE."
            )

            issues.append(
                DiagnosticIssue(
                    rule_id=self.rule_id,
                    rule_name=self.rule_name,
                    severity=self.severity,
                    description=description,
                    target_node=first_cte_node,
                    snippet=first_cte_node.sql(dialect="snowflake"),
                    line_number=line_num,
                    # Rewriting requires LLM reasoning about query semantics.
                    suggested_replacement=None,
                    requires_llm=True,
                )
            )

        return issues
