"""Command line interface."""

from __future__ import annotations

import sys
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
def plan(config: Path = typer.Argument(..., help="Path to msc.yml")) -> None:
    """Show which checks would run, without running anything."""
    cfg = _load(config)
    table = Table(title=f"Plan for {cfg.name}")
    for col in ("Check", "Required", "Note"):
        table.add_column(col)
    for check in build_checks(cfg):
        table.add_row(check.id, "yes" if check.required else "no", check.note)
    console.print(table)


@app.command("init-django")
def init_django(
    old: Path = typer.Option(..., "--old", help="Checkout of the release still serving traffic"),
    new: Path = typer.Option(..., "--new", help="Checkout of the release being rolled out"),
    settings: str = typer.Option(..., "--settings", help="Django settings module, e.g. mysite.settings"),
    out: Path = typer.Option(Path("msc"), "--out", help="Directory for the generated files"),
    old_python: str = typer.Option(sys.executable, "--old-python", help="Interpreter of the old release's environment"),
    new_python: str = typer.Option(sys.executable, "--new-python", help="Interpreter of the new release's environment"),
    tests: str = typer.Option("", "--tests", help="Test labels to run, space separated (default: whole suite)"),
    seed: Path = typer.Option(None, "--seed", help="Python script that loads baseline data through the old release"),
    write: Path = typer.Option(None, "--write", help="Python script: write data through the release's own code"),
    read: Path = typer.Option(None, "--read", help="Python script: read the data already in the database"),
    env: list[str] = typer.Option([], "--env", help="KEY=VALUE for every command (repeatable)"),
    manage: str = typer.Option("manage.py", "--manage"),
) -> None:
    """Generate a configuration from the Django migrations the new release adds."""
    from .adapters.django import AdapterError, Options, Release, generate

    pairs = {}
    for item in env:
        if "=" not in item:
            console.print(f"[red]error:[/red] --env expects KEY=VALUE, got '{item}'")
            raise typer.Exit(code=2)
        key, value = item.split("=", 1)
        pairs[key] = value
    opts = Options(
        old=Release(old.resolve(), old_python), new=Release(new.resolve(), new_python),
        settings=settings, out=out.resolve(), manage=manage, test_labels=tests,
        seed=seed, write=write, read=read, env=pairs,
    )
    try:
        path, phases = generate(opts)
    except AdapterError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2)
    console.print(f"wrote {path} with {len(phases)} phase(s):")
    for m in phases:
        extra = f"  ({'; '.join(m.notes)})" if m.notes else ""
        console.print(f"  {m.app}.{m.name}{extra}")
    console.print(f"next: msc check {path}")


@app.command()
def check(
    config: Path = typer.Argument(..., help="Path to msc.yml"),
    out: Path = typer.Option(None, "--out", help="Directory for report files"),
    jobs: int = typer.Option(1, "--jobs", "-j", min=1, max=32, help="Checks to run at once"),
    timeout: int = typer.Option(None, "--timeout", min=1, help="Seconds allowed per command (overrides the config)"),
    strict: bool = typer.Option(False, "--strict", help="Exit 2 if any check was not run for lack of a configured command"),
) -> None:
    """Run every required check. Exits 1 if the release is unsafe, 2 on a setup error."""
    cfg = _load(config)
    if timeout:
        cfg.timeout = timeout

    def show(result) -> None:
        if result.status == SKIPPED:
            return
        mark = "[green]pass[/green]" if result.status == PASS else "[red]FAIL[/red]"
        console.print(f"{mark}  {result.check.id}  {result.detail}")

    try:
        report = run(cfg, on_result=show, jobs=jobs)
    except KeyboardInterrupt:
        console.print("[red]interrupted[/red]; scratch databases were removed")
        raise typer.Exit(code=130)
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
    if report.first_failing_state:
        console.print(f"first failing state: {report.first_failing_state}")
    if report.unconfigured:
        ids = ", ".join(r.check.id for r in report.unconfigured)
        console.print(f"[yellow]warning:[/yellow] {len(report.unconfigured)} check(s) not run because no command is configured: {ids}")
        if strict:
            raise typer.Exit(code=2)
    raise typer.Exit(code=0 if report.safe else 1)


if __name__ == "__main__":
    app()
