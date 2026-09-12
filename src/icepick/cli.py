"""CLI interface for Icepick for Snowflake.

Provides typer-based command-line commands for linting and optimizing Snowflake SQL queries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from icepick import __version__
from icepick.agent_context import get_agent_context
from icepick.config import Config
from icepick.diff.formatter import format_diff, render_diff
from icepick.exceptions import AuthenticationError, ParseError
from icepick.feedback import FeedbackRecorder
from icepick.linter.base import Severity
from icepick.linter.engine import LinterEngine
from icepick.linter.rules.snow_001_sargable import NonSargableRule
from icepick.linter.rules.snow_003_sort import RedundantSortRule
from icepick.linter.rules.snow_007_nested_subquery import NestedSubqueryRule
from icepick.parser import parse_snowflake_sql
from icepick.patcher.in_place import ASTPatcher
from icepick.patcher.subquery_to_cte import SubqueryToCTE
from icepick.security.credentials import resolve_credential
from icepick.verifier.equivalence import EquivalenceVerifier

app = typer.Typer(
    name="icepick",
    help="Icepick for Snowflake - Deterministic AST Query Optimizer & Linter.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True, soft_wrap=True)

VALID_DIALECTS: tuple[str, ...] = ("snowflake", "postgres", "duckdb", "bigquery")


def _validate_dialect(dialect: str) -> str:
    """Validate that the specified dialect is supported.

    Args:
        dialect: SQL dialect name.

    Returns:
        The normalized lowercase dialect string.

    Raises:
        typer.Exit: If dialect is not supported.
    """
    normalized = dialect.strip().lower()
    if normalized not in VALID_DIALECTS:
        valid_list = ", ".join(f"'{d}'" for d in VALID_DIALECTS)
        err_console.print(
            f"error: Invalid dialect '{dialect}'. Supported dialects are: {valid_list}."
        )
        raise typer.Exit(code=1)
    return normalized


def version_callback(value: bool) -> None:
    """Print the version of icepick and exit."""
    if value:
        typer.echo(f"icepick version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Icepick: SQL optimizer and linter for Snowflake."""


def _load_config(config_path: Path | None) -> Config:
    """Load Config from file or fallback to environment variables."""
    if config_path is not None:
        content = config_path.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            import tomllib  # type: ignore[import-not-found]

            data = tomllib.loads(content)
        return Config.from_dict(data)
    return Config.from_env()


def _create_engine(cfg: Config) -> LinterEngine:
    """Create and configure LinterEngine with default rules."""
    engine = LinterEngine(config=cfg)
    engine.register_rule(NonSargableRule())
    engine.register_rule(RedundantSortRule())
    engine.register_rule(NestedSubqueryRule())
    return engine


@app.command("check")
def check(
    file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the Snowflake SQL file to check.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect to use for parsing.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to configuration file (.json or .toml).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output diagnostic issues as structured JSON array to stdout.",
    ),
) -> None:
    """Check a SQL file for performance anti-patterns and issues."""
    dialect = _validate_dialect(dialect)

    try:
        sql_text = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        ast = parse_snowflake_sql(sql_text, dialect=dialect)
    except ParseError as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc.message}")
        line_info = f" (Line {exc.line}, Column {exc.col})" if exc.line else ""
        if line_info:
            err_console.print(f"[yellow]Location:{line_info}[/yellow]")
        raise typer.Exit(code=2) from exc

    cfg = _load_config(config)
    engine = _create_engine(cfg)
    issues = engine.diagnose(ast)

    if json_output:
        issues_data = [
            {
                "rule_id": issue.rule_id,
                "rule_name": issue.rule_name,
                "severity": (
                    issue.severity.value
                    if isinstance(issue.severity, Severity)
                    else str(issue.severity)
                ),
                "line": issue.line_number,
                "description": issue.description,
                "snippet": issue.snippet,
                "can_auto_fix": issue.can_auto_fix,
            }
            for issue in issues
        ]
        typer.echo(json.dumps(issues_data, ensure_ascii=False, indent=2))
        if not issues:
            raise typer.Exit(code=0)
        raise typer.Exit(code=1)

    if not issues:
        console.print("[bold green]✓ No issues found.[/bold green]")
        raise typer.Exit(code=0)

    table = Table(title=f"Diagnostic Report: {file.name}", header_style="bold cyan")
    table.add_column("Rule ID", style="bold")
    table.add_column("Rule Name")
    table.add_column("Severity")
    table.add_column("Line")
    table.add_column("Description")

    for issue in issues:
        severity_val = (
            issue.severity.value if isinstance(issue.severity, Severity) else str(issue.severity)
        )
        if severity_val in {"CRITICAL", "HIGH"}:
            severity_style = f"[bold red]{severity_val}[/bold red]"
        elif severity_val == "MEDIUM":
            severity_style = f"[yellow]{severity_val}[/yellow]"
        else:
            severity_style = f"[cyan]{severity_val}[/cyan]"

        line_str = str(issue.line_number) if issue.line_number is not None else "-"
        table.add_row(issue.rule_id, issue.rule_name, severity_style, line_str, issue.description)

    console.print(table)
    console.print(f"\n[bold red]Found {len(issues)} issue(s).[/bold red]")
    raise typer.Exit(code=1)


