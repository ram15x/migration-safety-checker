"""Runs the checks of a matrix, each in an isolated scratch database."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psycopg

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .config import Config
from .db import Database
from .matrix import FORWARD, LIVE, NEW, OLD, ROLLBACK, Check, build_checks

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"

# Why a check failed. Only HARNESS failures say nothing about the migration.
HARNESS = "harness"        # the command could not run properly: timeout, not found, import error, ...
MIGRATION = "migration"    # a phase did not apply
SCHEMA = "schema"          # an app command failed with a database error (missing column, constraint, ...)
APP = "app-test"           # an app command failed without a database or harness signature (an assertion, say)
INVARIANT = "invariant"    # one of your SQL invariants returned rows
CAUSES = (SCHEMA, MIGRATION, INVARIANT, APP, HARNESS)

_HARNESS = re.compile(
    r"ModuleNotFoundError|No module named|ImportError|cannot import name|SyntaxError|IndentationError"
    r"|command not found|No such file or directory|can't open file|Permission denied|exec format error"
    r"|ImproperlyConfigured|timed out after|no command configured|exit 12[67]"
    r"|connection to server .* failed|could not connect to server|Connection refused|the database system is"
)
_SCHEMA = re.compile(
    r"UndefinedColumn|UndefinedTable|UndefinedFunction|NotNullViolation|UniqueViolation|CheckViolation"
    r"|ForeignKeyViolation|InvalidTextRepresentation|DatatypeMismatch|DataError|IntegrityError|ProgrammingError"
    r"|OperationalError|SQLSTATE|django\.db\.utils|psycopg2?\.errors|psycopg\.errors"
    r"|column \"?[\w.]+\"? (of relation \"?\w+\"? )?does not exist|relation \"?[\w.]+\"? does not exist"
    r"|violates (not-null|unique|check|foreign key)"
)


def classify(code: int, output: str) -> str:
    """Decide from the exit code and the command's output why an app command failed.

    This is pattern matching on exit codes and error text, not proof: a command whose
    output matches nothing is reported as an app-test failure.
    """
    if code in (124, 126, 127) or _HARNESS.search(output):
        return HARNESS
    if _SCHEMA.search(output):
        return SCHEMA
    return APP

LOG_LIMIT = 4000


@dataclass
class CheckResult:
    check: Check
    status: str
    detail: str = ""
    log: str = ""
    duration: float = 0.0
    cause: str = ""  # for failures: one of CAUSES


@dataclass
class Report:
    name: str
    results: list[CheckResult]

    @property
    def failed(self) -> list[CheckResult]:
        """Failed checks of the rollout itself (diagnostic isolation runs are not counted)."""
        return [r for r in self.results if r.status == FAIL and not r.check.diagnostic]

    @property
    def required(self) -> list[CheckResult]:
        return [r for r in self.results if r.check.required and not r.check.diagnostic]

    @property
    def diagnostic(self) -> list[CheckResult]:
        """Phase-in-isolation runs. They explain failures but never change the verdict."""
        return [r for r in self.results if r.check.diagnostic]

    @property
    def triage(self) -> dict[str, int]:
        """Number of failed checks per cause, for the causes that occurred."""
        counts = {c: sum(1 for r in self.failed if r.cause == c) for c in CAUSES}
        return {c: n for c, n in counts.items() if n}

    @property
    def harness_failures(self) -> list[CheckResult]:
        return [r for r in self.failed if r.cause == HARNESS]

    @property
    def unconfigured(self) -> list[CheckResult]:
        """Checks that did not run because a command is missing from the config."""
        return [r for r in self.results if r.status == SKIPPED and "not configured" in r.detail]

    @property
    def first_failing_state(self) -> str | None:
        """The earliest database state in which any check failed."""
        return self.failed[0].check.state.name if self.failed else None

    @property
    def safe(self) -> bool:
        return self.verdict == "SAFE"

    @property
    def verdict(self) -> str:
        """SAFE, UNSAFE, or INCONCLUSIVE when every failure came from the harness."""
        if not self.failed:
            return "SAFE"
        if any(r.cause != HARNESS for r in self.failed):
            return "UNSAFE"
        return "INCONCLUSIVE"


def _trim(text: str) -> str:
    """Keep the start and the end of long output: the error is usually at the end."""
    text = text.strip()
    if len(text) <= LOG_LIMIT:
        return text
    head = LOG_LIMIT // 8
    return text[:head] + "\n... (truncated) ...\n" + text[-(LOG_LIMIT - head):]


def _last_line(message: str) -> str:
    lines = [ln.strip() for ln in str(message).splitlines() if ln.strip()]
    for line in reversed(lines):
        if "Error" in line or "Exception" in line or "error:" in line:
            return line
    return lines[-1] if lines else "failed"


def _run_command(command: str, cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str]:
    """Run a shell command in its own process group; kill the whole group on timeout.

    Killing only the shell would leave its children running and holding the output
    pipes, so a hung test suite could stall the run past the timeout.
    """
    if not command.strip():
        return 2, "no command configured"
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, env=env, text=True, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, _ = proc.communicate()
        return 124, _trim((out or "") + f"\ntimed out after {timeout}s")
    except BaseException:
        _kill_group(proc)
        raise
    code = proc.returncode
    out = out or ""
    if code == 127 and "not found" in out:
        out += "\n(exit 127: a program in this command is not installed or not on PATH)"
    return code, _trim(out)


def _kill_group(proc: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _shell(cfg: Config, command: str, env_url: str, check: Check, app: str) -> tuple[int, str]:
    env = dict(os.environ)
    env.update({"DATABASE_URL": env_url, "MIGRATION_SAFETY_CHECKER_STATE": check.state.name, "MIGRATION_SAFETY_CHECKER_APP": app})
    return _run_command(command, cfg.root, env, cfg.timeout)


def _apply(
    cfg: Config, db: Database, dbname: str, sql_files: list[str], commands: list[str]
) -> None:
    """Apply SQL files, then shell commands, to one database."""
    db.apply_files(dbname, cfg.root, sql_files)
    env = dict(os.environ)
    env["DATABASE_URL"] = db.url(dbname)
    env["MIGRATION_SAFETY_CHECKER_APP"] = "migrate"
    for command in commands:
        code, out = _run_command(command, cfg.root, env, cfg.timeout)
        if code != 0:
            raise RuntimeError(f"exit {code}: {command}\n{out}")


def _execute_live(cfg: Config, db: Database, name: str, check: Check, started: float) -> CheckResult:
    """Write rows with the versions running before the phase, then apply the phase over them."""
    state, phase = check.state, check.phase
    assert phase is not None
    try:
        for earlier in state.applied:
            _apply(cfg, db, name, earlier.sql, earlier.run)
    except (psycopg.Error, RuntimeError) as exc:
        return CheckResult(
            check, FAIL, f"migration failed before the application ran: {exc}",
            duration=time.monotonic() - started,
            cause=HARNESS if _HARNESS.search(str(exc)) else MIGRATION,
        )
    url = db.url(name)
    logs: list[str] = []
    writers: list[str] = []
    for label, command, app, running in (
        ("old write", cfg.old.write, OLD, state.old_required),
        ("new write", cfg.new.write, NEW, state.new_required),
    ):
        if not running or not command:
            continue
        for _ in range(cfg.live_rows):
            code, out = _shell(cfg, command, url, check, app)
            if out:
                logs.append(f"[{label}]\n{out}")
            if code != 0:
                return CheckResult(
                    check, SKIPPED,
                    f"could not write live rows ({label} exited with code {code}); "
                    "this phase was not tested over live data",
                    log="\n\n".join(logs), duration=time.monotonic() - started,
                )
        writers.append(app)
    try:
        _apply(cfg, db, name, phase.sql, phase.run)
    except (psycopg.Error, RuntimeError) as exc:
        return CheckResult(
            check, FAIL,
            f"phase '{phase.name}' failed on rows written by the {' and '.join(writers)} version: "
            f"{_last_line(exc)}",
            log="\n\n".join(logs + [str(exc)]), duration=time.monotonic() - started,
            cause=HARNESS if _HARNESS.search(str(exc)) else MIGRATION,
        )
    for inv in cfg.invariants:
        try:
            rows = db.violations(name, inv.sql)
        except psycopg.Error as exc:
            return CheckResult(
                check, FAIL, f"invariant '{inv.name}' could not run: {str(exc).splitlines()[0]}",
                log="\n\n".join(logs), duration=time.monotonic() - started, cause=SCHEMA,
            )
        if rows:
            return CheckResult(
                check, FAIL, f"invariant '{inv.name}' violated by {len(rows)} row(s) after the phase",
                log="\n\n".join(logs), duration=time.monotonic() - started, cause=INVARIANT,
            )
    return CheckResult(check, PASS, log="\n\n".join(logs), duration=time.monotonic() - started)


def _execute(cfg: Config, db: Database, template: str, check: Check) -> CheckResult:
    name = db.new_name(check.id)
    started = time.monotonic()
    try:
        db.create(name, template=template)
    except psycopg.Error as exc:
        return CheckResult(
            check, FAIL, f"could not create a scratch database: {str(exc).splitlines()[0]}",
            duration=time.monotonic() - started, cause=HARNESS,
        )
    try:
        if check.kind == LIVE:
            return _execute_live(cfg, db, name, check, started)
        try:
            for phase in check.state.applied:
                _apply(cfg, db, name, phase.sql, phase.run)
            if check.state.partial is not None:
                part = check.state.partial
                _apply(cfg, db, name, part.partial_sql, part.partial_run)
        except (psycopg.Error, RuntimeError) as exc:
            if check.diagnostic:
                return CheckResult(
                    check, SKIPPED,
                    f"phase cannot be applied to the baseline on its own (it may depend on an earlier phase): "
                    f"{_last_line(exc)}",
                    duration=time.monotonic() - started,
                )
            cause = HARNESS if _HARNESS.search(str(exc)) else MIGRATION
            return CheckResult(
                check, FAIL, f"migration failed before the application ran: {exc}",
                duration=time.monotonic() - started, cause=cause,
            )

        url = db.url(name)
        steps: list[tuple[str, str, str]] = []
        if check.kind == OLD:
            steps = [("old test", cfg.old.test or "", OLD)]
        elif check.kind == NEW:
            steps = [("new test", cfg.new.test or "", NEW)]
        elif check.kind == FORWARD:
            steps = [
                ("old write", cfg.old.write or "", OLD),
                ("new read after old write", cfg.new.read or cfg.new.test or "", NEW),
            ]
        elif check.kind == ROLLBACK:
            steps = [
                ("new write", cfg.new.write or "", NEW),
                ("old read after new write", cfg.old.read or cfg.old.test or "", OLD),
            ]

        logs: list[str] = []
        for label, command, app in steps:
            code, out = _shell(cfg, command, url, check, app)
            if out:
                logs.append(f"[{label}]\n{out}")
            if code != 0:
                return CheckResult(
                    check, FAIL, f"{label} exited with code {code}",
                    log="\n\n".join(logs), duration=time.monotonic() - started,
                    cause=classify(code, out),
                )

        for inv in cfg.invariants:
            try:
                rows = db.violations(name, inv.sql)
            except psycopg.Error as exc:
                return CheckResult(
                    check, FAIL,
                    f"invariant '{inv.name}' could not run: {str(exc).splitlines()[0]}",
                    log="\n\n".join(logs), duration=time.monotonic() - started, cause=SCHEMA,
                )
            if rows:
                sample = ", ".join(str(r) for r in rows[:3])
                return CheckResult(
                    check, FAIL,
                    f"invariant '{inv.name}' violated by {len(rows)} row(s), e.g. {sample}",
                    log="\n\n".join(logs), duration=time.monotonic() - started, cause=INVARIANT,
                )

        return CheckResult(
            check, PASS, log="\n\n".join(logs), duration=time.monotonic() - started
        )
    finally:
        db.drop(name)


def _describe(admin_url: str) -> str:
    try:
        parts = conninfo_to_dict(make_conninfo(admin_url))
    except psycopg.Error:
        return "the configured database"
    host = parts.get("host") or "local socket"
    port = f":{parts['port']}" if parts.get("port") else ""
    return f"{parts.get('user', '')}@{host}{port}".lstrip("@")


def run(
    cfg: Config,
    on_result: Callable[[CheckResult], None] | None = None,
    jobs: int = 1,
    isolate: bool = False,
) -> Report:
    """Run every check. With ``jobs`` > 1, checks run concurrently, each in its own database.

    With ``isolate``, every phase is also applied alone on the baseline and tested
    (diagnostic results: reported, never part of the verdict).
    """
    db = Database(cfg.admin_url)
    template = db.new_name("template")
    checks = build_checks(cfg, isolate=isolate)
    results: dict[str, CheckResult] = {}
    lock = threading.Lock()

    def record(result: CheckResult) -> None:
        with lock:
            results[result.check.id] = result
            if on_result:
                on_result(result)

    try:
        try:
            db.create(template)
        except psycopg.OperationalError as exc:
            reason = " ".join(str(exc).split()) or "connection failed"
            raise RuntimeError(
                f"cannot connect to PostgreSQL as {_describe(cfg.admin_url)} "
                f"(MIGRATION_SAFETY_CHECKER_ADMIN_URL): {reason}"
            ) from None
        try:
            _apply(cfg, db, template, cfg.baseline_sql, cfg.baseline_run)
        except (psycopg.Error, RuntimeError) as exc:
            raise RuntimeError(f"baseline failed: {exc}") from exc

        runnable = []
        for check in checks:
            if check.required:
                runnable.append(check)
            else:
                record(CheckResult(check, SKIPPED, check.note))
        if jobs <= 1:
            for check in runnable:
                record(_execute(cfg, db, template, check))
        else:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                for future in [pool.submit(_execute, cfg, db, template, c) for c in runnable]:
                    record(future.result())
    finally:
        db.cleanup()
    return Report(name=cfg.name, results=[results[c.id] for c in checks if c.id in results])
