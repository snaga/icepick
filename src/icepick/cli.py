"""CLI interface for Icepick for Snowflake.

Provides typer-based command-line commands for linting and optimizing Snowflake SQL queries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import sqlglot.errors
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from icepick import __version__
from icepick.agent_context import get_agent_context
from icepick.config import Config, ConfigResolver, RuntimeConfigSummary
from icepick.diff import render_diff
from icepick.exceptions import ParseError
from icepick.feedback import FeedbackRecorder
from icepick.linter.base import Severity
from icepick.linter.engine import LinterEngine
from icepick.prescription import PrescriptionEngine, PrescriptionPlan
from icepick.verifier.equivalence import generate_verification_sql

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


def _load_config(
    config_path: Path | None = None,
    cli_args: dict[str, Any] | None = None,
) -> tuple[Config, RuntimeConfigSummary]:
    """Load and resolve Config cascading across CLI, Env, File, and Defaults."""
    resolver = ConfigResolver(
        cli_args=cli_args,
        config_file=config_path,
    )
    return resolver.resolve()



def _create_engine(cfg: Config) -> LinterEngine:
    """Create and configure LinterEngine with default rules."""
    return LinterEngine(config=cfg)


@app.command("diag")
def diag(
    file: Path = typer.Argument(
        ...,
        help="Path to Snowflake SQL file to diagnose.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: 'text' (human-readable cards) or 'json' (machine-readable plan).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Shorthand for --format json.",
    ),
    severity: str | None = typer.Option(
        None,
        "--severity",
        "-s",
        help="Filter prescriptions by severity threshold ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW').",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect (default: snowflake).",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Diagnose Snowflake SQL and generate actionable optimization prescriptions."""
    valid_dialect = _validate_dialect(dialect)

    norm_format = format.strip().lower()
    if norm_format not in ("text", "json"):
        err_console.print(
            f"[bold red]Error:[/bold red] Invalid format '{format}'. Supported formats are: 'text', 'json'."
        )
        raise typer.Exit(code=1)

    min_rank: int | None = None
    if severity is not None:
        norm_sev = severity.strip().upper()
        if norm_sev not in VALID_SEVERITIES:
            valid_list = ", ".join(VALID_SEVERITIES)
            err_console.print(
                f"[bold red]Error:[/bold red] Invalid severity '{severity}'. "
                f"Supported severity thresholds are: {valid_list}."
            )
            err_console.print(
                f"[yellow]Actionable Advice:[/yellow] Specify one of the valid severity thresholds: {valid_list}."
            )
            raise typer.Exit(code=1)
        min_rank = SEVERITY_ORDER[norm_sev]

    try:
        sql_text = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        cfg, _ = _load_config(config, cli_args={"dialect": valid_dialect})
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    linter_engine = _create_engine(cfg)
    engine = PrescriptionEngine(linter_engine=linter_engine, dialect=valid_dialect)

    try:
        plan: PrescriptionPlan = engine.diagnose(sql_text, file_path=str(file))
    except (ParseError, sqlglot.errors.ParseError) as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc}")
        line_info = getattr(exc, "line", None)
        col_info = getattr(exc, "col", None)
        if line_info is not None:
            err_console.print(f"[yellow]Location: Line {line_info}, Column {col_info}[/yellow]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        err_console.print(f"[bold red]Error during diagnosis:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    if min_rank is not None:
        plan.prescriptions = [
            rx for rx in plan.prescriptions if _get_severity_rank(rx.severity) >= min_rank
        ]
        plan.issues_count = len(plan.prescriptions)

    is_json = json_output or norm_format == "json"

    if is_json:
        typer.echo(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        if plan.issues_count == 0:
            raise typer.Exit(code=0)
        raise typer.Exit(code=1)

    if plan.issues_count == 0:
        console.print(f"[green][OK] No optimization issues found in {file}. Clean query![/green]")
        raise typer.Exit(code=0)

    console.print(
        f"[bold cyan]Prescription Plan:[/bold cyan] {plan.issues_count} optimization prescriptions found in {file}\n"
    )

    for rx in plan.prescriptions:
        severity_val = rx.severity.value if isinstance(rx.severity, Severity) else str(rx.severity)
        if severity_val in {"CRITICAL", "HIGH"}:
            severity_color = "bold red"
        elif severity_val == "MEDIUM":
            severity_color = "yellow"
        else:
            severity_color = "cyan"

        panel_title = (
            f"[bold yellow]{rx.id}[/bold yellow] "
            f"[{severity_color}]{severity_val}[/{severity_color}] - "
            f"Rule: [bold]{rx.rule_id}[/bold] "
            f"(Action: [magenta]{rx.action.value}[/magenta])"
        )

        cte_label = rx.target.cte or "(top-level query)"
        node_label = rx.target.node_type
        if rx.target.line_range:
            start_l, end_l = rx.target.line_range
            line_label = f"L{start_l}" if start_l == end_l else f"L{start_l}-L{end_l}"
        else:
            line_label = "N/A"

        body_lines = [
            f"[bold]CTE:[/bold] {cte_label}",
            f"[bold]Node:[/bold] {node_label}",
            f"[bold]Line:[/bold] {line_label}",
            f"[bold]Original SQL:[/bold] [red]{escape(rx.original_sql)}[/red]",
        ]

        if rx.action.value == "DELETE":
            body_lines.append("[bold]Suggested SQL:[/bold] [dim](Remove node)[/dim]")
        elif rx.suggested_sql:
            body_lines.append(
                f"[bold]Suggested SQL:[/bold] [green]{escape(rx.suggested_sql)}[/green]"
            )
        else:
            body_lines.append("[bold]Suggested SQL:[/bold] [dim](Manual rewrite recommended)[/dim]")

        body_lines.append(f"[bold]Rationale:[/bold] {escape(rx.rationale)}")
        body_lines.append(
            f"[bold]Expected Impact:[/bold] [italic]{escape(rx.expected_impact)}[/italic]"
        )

        content = "\n".join(body_lines)
        console.print(Panel(content, title=panel_title, border_style="cyan"))

    raise typer.Exit(code=1)


@app.command("diff")
def diff(
    file: Path = typer.Argument(
        ...,
        help="Path to Snowflake SQL file to diff.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    rx: str | None = typer.Option(
        None,
        "--rx",
        help="Comma-separated prescription IDs to apply (e.g. 'RX-001,RX-003'). If omitted, all prescriptions are applied.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save the generated unified diff to a .patch file instead of stdout.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect (default: snowflake).",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file (.json or .toml).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Generate minimal source-preserving Unified Diff for specified prescriptions."""
    valid_dialect = _validate_dialect(dialect)

    try:
        sql_text = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        cfg, _ = _load_config(config, cli_args={"dialect": valid_dialect})
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    selected_ids = [s.strip() for s in rx.split(",") if s.strip()] if rx else None

    linter_engine = _create_engine(cfg)
    engine = PrescriptionEngine(linter_engine=linter_engine, dialect=valid_dialect)

    try:
        diff_text = engine.generate_diff(sql_text, selected_ids=selected_ids, filename=file.name)
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except (ParseError, sqlglot.errors.ParseError) as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc}")
        line_info = getattr(exc, "line", None)
        col_info = getattr(exc, "col", None)
        if line_info is not None:
            err_console.print(f"[yellow]Location: Line {line_info}, Column {col_info}[/yellow]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        err_console.print(f"[bold red]Error during diff generation:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    if not diff_text:
        console.print(
            "[dim]No diff generated. The query is already optimal or selected prescriptions produced no changes.[/dim]"
        )
        raise typer.Exit(code=0)

    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(diff_text, encoding="utf-8")
        except Exception as exc:
            err_console.print(f"[bold red]Error writing output file {output}:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc
        console.print(f"[green]Unified diff saved to {output}[/green]", soft_wrap=True)
        raise typer.Exit(code=0)

    render_diff(diff_text, console=console)
    raise typer.Exit(code=0)


@app.command("fix")
def fix(
    file: Path = typer.Argument(
        ...,
        help="Path to Snowflake SQL file to fix in-place.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        writable=True,
    ),
    rx: str | None = typer.Option(
        None,
        "--rx",
        help="Comma-separated prescription IDs to apply (e.g. 'RX-001,RX-003'). If omitted, all prescriptions are applied.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate fix without modifying the target SQL file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Bypass confirmation in non-interactive / agent environments.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect (default: snowflake).",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file (.json or .toml).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Apply optimization prescriptions in-place to Snowflake SQL file."""
    valid_dialect = _validate_dialect(dialect)

    is_tty = sys.stdin.isatty()
    if not dry_run and not force:
        if not is_tty:
            err_console.print(
                "[bold red]Error:[/bold red] Non-interactive environment detected without --force. "
                "Use --force to confirm in-place file modification."
            )
            raise typer.Exit(code=1)
        confirm = typer.confirm(
            f"Are you sure you want to modify {file} in-place?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=0)

    try:
        sql_text = file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        cfg, _ = _load_config(config, cli_args={"dialect": valid_dialect})
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    selected_ids = [s.strip() for s in rx.split(",") if s.strip()] if rx else None

    linter_engine = _create_engine(cfg)
    engine = PrescriptionEngine(linter_engine=linter_engine, dialect=valid_dialect)

    try:
        modified_sql, applied_ids = engine.apply_fixes(sql_text, selected_ids=selected_ids)
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except (ParseError, sqlglot.errors.ParseError) as exc:
        err_console.print(f"[bold red]Parse Error:[/bold red] {exc}")
        line_info = getattr(exc, "line", None)
        col_info = getattr(exc, "col", None)
        if line_info is not None:
            err_console.print(f"[yellow]Location: Line {line_info}, Column {col_info}[/yellow]")
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        err_console.print(f"[bold red]Error during fix application:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    if not applied_ids or modified_sql == sql_text:
        console.print(
            "[dim]No changes applied. The query is already optimal or selected prescriptions produced no changes.[/dim]"
        )
        raise typer.Exit(code=0)

    if dry_run:
        diff_text = engine.generate_diff(sql_text, selected_ids=selected_ids, filename=file.name)
        console.print(f"[bold cyan]Simulated in-place fix for {file} (dry-run):[/bold cyan]")
        if diff_text:
            render_diff(diff_text, console=console)
        console.print(
            f"[green]Dry-run complete. {len(applied_ids)} prescription(s) would be applied: {', '.join(applied_ids)}[/green]"
        )
        raise typer.Exit(code=0)

    try:
        file.write_text(modified_sql, encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error writing target file {file}:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(
        f"[green]Successfully applied {len(applied_ids)} prescription(s) to {file}: {', '.join(applied_ids)}[/green]"
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
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save the verification SQL to the specified file.",
    ),
    count_only: bool = typer.Option(
        False,
        "--count-only",
        help="Generate difference count aggregation query instead of row-level differences.",
    ),
    dialect: str = typer.Option(
        "snowflake",
        "--dialect",
        "-d",
        help="SQL dialect to use.",
    ),
) -> None:
    """Generate bidirectional EXCEPT equivalence verification SQL query for external execution."""
    dialect = _validate_dialect(dialect)
    try:
        orig_sql = original_file.read_text(encoding="utf-8")
        opt_sql = optimized_file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading files:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    sql = generate_verification_sql(orig_sql, opt_sql, count_only=count_only, dialect=dialect)

    if output is not None:
        try:
            output.write_text(sql, encoding="utf-8")
        except Exception as exc:
            err_console.print(f"[bold red]Error writing output file {output}:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc
        console.print(f"[bold green][OK] Verification SQL saved to {output}[/bold green]")
    else:
        typer.echo(sql)

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
            f"[bold green][OK] Feedback recorded successfully to {recorder.log_path}[/bold green]",
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


config_app = typer.Typer(
    name="config",
    help="Manage and inspect Icepick configuration.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file (.json or .toml).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output active configuration as structured JSON.",
    ),
) -> None:
    """Display currently resolved configuration settings and their provenance."""
    resolver = ConfigResolver(config_file=config)
    try:
        _cfg, runtime_summary = resolver.resolve()
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(runtime_summary.to_dict(mask=True), indent=2))
        raise typer.Exit(code=0)

    table = Table(
        title="Icepick Resolved Configuration",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Option", style="bold")
    table.add_column("Resolved Value")
    table.add_column("Source", style="green")
    table.add_column("Secret?", justify="center")

    for key, item in sorted(runtime_summary.items.items()):
        is_sec = "[yellow]Yes[/yellow]" if item.is_secret else "No"
        val = item.display_value()
        if val == "":
            val = "[dim](none)[/dim]"
        table.add_row(key, val, item.source.value, is_sec)

    console.print(table)
    raise typer.Exit(code=0)
