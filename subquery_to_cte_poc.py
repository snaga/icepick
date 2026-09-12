#!/usr/bin/env python3
"""
Snowflake Query Optimizer PoC - Subquery to CTE Extraction
Detects deeply nested inline subqueries (derived tables) in FROM and JOIN clauses,
extracts them into clean top-level named CTEs, and displays a Unified Diff.
"""

import difflib
import sqlglot
from sqlglot import exp
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

SAMPLE_NESTED_SQL = """
SELECT 
    m.store_id,
    m.store_name,
    s.total_sales,
    i.total_inventory_value
FROM (
    SELECT 
        store_id, 
        store_name, 
        region
    FROM analytics.master.stores
    WHERE is_active = TRUE 
      AND region = 'APAC'
) AS m
JOIN (
    SELECT 
        store_id,
        SUM(sales_amount) AS total_sales,
        COUNT(DISTINCT order_id) AS total_orders
    FROM analytics.sales.orders
    WHERE order_date >= '2026-06-01'
    GROUP BY store_id
) AS s ON m.store_id = s.store_id
LEFT JOIN (
    SELECT 
        store_id,
        SUM(quantity * unit_cost) AS total_inventory_value
    FROM analytics.inventory.stock_levels
    WHERE stock_date = CURRENT_DATE()
    GROUP BY store_id
) AS i ON m.store_id = i.store_id
WHERE s.total_sales > 100000;
""".strip()

def extract_subqueries_to_cte(sql_text: str):
    ast = sqlglot.parse_one(sql_text, read="snowflake")
    
    # 1. Base formatted SQL for clean diff
    base_formatted = ast.sql(dialect="snowflake", pretty=True)
    
    # 2. Collect inline subqueries in From and Join clauses
    subqueries_to_extract = []
    
    # Check FROM clause
    from_clause = ast.find(exp.From)
    if from_clause and isinstance(from_clause.this, exp.Subquery):
        subqueries_to_extract.append(("FROM", from_clause.this, "cte_stores_apac"))

    # Check JOIN clauses
    for join in ast.find_all(exp.Join):
        if isinstance(join.this, exp.Subquery):
            alias = join.this.alias
            cte_name = f"cte_sales_summary" if alias == "s" else f"cte_inventory_current"
            subqueries_to_extract.append(("JOIN", join.this, cte_name))

    # 3. Create CTEs and replace in AST
    new_ctes = []
    for clause_type, subquery_node, cte_name in subqueries_to_extract:
        inner_select = subquery_node.this
        alias_name = subquery_node.alias
        
        # Build CTE expression
        cte_node = exp.CTE(
            this=inner_select,
            alias=exp.TableAlias(this=exp.to_identifier(cte_name))
        )
        new_ctes.append(cte_node)
        
        # Replace subquery with simple table reference
        table_ref = exp.Table(
            this=exp.to_identifier(cte_name),
            alias=exp.TableAlias(this=exp.to_identifier(alias_name))
        )
        subquery_node.replace(table_ref)

    # Attach CTEs to top-level Select
    existing_with = ast.args.get("with_")
    if existing_with:
        for c in new_ctes:
            existing_with.append("expressions", c)
    else:
        ast.set("with_", exp.With(expressions=new_ctes))

    optimized_sql = ast.sql(dialect="snowflake", pretty=True)
    return base_formatted, optimized_sql, subqueries_to_extract

def main():
    console.print(Panel.fit(
        "[bold cyan]Snowflake Query Optimizer PoC: Subquery-to-CTE Extraction[/bold cyan]\n"
        "[dim]Refactoring nested derived tables into top-level readable CTE pipeline[/dim]",
        border_style="cyan"
    ))

    base_formatted, optimized_sql, extracted = extract_subqueries_to_cte(SAMPLE_NESTED_SQL)

    # Report table
    table = Table(title=f"Detected Nested Subqueries ({len(extracted)} items extracted)", show_lines=True)
    table.add_column("Location", style="bold yellow", width=12)
    table.add_column("Original Alias", style="cyan", width=15)
    table.add_column("Generated CTE Name", style="bold green", width=25)
    table.add_column("Benefit", style="white")

    for loc, node, cte_name in extracted:
        table.add_row(
            f"{loc} clause",
            node.alias,
            cte_name,
            "Eliminates deep indentation, enables linear reading, simplifies debugging & reuse"
        )
    console.print(table)

    # Generate Unified Diff
    orig_lines = [l + "\n" for l in base_formatted.splitlines()]
    opt_lines = [l + "\n" for l in optimized_sql.splitlines()]
    diff = list(difflib.unified_diff(
        orig_lines,
        opt_lines,
        fromfile="a/models/marts/nested_reporting_query.sql (original)",
        tofile="b/models/marts/nested_reporting_query.sql (CTE extracted)",
        n=3
    ))
    diff_text = "".join(diff)

    # Save artifacts
    patch_path = "Temp/Snowflake_Query_Optimizer_PoC/subquery_to_cte.patch"
    opt_path = "Temp/Snowflake_Query_Optimizer_PoC/nested_reporting_query_optimized.sql"

    with open(patch_path, "w", encoding="utf-8") as f:
        f.write(diff_text)
    with open(opt_path, "w", encoding="utf-8") as f:
        f.write(optimized_sql)

    console.print()
    console.print(Panel("[bold]Unified Diff Preview (git diff compatible):[/bold]", border_style="green"))
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
    console.print(syntax)

    console.print(f"\n[bold cyan]Artifacts saved:[/bold cyan]")
    console.print(f"- Patch file: [underline]{patch_path}[/underline]")
    console.print(f"- Optimized SQL: [underline]{opt_path}[/underline]")

if __name__ == "__main__":
    main()
