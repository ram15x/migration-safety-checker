"""Command line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ConfigError, load
from .matrix import build_checks
from .report import to_json, to_junit, to_markdown
from .runner import FAIL, PASS, SKIPPED, run

app = typer.Typer(
    help="Test that old and new application versions both work against every database state of a rolling deploy.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _load(path: Path):
    try:
        return load(path)
    except ConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command()
def plan(config: Path = typer.Argument(..., help="Path to releasecompat.yml")) -> None:
    """Show which checks would run, without running anything."""
    cfg = _load(config)
    table = Table(title=f"Plan for {cfg.name}")
    for col in ("Check", "Required", "Note"):
        table.add_column(col)
    for check in build_checks(cfg):
        table.add_row(check.id, "yes" if check.required else "no", check.note)
    console.print(table)


@app.command()
def check(
    config: Path = typer.Argument(..., help="Path to releasecompat.yml"),
    out: Path = typer.Option(None, "--out", help="Directory for report files"),
) -> None:
    """Run every required check. Exits 1 if the release is unsafe."""
    cfg = _load(config)

    def show(result) -> None:
        if result.status == SKIPPED:
            return
        mark = "[green]pass[/green]" if result.status == PASS else "[red]FAIL[/red]"
        console.print(f"{mark}  {result.check.id}  {result.detail}")

    try:
        report = run(cfg, on_result=show)
    except RuntimeError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2)

    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(to_markdown(report))
        (out / "report.json").write_text(to_json(report))
        (out / "junit.xml").write_text(to_junit(report))
        console.print(f"reports written to {out}")

    status = "[green]SAFE[/green]" if report.safe else "[red]UNSAFE[/red]"
    console.print(f"\nverdict: {status} ({len(report.failed)} of {len(report.required)} required checks failed)")
    raise typer.Exit(code=0 if report.safe else 1)


if __name__ == "__main__":
    app()
