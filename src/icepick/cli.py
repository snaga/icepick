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
from icepick.diff import apply_unified_diff, format_diff, render_diff, split_hunks
from icepick.exceptions import AuthenticationError, ParseError
from icepick.feedback import FeedbackRecorder
from icepick.linter.base import DiagnosticIssue, Severity
from icepick.linter.engine import LinterEngine
from icepick.linter.rules import (
    CorrelatedSubqueryRule,
    DuplicateTableScanRule,
    ImplicitCrossJoinRule,
    NestedSubqueryRule,
    NonSargableRule,
    RedundantSortRule,
    UnionToUnionAllRule,
)
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


VALID_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

SEVERITY_ORDER: dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def _get_severity_rank(severity: Severity | str) -> int:
    """Return numeric rank for a severity level."""
    val = severity.value if isinstance(severity, Severity) else str(severity)
    return SEVERITY_ORDER.get(val.upper(), 0)


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
    engine.register_rule(CorrelatedSubqueryRule())
    engine.register_rule(RedundantSortRule())
    engine.register_rule(ImplicitCrossJoinRule())
    engine.register_rule(DuplicateTableScanRule())
    engine.register_rule(UnionToUnionAllRule())
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
        issues_data = [issue.to_dict() for issue in issues]
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


@app.command("rewrite")
def rewrite(
    file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the Snowflake SQL file to rewrite.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save the unified diff output to the specified .patch file.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect to use for parsing.",
    ),
    category: str | None = typer.Option(
        None,
        "--category",
        help="Severity category filter (CRITICAL, HIGH, MEDIUM, LOW).",
    ),
    flatten_subqueries: bool = typer.Option(
        False,
        "--flatten-subqueries",
        help="Flatten inline derived tables to top-level CTEs.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output rewrite result as structured JSON to stdout.",
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
    """Optimize Snowflake SQL queries and output Unified Diff (read-only)."""
    dialect = _validate_dialect(dialect)

    target_rank: int | None = None
    if category is not None:
        norm_cat = category.strip().upper()
        if norm_cat not in VALID_SEVERITIES:
            valid_list = ", ".join(VALID_SEVERITIES)
            err_console.print(
                f"[bold red]Error:[/bold red] Invalid category '{category}'. "
                f"Supported severity categories are: {valid_list}."
            )
            err_console.print(
                f"[yellow]Actionable Advice:[/yellow] Specify one of the valid categories: {valid_list}."
            )
            raise typer.Exit(code=1)
        target_rank = SEVERITY_ORDER[norm_cat]

    try:
        original_sql = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        ast = parse_snowflake_sql(original_sql, dialect=dialect)
    except ParseError as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc.message}")
        line_info = f" (Line {exc.line}, Column {exc.col})" if exc.line else ""
        if line_info:
            err_console.print(f"[yellow]Location:{line_info}[/yellow]")
        raise typer.Exit(code=2) from exc

    cfg = _load_config(config)
    engine = _create_engine(cfg)
    issues = engine.diagnose(ast)
    auto_fixable_issues = [i for i in issues if i.can_auto_fix and i.rule_id != "SNOW-007"]

    if target_rank is not None:
        auto_fixable_issues = [
            i for i in auto_fixable_issues if _get_severity_rank(i.severity) >= target_rank
        ]

    patcher = ASTPatcher(dialect=dialect)
    applied_issues: list[DiagnosticIssue] = []
    if auto_fixable_issues:
        ast, applied = patcher.apply_all(ast, auto_fixable_issues)
        applied_issues.extend(applied)

    if flatten_subqueries:
        converter = SubqueryToCTE(dialect=dialect)
        ast, _ = converter.flatten_all_subqueries(ast)

    optimized_sql = ast.sql(dialect=dialect, pretty=True)
    diff_text = format_diff(original_sql, optimized_sql, filename=file.name, dialect=dialect)

    if not diff_text.strip():
        if json_output:
            payload = {
                "file": str(file),
                "has_changes": False,
                "issues_count": 0,
                "diff": "",
                "issues": [],
            }
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if output is not None:
                output.write_text("", encoding="utf-8")
                console.print(
                    f"[bold green]No optimizable issues found. Saved empty patch to {output}[/bold green]"
                )
            else:
                console.print("[bold green]No optimizable issues found.[/bold green]")
        raise typer.Exit(code=0)

    if output is not None:
        output.write_text(diff_text, encoding="utf-8")
        if not json_output:
            console.print(f"[bold green]Saved patch to {output}[/bold green]")
        else:
            err_console.print(f"[bold green]Saved patch to {output}[/bold green]")

    if json_output:
        payload = {
            "file": str(file),
            "has_changes": True,
            "issues_count": len(applied_issues),
            "diff": diff_text,
            "issues": [i.to_dict() for i in applied_issues],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if output is None:
            render_diff(diff_text, console=console)

    raise typer.Exit(code=0)


@app.command("patch")
def patch(
    target_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the Snowflake SQL file to patch.",
    ),
    patch_file: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Optional path to .patch file. If omitted, diff is read from stdin.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Prompt for interactive confirmation before applying each hunk.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate patch application without modifying the target file.",
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
        help="Output patch result as structured JSON to stdout.",
    ),
) -> None:
    """Apply a Unified Diff patch to a Snowflake SQL file."""
    # 1. Read Diff
    if patch_file is not None:
        try:
            diff_text = patch_file.read_text(encoding="utf-8")
        except Exception as exc:
            err_console.print(f"[bold red]Error reading patch file {patch_file}:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc
    else:
        diff_text = sys.stdin.read()

    hunks = split_hunks(diff_text)
    if not diff_text.strip() or not hunks:
        err_console.print("Error: No diff content or hunks found in patch input.")
        raise typer.Exit(code=1)

    # 2. Safety Guard
    if not dry_run and not sys.stdin.isatty() and not force:
        err_console.print(
            "error: Overwriting files in non-interactive environment requires --force flag "
            "(e.g. icepick patch query.sql --force)."
        )
        raise typer.Exit(code=1)

    # 3. Interactive hunk selection
    selected_hunks: list[int] | None = None
    if interactive:
        selected_hunks = []
        for idx, hunk in enumerate(hunks):
            if not json_output:
                console.print(render_diff(hunk.to_text()))
            choice = (
                typer.prompt(
                    "Apply this hunk? [y,n,q]",
                    default="y",
                    show_default=False,
                )
                .strip()
                .lower()
            )
            if choice == "y" or choice.startswith("y"):
                selected_hunks.append(idx)
            elif choice == "q" or choice.startswith("q"):
                break
            # 'n' or other inputs skip this hunk

    # 4. Apply patch
    try:
        original_sql = target_file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading target file {target_file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        patched_text, applied_count, total_count = apply_unified_diff(
            original_sql, diff_text, selected_hunks=selected_hunks
        )
    except ValueError as exc:
        err_console.print(f"[bold red]Error applying patch:[/bold red] {exc}")
        err_console.print(
            "[yellow]Actionable Advice:[/yellow] Ensure the target file has not been modified since the diff was generated, "
            "or re-generate the patch using 'icepick rewrite <file>'."
        )
        raise typer.Exit(code=1) from exc

    # 5. Save changes
    if not dry_run:
        target_file.write_text(patched_text, encoding="utf-8")

    # 6. Output
    if json_output:
        if dry_run:
            status = "dry_run"
        elif applied_count == 0:
            status = "skipped"
        else:
            status = "applied"

        payload = {
            "status": status,
            "file": str(target_file),
            "hunks_applied": applied_count,
            "hunks_total": total_count,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if dry_run:
            console.print(
                f"[yellow]Dry-run: Simulated applying {applied_count}/{total_count} hunk(s) to {target_file}[/yellow]"
            )
        elif applied_count == 0:
            console.print(f"[yellow]No hunks applied (0/{total_count}) to {target_file}[/yellow]")
        else:
            console.print(
                f"[bold green]✓ Successfully applied {applied_count}/{total_count} hunk(s) to {target_file}[/bold green]"
            )

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
