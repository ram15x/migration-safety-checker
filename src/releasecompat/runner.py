"""Runs the checks of a matrix, each in an isolated scratch database."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

import psycopg

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
    def safe(self) -> bool:
        return not self.failed

    @property
    def verdict(self) -> str:
        return "SAFE" if self.safe else "UNSAFE"


def _trim(text: str) -> str:
    text = text.strip()
    return text if len(text) <= LOG_LIMIT else text[:LOG_LIMIT] + "\n... (truncated)"


def _shell(cfg: Config, command: str, env_url: str, check: Check, app: str) -> tuple[int, str]:
    env = dict(os.environ)
    env.update({"DATABASE_URL": env_url, "RC_STATE": check.state.name, "RC_APP": app})
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cfg.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {cfg.timeout}s"
    return proc.returncode, _trim(proc.stdout + ("\n" + proc.stderr if proc.stderr else ""))


def _execute(cfg: Config, db: Database, template: str, check: Check) -> CheckResult:
    name = db.new_name(check.id)
    started = time.monotonic()
    db.create(name, template=template)
    try:
        try:
            for phase in check.state.applied:
                db.apply_files(name, cfg.root, phase.sql)
            if check.state.partial is not None:
                db.apply_files(name, cfg.root, check.state.partial.partial_sql)
        except psycopg.Error as exc:
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
                ("new test after old write", cfg.new.test or "", NEW),
            ]
        elif check.kind == ROLLBACK:
            steps = [
                ("new write", cfg.new.write or "", NEW),
                ("old test after new write", cfg.old.test or "", OLD),
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
            rows = db.violations(name, inv.sql)
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


def run(
    cfg: Config,
    on_result: Callable[[CheckResult], None] | None = None,
) -> Report:
    db = Database(cfg.admin_url)
    results: list[CheckResult] = []
    template = db.new_name("template")
    try:
        db.create(template)
        try:
            db.apply_files(template, cfg.root, cfg.baseline_sql)
        except psycopg.Error as exc:
            raise RuntimeError(f"baseline SQL failed: {exc}") from exc

        for check in build_checks(cfg):
            if not check.required:
                result = CheckResult(check, SKIPPED, check.note)
            else:
                result = _execute(cfg, db, template, check)
            results.append(result)
            if on_result:
                on_result(result)
    finally:
        db.cleanup()
    return Report(name=cfg.name, results=results)