@app.command("fix")
def fix(
    file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the Snowflake SQL file to fix.",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        help="Show unified diff of changes in terminal.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        "-w",
        help="Overwrite the original SQL file with optimized code.",
    ),
    patch: Path | None = typer.Option(
        None,
        "--patch",
        "-p",
        help="Save the unified diff output to the specified .patch file.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Prompt for interactive confirmation before applying each fix.",
    ),
    flatten_subqueries: bool = typer.Option(
        False,
        "--flatten-subqueries",
        help="Flatten inline derived tables to top-level CTEs.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate fixes without modifying files or saving patches.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force overwrite files in non-interactive environments.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output fix result as structured JSON to stdout.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect to use for parsing.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to configuration file (.json or .toml).",
    ),
) -> None:
    """Fix detected issues in a SQL file and optionally format / flatten queries."""
    dialect = _validate_dialect(dialect)

    if write and not dry_run and not sys.stdin.isatty() and not force:
        err_console.print(
            "error: Overwriting files in non-interactive environment requires --force flag, or use --patch to output a patch file."
        )
        raise typer.Exit(code=1)

    try:
        original_sql = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        ast = parse_snowflake_sql(original_sql, dialect=dialect)
    except ParseError as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc.message}")
        raise typer.Exit(code=2) from exc

    cfg = _load_config(config)
    engine = _create_engine(cfg)
    issues = engine.diagnose(ast)
    auto_fixable_issues = [i for i in issues if i.can_auto_fix and i.rule_id != "SNOW-007"]

    patcher = ASTPatcher(dialect=dialect)
    fixed_rule_ids: list[str] = []

    if interactive:
        for issue in auto_fixable_issues:
            backup_ast = ast.copy()
            current_sql = ast.sql(dialect=dialect, pretty=True)
            try:
                ast = patcher.apply_issue(ast, issue)
                new_sql = ast.sql(dialect=dialect, pretty=True)
                hunk_diff = format_diff(current_sql, new_sql, filename=file.name, dialect=dialect)
                if hunk_diff.strip():
                    if not json_output:
                        console.print(render_diff(hunk_diff))
                    choice = (
                        typer.prompt(f"Apply fix for {issue.rule_id}? [y/n/q]", default="y")
                        .strip()
                        .lower()
                    )
                    if choice == "y":
                        fixed_rule_ids.append(issue.rule_id)
                    elif choice == "q":
                        ast = backup_ast
                        break
                    else:  # "n" or anything else
                        ast = backup_ast
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[yellow]Skipped fix for {issue.rule_id} due to error: {exc}[/yellow]"
                )
                ast = backup_ast

        if flatten_subqueries:
            backup_ast = ast.copy()
            current_sql = ast.sql(dialect=dialect, pretty=True)
            try:
                converter = SubqueryToCTE()
                ast, _ = converter.flatten_all_subqueries(ast)
                new_sql = ast.sql(dialect=dialect, pretty=True)
                diff_sub = format_diff(current_sql, new_sql, filename=file.name, dialect=dialect)
                if diff_sub.strip():
                    if not json_output:
                        console.print(render_diff(diff_sub))
                    choice = (
                        typer.prompt("Apply subquery flattening to CTE? [y/n/q]", default="y")
                        .strip()
                        .lower()
                    )
                    if choice == "y":
                        fixed_rule_ids.append("SNOW-007")
                    else:
                        ast = backup_ast
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[yellow]Skipped subquery flattening due to error: {exc}[/yellow]"
                )
                ast = backup_ast
    else:
        # Non-interactive batch application
        if auto_fixable_issues:
            ast, applied = patcher.apply_all(ast, auto_fixable_issues)
            fixed_rule_ids.extend([issue.rule_id for issue in applied])

        if flatten_subqueries:
            converter = SubqueryToCTE()
            ast, _ = converter.flatten_all_subqueries(ast)
            # If flattened subqueries were extracted, add SNOW-007
            fixed_rule_ids.append("SNOW-007")

    optimized_sql = ast.sql(dialect=dialect, pretty=True)
    full_diff = format_diff(original_sql, optimized_sql, filename=file.name, dialect=dialect)

    if not full_diff.strip():
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "status": "unchanged",
                        "file": str(file),
                        "issues_fixed": [],
                        "diff": "",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print("[bold green]No modifications needed.[/bold green]")
        raise typer.Exit(code=0)

    # Save patch if requested and not in dry-run mode
    if patch is not None and not dry_run:
        patch.write_text(full_diff, encoding="utf-8")
        if not json_output:
            console.print(f"[bold green]Saved patch to {patch}[/bold green]")
        else:
            err_console.print(f"[bold green]Saved patch to {patch}[/bold green]")

    # Write modified file if requested and not in dry-run mode
    if write and not dry_run:
        file.write_text(optimized_sql + "\n", encoding="utf-8")
        if not json_output:
            console.print(f"[bold green]Successfully updated {file}[/bold green]")
        else:
            err_console.print(f"[bold green]Successfully updated {file}[/bold green]")

    if json_output:
        output_payload = {
            "status": "optimized",
            "file": str(file),
            "issues_fixed": fixed_rule_ids,
            "diff": full_diff,
        }
        typer.echo(json.dumps(output_payload, ensure_ascii=False, indent=2))
    else:
        # Show diff if --diff is explicitly set, --dry-run is set, or neither --write nor --patch is specified
        if diff or dry_run or (not write and patch is None):
            console.print(render_diff(full_diff))

    raise typer.Exit(code=0)


