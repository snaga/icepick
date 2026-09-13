"""CLI interface for Icepick for Snowflake.

Provides typer-based command-line commands for linting and optimizing Snowflake SQL queries.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from icepick import __version__
from icepick.agent_context import get_agent_context
from icepick.config import Config, ConfigResolver, RuntimeConfigSummary
from icepick.credentials import (
    format_actionable_pair_error,
    format_actionable_provider_guidance,
    resolve_credential_pair,
)
from icepick.diff import apply_unified_diff, format_diff, render_diff, split_hunks
from icepick.exceptions import AuthenticationError, ParseError
from icepick.feedback import FeedbackRecorder
from icepick.health import ConnectionTester
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
from icepick.llm.client import LLMClient
from icepick.parser import parse_snowflake_sql
from icepick.patcher import (
    AgenticPatcher,
    ASTPatcher,
    SubqueryToCTE,
    TextSplicer,
)
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


def _load_config(
    config_path: Path | None = None,
    cli_args: dict[str, Any] | None = None,
) -> tuple[Config, RuntimeConfigSummary]:
    """Load and resolve Config cascading across CLI, Env, File, Keyring, and Defaults."""
    resolver = ConfigResolver(
        cli_args=cli_args,
        config_file=config_path,
    )
    return resolver.resolve()


def _render_active_config_banner(summary: RuntimeConfigSummary, cfg: Config) -> None:
    """Render runtime configuration feedback banner for LLM / agentic runs."""
    provider_item = summary.get_item("llm_provider")
    model_item = summary.get_item("llm_model")
    project_item = summary.get_item("gcp_project")
    location_item = summary.get_item("gcp_location")
    gemini_key_item = summary.get_item("gemini_api_key")

    provider_src = escape(provider_item.source.value if provider_item else "default")
    model_src = escape(model_item.source.value if model_item else "default")
    provider_val = escape(str(cfg.llm_provider))
    model_val = escape(str(cfg.llm_model))

    lines = [
        "[bold cyan]Active LLM Configuration:[/bold cyan]",
        f"  Provider: {provider_val} (Source: {provider_src})",
        f"  Model:    {model_val} (Source: {model_src})",
    ]

    if cfg.llm_provider == "vertex":
        proj_src = escape(project_item.source.value if project_item else "default")
        loc_src = escape(location_item.source.value if location_item else "default")
        proj_val = escape(str(cfg.gcp_project or "(not set)"))
        loc_val = escape(str(cfg.gcp_location or "us-central1 (default)"))
        lines.append(f"  Project:  {proj_val} (Source: {proj_src})")
        lines.append(f"  Location: {loc_val} (Source: {loc_src})")
        lines.append("  Auth:     Google ADC / Subprocess Token")
    else:
        key_src = escape(gemini_key_item.source.value if gemini_key_item else "default")
        key_val = escape(
            str(
                gemini_key_item.display_value()
                if gemini_key_item and gemini_key_item.value
                else "(not set)"
            )
        )
        lines.append(f"  API Key:  {key_val} (Source: {key_src})")

    content = "\n".join(lines)
    console.print(Panel(content, title="Active LLM Configuration", border_style="cyan"))


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

    try:
        cfg, _ = _load_config(config, cli_args={"dialect": dialect})
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc
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
    agentic: bool = typer.Option(
        False,
        "--agentic",
        help="Enable LLM-assisted targeted rewriting for complex patterns (e.g. correlated subqueries).",
    ),
    verify_loop: bool = typer.Option(
        False,
        "--verify-loop",
        help="Enable closed-loop verification with Snowflake bidirectional EXCEPT; retries LLM rewrite on difference.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        help="Maximum retry attempts for verify-loop self-correction.",
    ),
    flatten_subqueries: bool = typer.Option(
        False,
        "--flatten-subqueries",
        help="Flatten inline derived tables to top-level CTEs.",
    ),
    reformat: bool = typer.Option(
        False,
        "--reformat",
        help="Reformat entire SQL query AST (pretty=True) instead of source-preserving minimal splicing.",
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
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="LLM provider ('gemini' or 'vertex').",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="LLM model identifier (e.g. 'gemini-3.8-flash').",
    ),
) -> None:
    """Optimize Snowflake SQL queries and output Unified Diff (read-only)."""
    dialect = _validate_dialect(dialect)
    if verify_loop:
        agentic = True

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

    cli_args: dict[str, Any] = {"dialect": dialect}
    if provider is not None:
        cli_args["llm_provider"] = provider
    if model is not None:
        cli_args["llm_model"] = model

    try:
        cfg, runtime_summary = _load_config(config, cli_args=cli_args)
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if (agentic or verify_loop) and not json_output:
        _render_active_config_banner(runtime_summary, cfg)
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
        # Optimization pipeline order:
        # 1. Deterministic AST rules (ASTPatcher): Clean up static anti-patterns first to minimize AST noise.
        ast, applied = patcher.apply_all(ast, auto_fixable_issues)
        applied_issues.extend(applied)

    # 2. Targeted LLM rewrite (AgenticPatcher): Rewrite complex, semantic-heavy patterns with minimal context.
    if agentic:
        agentic_issues = [i for i in issues if i.requires_llm and i.target_node is not None]
        if target_rank is not None:
            agentic_issues = [
                i for i in agentic_issues if _get_severity_rank(i.severity) >= target_rank
            ]
        if agentic_issues:
            provider_name = getattr(cfg, "llm_provider", "gemini")
            try:
                llm_client = LLMClient(config=cfg)
                provider_name = getattr(llm_client, "provider", provider_name)
                if hasattr(llm_client, "_auth_error") and llm_client._auth_error is not None:
                    raise llm_client._auth_error
                if hasattr(llm_client, "provider"):
                    if llm_client.provider == "gemini" and not getattr(llm_client, "api_key", None):
                        raise AuthenticationError("Gemini API key is not configured or resolved.")
                    if llm_client.provider == "vertex":
                        if not getattr(llm_client, "project", None):
                            raise ValueError("Google Cloud project ID is required for Vertex AI.")
                        if hasattr(llm_client, "_get_vertex_token"):
                            llm_client._get_vertex_token()
            except (AuthenticationError, ValueError) as exc:
                err_console.print(f"[bold red]Authentication Error:[/bold red] {exc}")
                if provider_name == "vertex":
                    err_console.print(
                        "[yellow]Actionable Advice:[/yellow] Ensure Google Cloud ADC is authenticated ('gcloud auth application-default login') and 'gcp_project' is set via CLI / config / env."
                    )
                else:
                    guidance = format_actionable_provider_guidance("gemini")
                    err_console.print(f"[yellow]Actionable Advice:[/yellow]\n{guidance}")
                raise typer.Exit(code=1) from exc

            # Resolve and validate Snowflake credentials if verify_loop is requested
            verifier: EquivalenceVerifier | None = None
            if verify_loop:
                account = getattr(cfg, "snowflake_account", None) or os.environ.get(
                    "SNOWFLAKE_ACCOUNT"
                )
                database = getattr(cfg, "snowflake_database", None) or os.environ.get(
                    "SNOWFLAKE_DATABASE"
                )
                warehouse = getattr(cfg, "snowflake_warehouse", None) or os.environ.get(
                    "SNOWFLAKE_WAREHOUSE"
                )
                user = getattr(cfg, "snowflake_user", None)
                password = getattr(cfg, "snowflake_password", None)
                if not user or not password:
                    try:
                        pair_user, pair_pass, _ = resolve_credential_pair("snowflake")
                        user = user or pair_user
                        password = password or pair_pass
                    except Exception:  # noqa: BLE001, S110
                        pass

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
                    if "user" in missing or "password" in missing:
                        auth_err = format_actionable_pair_error("snowflake")
                        err_console.print(f"[bold red]Authentication Error:[/bold red] {auth_err}")
                    else:
                        err_console.print(
                            f"[bold red]Configuration Error:[/bold red] Missing required Snowflake parameter(s): {', '.join(missing)}."
                        )
                        err_console.print(
                            "[yellow]Actionable Advice:[/yellow] Set Snowflake credentials in environment variables "
                            "(SNOWFLAKE_ACCOUNT, SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE) or via configuration file."
                        )
                    raise typer.Exit(code=1)

                verifier = EquivalenceVerifier(dialect=dialect)

            agentic_patcher = AgenticPatcher(llm_client=llm_client, dialect=dialect)
            ast, agentic_applied = agentic_patcher.apply_all(
                ast,
                agentic_issues,
                verifier=verifier,
                orig_sql=original_sql,
                config=cfg,
                max_retries=max_retries,
            )
            applied_issues.extend(agentic_applied)

    if flatten_subqueries:
        converter = SubqueryToCTE(dialect=dialect)
        ast, _ = converter.flatten_all_subqueries(ast)

    # Generate unified diff:
    # By default, use source-preserving TextSplicer for deterministic local fixes
    # to maintain comments, indentation, and formatting without AST reformatting noise.
    # Fall back to full AST reformatting (normalize=True) if --reformat is specified,
    # if --flatten-subqueries was requested (structural transformation),
    # if complex LLM rewrites were applied (AST-based modifications),
    # or if TextSplicer couldn't locate target spans.
    has_agentic_applied = any(i.requires_llm for i in applied_issues)
    if not reformat and not flatten_subqueries and not has_agentic_applied:
        splicer = TextSplicer(dialect=dialect)
        spliced_sql, spliced_issues = splicer.splice_all(original_sql, auto_fixable_issues)
        if spliced_issues:
            diff_text = format_diff(
                original_sql,
                spliced_sql,
                filename=file.name,
                normalize=False,
                dialect=dialect,
            )
            applied_issues = [
                i for i in applied_issues if i not in auto_fixable_issues
            ] + spliced_issues
        else:
            optimized_sql = ast.sql(dialect=dialect, pretty=True)
            diff_text = format_diff(
                original_sql,
                optimized_sql,
                filename=file.name,
                normalize=True,
                dialect=dialect,
            )
    else:
        optimized_sql = ast.sql(dialect=dialect, pretty=True)
        diff_text = format_diff(
            original_sql,
            optimized_sql,
            filename=file.name,
            normalize=True,
            dialect=dialect,
        )

    if not diff_text.strip():
        if json_output:
            payload = {
                "file": str(file),
                "has_changes": False,
                "issues_count": 0,
                "diff": "",
                "issues": [],
                "runtime_config": runtime_summary.to_dict(mask=True),
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
            "runtime_config": runtime_summary.to_dict(mask=True),
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output verification metrics as structured JSON to stdout.",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        "-t",
        help="Snowflake statement execution timeout in seconds.",
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
    """Verify deterministic equivalence between original and optimized queries using EXCEPT."""
    dialect = _validate_dialect(dialect)
    try:
        orig_sql = original_file.read_text(encoding="utf-8")
        opt_sql = optimized_file.read_text(encoding="utf-8")
    except Exception as exc:
        err_console.print(f"[bold red]Error reading files:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        cfg, _ = _load_config(config, cli_args={"dialect": dialect})
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    verifier = EquivalenceVerifier(dialect=dialect)
    verification_sql = verifier.build_verification_query(orig_sql, opt_sql, dialect=dialect)

    if dry_run:
        if json_output:
            dry_run_data = {
                "original_file": str(original_file),
                "optimized_file": str(optimized_file),
                "dry_run": True,
                "verification_sql": verification_sql,
            }
            typer.echo(json.dumps(dry_run_data, ensure_ascii=False, indent=2))
            raise typer.Exit(code=0)

        console.print("[bold cyan]Generated Verification SQL (Dry Run):[/bold cyan]")
        syntax = Syntax(verification_sql, "sql", theme="monokai", line_numbers=True, word_wrap=True)
        console.print(syntax)
        raise typer.Exit(code=0)

    result = verifier.verify_with_snowflake(orig_sql, opt_sql, config=cfg, timeout=timeout)

    if json_output:
        output_data = {
            "original_file": str(original_file),
            "optimized_file": str(optimized_file),
            "is_equivalent": result.is_equivalent,
            "orig_not_in_opt_count": result.orig_not_in_opt_count,
            "opt_not_in_orig_count": result.opt_not_in_orig_count,
            "error": result.error_message,
        }
        typer.echo(json.dumps(output_data, ensure_ascii=False, indent=2))
        raise typer.Exit(code=0 if result.is_equivalent else 1)

    if result.error_message:
        err_console.print(f"[bold red]Error during verification:[/bold red] {result.error_message}")
        err_console.print(
            "[yellow]Actionable Advice:[/yellow] Verify Snowflake connection settings via Windows Credential Manager "
            "('cmdkey /generic:icepick:snowflake /user:<user> /pass:<password>') or environment "
            "variables (SNOWFLAKE_ACCOUNT, SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE) or specify a configuration file via '--config <path>'."
        )
        raise typer.Exit(code=1)

    if not result.is_equivalent:
        table = Table(
            title="Equivalence Verification Differences",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Difference Type", style="dim")
        table.add_column("Row Count", justify="right")
        table.add_row("Original not in Optimized", str(result.orig_not_in_opt_count))
        table.add_row("Optimized not in Original", str(result.opt_not_in_orig_count))
        console.print(table)
        console.print(
            "[bold red]✗ Equivalence Verification Failed![/bold red] Queries produced differing result sets.",
            soft_wrap=True,
        )
        raise typer.Exit(code=1)

    console.print(
        "[bold green]✓ Equivalence Verified![/bold green] Queries are mathematically equivalent "
        "(0 differences in both directions).",
        soft_wrap=True,
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


@config_app.command("test")
def test_config(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Target service to test ('all', 'llm', 'snowflake').",
    ),
    llm_only: bool = typer.Option(
        False,
        "--llm",
        help="Test LLM connection only.",
    ),
    snowflake_only: bool = typer.Option(
        False,
        "--snowflake",
        help="Test Snowflake connection only.",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="LLM provider ('gemini' or 'vertex').",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="LLM model identifier.",
    ),
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        help="Timeout in seconds for each connection check.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output health report as structured JSON.",
    ),
) -> None:
    """Test connectivity, latency, and authentication for LLM and Snowflake services."""
    # Resolve target services to test
    # Specific flags (--llm, --snowflake) take precedence over general --target option
    if llm_only and snowflake_only:
        targets = ["llm", "snowflake"]
    elif llm_only:
        targets = ["llm"]
    elif snowflake_only:
        targets = ["snowflake"]
    else:
        normalized_target = target.strip().lower()
        if normalized_target == "all":
            targets = ["llm", "snowflake"]
        elif normalized_target == "llm":
            targets = ["llm"]
        elif normalized_target == "snowflake":
            targets = ["snowflake"]
        else:
            err_console.print(
                f"[bold red]Error:[/bold red] Invalid target '{target}'. "
                "Valid targets are 'all', 'llm', 'snowflake'."
            )
            err_console.print(
                "[yellow]Actionable Advice:[/yellow] Specify one of 'all', 'llm', or 'snowflake' for --target."
            )
            raise typer.Exit(code=1)

    cli_args: dict[str, Any] = {}
    if provider is not None:
        cli_args["llm_provider"] = provider
    if model is not None:
        cli_args["llm_model"] = model

    try:
        cfg, _ = _load_config(config, cli_args=cli_args)
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    tester = ConnectionTester()
    report = tester.test_all(cfg=cfg, targets=targets, timeout=timeout)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        if not report.all_passed:
            raise typer.Exit(code=1)
        raise typer.Exit(code=0)

    # Render summary table for interactive terminal output
    table = Table(
        title="Icepick Connection Health Check",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Service", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Details")

    for service, result in report.results.items():
        status_style = (
            "[bold green]PASS[/bold green]" if result.success else "[bold red]FAIL[/bold red]"
        )
        latency_str = f"{result.duration_ms:.1f}ms"
        details_items = [
            f"{k}={v}"
            for k, v in result.details.items()
            if v is not None and str(v) != "" and k != "response_snippet"
        ]
        details_str = ", ".join(details_items) if details_items else "-"
        table.add_row(service.upper(), status_style, latency_str, details_str)

    console.print(table)

    # If any service failed, output actionable advice panel
    for result in report.results.values():
        if not result.success:
            advice_content = f"[bold red]Error:[/bold red] {result.message}"
            if result.actionable_advice:
                advice_content += (
                    f"\n\n[yellow]Actionable Advice:[/yellow]\n{result.actionable_advice}"
                )
            console.print(
                Panel(
                    advice_content,
                    title=f"Actionable Advice: {result.service.upper()}",
                    border_style="red",
                )
            )

    if not report.all_passed:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)
