"""Runs the checks of a matrix, each in an isolated scratch database."""

from __future__ import annotations

import os
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
from .matrix import FORWARD, NEW, OLD, ROLLBACK, Check, build_checks

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"

LOG_LIMIT = 4000


@dataclass
class CheckResult:
    check: Check
    status: str
    detail: str = ""
    log: str = ""
    duration: float = 0.0


@dataclass
class Report:
    name: str
    results: list[CheckResult]

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL]

    @property
    def required(self) -> list[CheckResult]:
        return [r for r in self.results if r.check.required]

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
        return not self.failed

    @property
    def verdict(self) -> str:
        return "SAFE" if self.safe else "UNSAFE"


def _trim(text: str) -> str:
    text = text.strip()
    return text if len(text) <= LOG_LIMIT else text[:LOG_LIMIT] + "\n... (truncated)"


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


def _execute(cfg: Config, db: Database, template: str, check: Check) -> CheckResult:
    name = db.new_name(check.id)
    started = time.monotonic()
    try:
        db.create(name, template=template)
    except psycopg.Error as exc:
        return CheckResult(
            check, FAIL, f"could not create a scratch database: {str(exc).splitlines()[0]}",
            duration=time.monotonic() - started,
        )
    try:
        try:
            for phase in check.state.applied:
                _apply(cfg, db, name, phase.sql, phase.run)
            if check.state.partial is not None:
                part = check.state.partial
                _apply(cfg, db, name, part.partial_sql, part.partial_run)
        except (psycopg.Error, RuntimeError) as exc:
            return CheckResult(
                check, FAIL, f"migration failed before the application ran: {exc}",
                duration=time.monotonic() - started,
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
                )

        for inv in cfg.invariants:
            try:
                rows = db.violations(name, inv.sql)
            except psycopg.Error as exc:
                return CheckResult(
                    check, FAIL,
                    f"invariant '{inv.name}' could not run: {str(exc).splitlines()[0]}",
                    log="\n\n".join(logs), duration=time.monotonic() - started,
                )
            if rows:
                sample = ", ".join(str(r) for r in rows[:3])
                return CheckResult(
                    check, FAIL,
                    f"invariant '{inv.name}' violated by {len(rows)} row(s), e.g. {sample}",
                    log="\n\n".join(logs), duration=time.monotonic() - started,
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
) -> Report:
    """Run every check. With ``jobs`` > 1, checks run concurrently, each in its own database."""
    db = Database(cfg.admin_url)
    template = db.new_name("template")
    checks = build_checks(cfg)
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
