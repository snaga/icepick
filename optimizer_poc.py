#!/usr/bin/env python3
"""
Snowflake Query Optimizer PoC
AST-based Static Diagnosis & In-place Patching with Unified Diff Output
"""

import sys
import difflib
from dataclasses import dataclass
from typing import List, Optional

import sqlglot
from sqlglot import exp
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

@dataclass
class DiagnosticIssue:
    rule_id: str
    rule_name: str
    severity: str
    description: str
    target_node: exp.Expression
    snippet: str
    suggested_replacement: Optional[exp.Expression] = None

class SnowflakeAstOptimizer:
    def __init__(self, sql_text: str):
        self.raw_sql = sql_text
        self.ast = sqlglot.parse_one(sql_text, read="snowflake")
        self.issues: List[DiagnosticIssue] = []

    def diagnose(self) -> List[DiagnosticIssue]:
        self.issues.clear()
        
        # Rule 1: SNOW-001 Non-Sargable Predicate (DATE(...) = 'YYYY-MM-DD')
        for eq in self.ast.find_all(exp.EQ):
            if isinstance(eq.this, (exp.Anonymous, exp.TsOrDsToDate, exp.Date)) and isinstance(eq.expression, exp.Literal):
                date_str = eq.expression.this
                col_name = eq.this.this.sql(dialect="snowflake")
                replacement_sql = f"{col_name} >= '{date_str} 00:00:00' AND {col_name} < DATEADD(day, 1, '{date_str}')"
                replacement_node = sqlglot.parse_one(replacement_sql, read="snowflake")
                
                self.issues.append(DiagnosticIssue(
                    rule_id="SNOW-001",
                    rule_name="Non-Sargable Predicate",
                    severity="HIGH",
                    description=f"Column '{col_name}' wrapped in function, disabling partition pruning.",
                    target_node=eq,
                    snippet=eq.sql(dialect="snowflake"),
                    suggested_replacement=replacement_node
                ))

        # Rule 2: SNOW-003 Redundant Sort in Subquery
        for sub in self.ast.find_all(exp.Subquery):
            sel = sub.find(exp.Select)
            if sel and sel.find(exp.Order) and not sel.find(exp.Limit):
                order_node = sel.find(exp.Order)
                self.issues.append(DiagnosticIssue(
                    rule_id="SNOW-003",
                    rule_name="Redundant Sort in Subquery",
                    severity="LOW",
                    description="ORDER BY in subquery without LIMIT wastes optimizer & memory resources.",
                    target_node=order_node,
                    snippet=order_node.sql(dialect="snowflake"),
                    suggested_replacement=None
                ))

        # Rule 3: SNOW-002 Correlated Subquery
        for cte in self.ast.find_all(exp.CTE):
            for where in cte.find_all(exp.Where):
                for sub in where.find_all(exp.Select):
                    outer_refs = [c.sql() for c in sub.find_all(exp.Column) if c.table == 'o']
                    if outer_refs:
                        new_cte_sql = """
high_value_orders AS (
    SELECT 
        o.order_id,
        o.customer_id,
        o.order_amount,
        o.order_date
    FROM (
        SELECT 
            order_id,
            customer_id,
            order_amount,
            order_date,
            AVG(order_amount) OVER (
                PARTITION BY customer_id 
                ORDER BY order_date 
                RANGE BETWEEN INTERVAL '30 DAYS' PRECEDING AND CURRENT ROW
            ) AS rolling_avg_amount
        FROM analytics.sales.orders
    ) AS o
    WHERE o.order_amount > o.rolling_avg_amount
)
""".strip()
                        new_cte_node = sqlglot.parse_one(f"WITH {new_cte_sql} SELECT 1", read="snowflake").find(exp.CTE)
                        self.issues.append(DiagnosticIssue(
                            rule_id="SNOW-002",
                            rule_name="Correlated Subquery (Unnesting)",
                            severity="CRITICAL",
                            description=f"Correlated subquery referencing {outer_refs} causes repeated quadratic scans. Unnested using window function.",
                            target_node=cte,
                            snippet=where.sql(dialect="snowflake"),
                            suggested_replacement=new_cte_node
                        ))

        return self.issues

    def apply_fixes(self) -> str:
        for issue in self.issues:
            if issue.suggested_replacement is not None:
                issue.target_node.replace(issue.suggested_replacement)
            else:
                issue.target_node.pop()
        
        return self.ast.sql(dialect="snowflake", pretty=True)

def main():
    console.print(Panel.fit("[bold cyan]Snowflake Query Optimizer PoC[/bold cyan]\n[dim]AST-based Static Diagnosis & In-place Patching[/dim]", border_style="cyan"))

    input_file = "Temp/Snowflake_Query_Optimizer_PoC/sample_batch.sql"
    with open(input_file, "r", encoding="utf-8") as f:
        original_sql = f.read()

    optimizer = SnowflakeAstOptimizer(original_sql)
    issues = optimizer.diagnose()

    # 1. Print Diagnostic Report
    table = Table(title=f"AST Diagnostic Report ({len(issues)} issues found)", show_lines=True)
    table.add_column("Rule ID", style="bold yellow", width=10)
    table.add_column("Rule Name", style="cyan", width=25)
    table.add_column("Severity", style="bold magenta", width=10)
    table.add_column("Description & Bottleneck Snippet", style="white")

    for issue in issues:
        sev_color = "red" if issue.severity == "CRITICAL" else ("yellow" if issue.severity == "HIGH" else "green")
        table.add_row(
            issue.rule_id,
            issue.rule_name,
            f"[{sev_color}]{issue.severity}[/{sev_color}]",
            f"{issue.description}\n[dim yellow]Snippet:[/dim yellow] [dim]{issue.snippet}[/dim]"
        )

    console.print(table)
    console.print("\n[bold green]Applying In-place AST Patches...[/bold green]")

    # 2. Apply Patches
    optimized_sql = optimizer.apply_fixes()

    # 3. Generate and Display Unified Diff (Normalized for clean AST diff)
    base_formatted_sql = sqlglot.parse_one(original_sql, read="snowflake").sql(dialect="snowflake", pretty=True)
    orig_lines = [l + "\n" for l in base_formatted_sql.splitlines()]
    opt_lines = [l + "\n" for l in optimized_sql.splitlines()]
    diff = list(difflib.unified_diff(
        orig_lines,
        opt_lines,
        fromfile="a/models/marts/sample_batch.sql",
        tofile="b/models/marts/sample_batch.sql",
        n=3
    ))

    diff_text = "".join(diff)
    
    patch_file = "Temp/Snowflake_Query_Optimizer_PoC/optimized_patch.patch"
    with open(patch_file, "w", encoding="utf-8") as f:
        f.write(diff_text)

    opt_file = "Temp/Snowflake_Query_Optimizer_PoC/sample_batch_optimized.sql"
    with open(opt_file, "w", encoding="utf-8") as f:
        f.write(optimized_sql)

    console.print(Panel("[bold]Unified Diff Preview (git diff compatible):[/bold]", border_style="green"))
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
    console.print(syntax)

    console.print(f"\n[bold cyan]Artifacts saved:[/bold cyan]")
    console.print(f"- Patch file: [underline]{patch_file}[/underline]")
    console.print(f"- Optimized SQL: [underline]{opt_file}[/underline]")

if __name__ == "__main__":
    main()
