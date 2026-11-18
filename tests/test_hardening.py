"""Timeouts, parallel runs and error messages."""

import json
import os
import time
from pathlib import Path

import pytest

from migration_safety_checker.config import load
from migration_safety_checker.report import to_json, to_markdown
from migration_safety_checker.runner import _run_command, run

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "rename-column"
needs_db = pytest.mark.skipif(not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL"), reason="MIGRATION_SAFETY_CHECKER_ADMIN_URL is not set")


def test_timeout_kills_the_whole_process_group(tmp_path):
    # the shell starts a background child that keeps the output pipe open
    started = time.monotonic()
    code, out = _run_command("sleep 30 & sleep 30", tmp_path, dict(os.environ), timeout=1)
    assert code == 124 and "timed out after 1s" in out
    assert time.monotonic() - started < 10


def test_missing_program_is_explained(tmp_path):
    code, out = _run_command("definitely-not-a-program", tmp_path, dict(os.environ), timeout=5)
    assert code == 127 and "not installed or not on PATH" in out


def test_empty_command_is_not_a_silent_pass(tmp_path):
    code, out = _run_command("   ", tmp_path, dict(os.environ), timeout=5)
    assert code != 0 and "no command configured" in out


@needs_db
def test_parallel_run_matches_serial_run():
    cfg = load(EXAMPLES / "unsafe.yml")
    serial = run(cfg)
    parallel = run(cfg, jobs=4)
    assert [r.check.id for r in parallel.results] == [r.check.id for r in serial.results]
    assert {r.check.id for r in parallel.failed} == {r.check.id for r in serial.failed}


@needs_db
def test_unreachable_database_gives_a_clear_error():
    cfg = load(EXAMPLES / "safe.yml")
    cfg.admin_url = "postgresql://postgres:secret@127.0.0.1:1/postgres"
    with pytest.raises(RuntimeError) as exc:
        run(cfg)
    msg = str(exc.value)
    assert "cannot connect to PostgreSQL" in msg and "secret" not in msg


@needs_db
def test_missing_write_command_is_reported_as_not_run(tmp_path):
    (tmp_path / "s.sql").write_text("CREATE TABLE t(id int);")
    (tmp_path / "p.sql").write_text("SELECT 1;")
    (tmp_path / "migration-safety-checker.yml").write_text(
        "baseline: {sql: [s.sql]}\nphases: [{name: p, sql: [p.sql]}]\n"
        "apps: {old: {test: 'true'}, new: {test: 'true'}}\n"
    )
    report = run(load(tmp_path / "migration-safety-checker.yml"))
    assert report.safe
    assert {r.check.id for r in report.unconfigured} == {"live@before:p", "forward@after:p", "rollback@after:p"}
    assert "Not run, because the config has no command" in to_markdown(report)


@needs_db
def test_json_report_has_logs_and_first_failing_state():
    report = run(load(EXAMPLES / "unsafe.yml"))
    data = json.loads(to_json(report))
    assert data["first_failing_state"] == "after:rename"
    assert any(c["status"] == "fail" and c["log"] for c in data["checks"])