@app.command("verify")
def verify(
    original_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the original Snowflake SQL file.",
    ),
    optimized_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the optimized Snowflake SQL file.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Output the generated bidirectional EXCEPT verification SQL query without connecting to Snowflake.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect to use.",
    ),
) -> None:
    """Verify deterministic equivalence between original and optimized queries using EXCEPT."""
    dialect = _validate_dialect(dialect)
    try:
        orig_sql = original_file.read_text(encoding="utf-8")
        opt_sql = optimized_file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading files:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    verifier = EquivalenceVerifier(dialect=dialect)
    verification_sql = verifier.build_verification_query(orig_sql, opt_sql, dialect=dialect)

    if dry_run:
        console.print("[bold cyan]Generated Verification SQL (Dry Run):[/bold cyan]")
        syntax = Syntax(verification_sql, "sql", theme="monokai", line_numbers=True, word_wrap=True)
        console.print(syntax)
        raise typer.Exit(code=0)

    # Securely resolve Snowflake credentials
    try:
        resolve_credential("snowflake_password")
    except AuthenticationError as exc:
        err_console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    console.print(
        "[yellow]Direct Snowflake execution with resolved credentials requires an active connection session. "
        "Use --dry-run to display the verification query.[/yellow]"
    )
    raise typer.Exit(code=0)


@app.command("feedback")
def feedback(
    message: str = typer.Argument(
        ...,
        help="Feedback message describing friction, bug, documentation issue, or idea.",
    ),
    category: str = typer.Option(
        "friction",
        "--category",
        "-c",
        help="Feedback category ('friction', 'bug', 'doc', 'idea').",
    ),
    log_file: Path | None = typer.Option(
        None,
        "--log-file",
        help="Path to feedback JSON Lines log file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output the recorded feedback entry in JSON format to stdout.",
    ),
) -> None:
    """Record friction, bug, doc feedback, or idea into a local JSON Lines log."""
    recorder = FeedbackRecorder(log_path=log_file)
    try:
        entry = recorder.record(message=message, category=category)
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        err_console.print(
            f"[bold red]File Error:[/bold red] Failed to write feedback to '{recorder.log_path}': {exc}"
        )
        err_console.print(
            "[yellow]Actionable Advice:[/yellow] Ensure the target directory exists and has write permissions, "
            "or specify an alternate file path using '--log-file <path>'."
        )
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
    else:
        console.print(
            f"[bold green]✓ Feedback recorded successfully to {recorder.log_path}[/bold green]",
            soft_wrap=True,
        )
    raise typer.Exit(code=0)


@app.command("agent-context")
def agent_context(
    json_output: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Output introspected CLI specification in formatted JSON (default: True).",
    ),
) -> None:
    """Introspect CLI capabilities, rules, commands, and environment schema as structured JSON."""
    ctx = get_agent_context()
    if json_output:
        typer.echo(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        console.print(f"[bold cyan]{ctx['name']}[/bold cyan] v{ctx['version']}")
        console.print(f"{ctx['description']}")
    raise typer.Exit(code=0)
