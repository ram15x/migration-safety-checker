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
def plan(config: Path = typer.Argument(..., help="Path to migration-safety-checker.yml")) -> None:
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
    out: Path = typer.Option(Path("checks"), "--out", help="Directory for the generated files"),
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
    console.print(f"next: migration-safety-checker check {path}")


@app.command("init-alembic")
def init_alembic(
    old: Path = typer.Option(..., "--old", help="Checkout of the release still serving traffic"),
    new: Path = typer.Option(..., "--new", help="Checkout of the release being rolled out"),
    test: str = typer.Option(..., "--test", help="Shell command that runs the application's tests, from the release directory; {python} is the release's interpreter"),
    out: Path = typer.Option(Path("checks"), "--out", help="Directory for the generated files"),
    old_python: str = typer.Option(sys.executable, "--old-python", help="Interpreter of the old release's environment (needs alembic)"),
    new_python: str = typer.Option(sys.executable, "--new-python", help="Interpreter of the new release's environment (needs alembic)"),
    config: str = typer.Option("alembic.ini", "--config", help="Alembic config file, relative to each checkout"),
    seed: str = typer.Option("", "--seed", help="Shell command that loads baseline data through the old release"),
    write: str = typer.Option("", "--write", help="Shell command: write data through the release's own code"),
    read: str = typer.Option("", "--read", help="Shell command: read the data already in the database"),
    env: list[str] = typer.Option([], "--env", help="KEY=VALUE for every command (repeatable)"),
    url_env: list[str] = typer.Option([], "--url-env", help="Variable your env.py reads the database URL from (repeatable; default DATABASE_URL)"),
    url_part: list[str] = typer.Option([], "--url-part", help="NAME=PART (host, port, user, password or dbname) exported for apps that build their own URL (repeatable)"),
    driver: str = typer.Option("", "--driver", help="SQLAlchemy driver to put in the URL, e.g. psycopg"),
    migrate_first: bool = typer.Option(False, "--migrate-first", help="All revisions run before the new release starts (e.g. `alembic upgrade head` in a pre-start step): require the new release only after the last one"),
) -> None:
    """Generate a configuration from the Alembic revisions the new release adds."""
    from .adapters.alembic import Options, Release, generate
    from .adapters.django import AdapterError

    pairs = {}
    for item in env:
        if "=" not in item:
            console.print(f"[red]error:[/red] --env expects KEY=VALUE, got '{item}'")
            raise typer.Exit(code=2)
        key, value = item.split("=", 1)
        pairs[key] = value
    parts = {}
    for item in url_part:
        name, _, part = item.partition("=")
        if part not in ("host", "port", "user", "password", "dbname"):
            console.print(f"[red]error:[/red] --url-part expects NAME=PART with PART one of host, port, user, password, dbname; got '{item}'")
            raise typer.Exit(code=2)
        parts[name] = part
    opts = Options(
        old=Release(old.resolve(), old_python, test), new=Release(new.resolve(), new_python, test),
        out=out.resolve(), config=config, seed=seed, write=write, read=read, env=pairs,
        url_env=url_env or ["DATABASE_URL"], url_parts=parts, driver=driver, migrate_first=migrate_first,
    )
    try:
        path, phases = generate(opts)
    except AdapterError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2)
    console.print(f"wrote {path} with {len(phases)} phase(s):")
    for r in phases:
        extra = f"  ({'; '.join(r.notes)})" if r.notes else ""
        console.print(f"  {r.label}{extra}", highlight=False)
    console.print(f"next: migration-safety-checker check {path}")


@app.command()
def check(
    config: Path = typer.Argument(..., help="Path to migration-safety-checker.yml"),
    out: Path = typer.Option(None, "--out", help="Directory for report files"),
    jobs: int = typer.Option(1, "--jobs", "-j", min=1, max=32, help="Checks to run at once"),
    timeout: int = typer.Option(None, "--timeout", min=1, help="Seconds allowed per command (overrides the config)"),
    isolate: bool = typer.Option(False, "--isolate", help="Also apply each phase alone on the baseline (diagnostic, never changes the verdict)"),
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
        if result.check.diagnostic:
            mark = "[dim]diag[/dim] " + mark
        cause = f"  [{result.cause}]" if result.cause else ""
        console.print(f"{mark}  {result.check.id}  {result.detail}{cause}", markup=True, highlight=False)

    try:
        report = run(cfg, on_result=show, jobs=jobs, isolate=isolate)
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

    status = {"SAFE": "[green]SAFE[/green]", "UNSAFE": "[red]UNSAFE[/red]", "INCONCLUSIVE": "[yellow]INCONCLUSIVE[/yellow]"}[report.verdict]
    console.print(f"\nverdict: {status} ({len(report.failed)} of {len(report.required)} required checks failed)")
    if report.triage:
        console.print("failures by cause: " + ", ".join(f"{c} {n}" for c, n in report.triage.items()))
    if report.verdict == "INCONCLUSIVE":
        console.print("[yellow]every failure was a harness failure (command could not run); nothing shows the migration is unsafe[/yellow]")
    if report.first_failing_state:
        console.print(f"first failing state: {report.first_failing_state}")
    if report.unconfigured:
        ids = ", ".join(r.check.id for r in report.unconfigured)
        console.print(f"[yellow]warning:[/yellow] {len(report.unconfigured)} check(s) not run because no command is configured: {ids}")
        if strict:
            raise typer.Exit(code=2)
    raise typer.Exit(code={"SAFE": 0, "UNSAFE": 1, "INCONCLUSIVE": 2}[report.verdict])


if __name__ == "__main__":
    app()
