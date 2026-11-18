"""The live-data check: a phase is applied over rows the running versions just wrote."""

import os
import sys

import pytest

from migration_safety_checker.config import load
from migration_safety_checker.matrix import LIVE, build_checks
from migration_safety_checker.runner import FAIL, PASS, SKIPPED, run

PY = sys.executable
needs_db = pytest.mark.skipif(
    not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL"),
    reason="MIGRATION_SAFETY_CHECKER_ADMIN_URL is not set",
)


def _sql(tmp_path, name: str, sql: str) -> str:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import os, psycopg\n"
        "psycopg.connect(os.environ['DATABASE_URL'], autocommit=True).execute(%r)\n" % sql
    )
    return f"{PY} {script}"


def _config(tmp_path, phase_sql: str, old_write: str, extra: str = ""):
    (tmp_path / "base.sql").write_text("CREATE TABLE t (id serial PRIMARY KEY, v int);")
    (tmp_path / "phase.sql").write_text(phase_sql)
    old = _sql(tmp_path, "old_write", old_write)
    noop = _sql(tmp_path, "noop", "SELECT 1")
    (tmp_path / "migration-safety-checker.yml").write_text(
        f"""
baseline: {{sql: [base.sql]}}
phases:
  - name: enforce
    sql: [phase.sql]
    old_may_run: false
apps:
  old: {{test: "{noop}", write: "{old}"}}
  new: {{test: "{noop}"}}
{extra}
"""
    )
    return load(tmp_path / "migration-safety-checker.yml")


def test_one_live_check_per_phase_is_built_before_its_states(tmp_path):
    cfg = _config(tmp_path, "SELECT 1;", "INSERT INTO t (v) VALUES (NULL)")
    ids = [c.id for c in build_checks(cfg)]
    assert ids[0] == "old@baseline"
    assert ids.index("live@before:enforce") < ids.index("old@after:enforce")
    assert sum(1 for c in build_checks(cfg) if c.kind == LIVE) == 1


def test_live_check_is_not_required_without_a_write_command(tmp_path):
    cfg = _config(tmp_path, "SELECT 1;", "INSERT INTO t (v) VALUES (1)")
    cfg.old.write = None
    live = [c for c in build_checks(cfg) if c.kind == LIVE][0]
    assert not live.required and "not configured" in live.note


@needs_db
def test_phase_that_fails_on_rows_the_old_version_wrote_is_a_migration_failure(tmp_path):
    cfg = _config(
        tmp_path, "ALTER TABLE t ALTER COLUMN v SET NOT NULL;", "INSERT INTO t (v) VALUES (NULL)"
    )
    report = run(cfg)
    live = next(r for r in report.results if r.check.kind == LIVE)
    assert live.status == FAIL
    assert "failed on rows written by the old version" in live.detail
    assert not report.safe


@needs_db
def test_phase_that_succeeds_over_live_rows_passes(tmp_path):
    cfg = _config(tmp_path, "ALTER TABLE t ADD COLUMN w int;", "INSERT INTO t (v) VALUES (1)")
    report = run(cfg)
    assert next(r for r in report.results if r.check.kind == LIVE).status == PASS
    assert report.safe


@needs_db
def test_live_check_can_be_disabled(tmp_path):
    cfg = _config(
        tmp_path, "ALTER TABLE t ALTER COLUMN v SET NOT NULL;", "INSERT INTO t (v) VALUES (NULL)",
        extra="live: false",
    )
    report = run(cfg)
    live = next(r for r in report.results if r.check.kind == LIVE)
    assert live.status == SKIPPED and "disabled" in live.detail


@needs_db
def test_failing_write_means_not_tested_rather_than_failed(tmp_path):
    cfg = _config(tmp_path, "SELECT 1;", "INSERT INTO no_such_table VALUES (1)")
    report = run(cfg)
    live = next(r for r in report.results if r.check.kind == LIVE)
    assert live.status == SKIPPED and "could not write live rows" in live.detail


@needs_db
def test_live_rows_controls_how_many_rows_each_version_writes(tmp_path):
    # A unique index over a constant value only fails once two rows exist.
    cfg = _config(tmp_path, "CREATE UNIQUE INDEX t_v ON t (v);", "INSERT INTO t (v) VALUES (7)", extra="live_rows: 1")
    assert next(r for r in run(cfg).results if r.check.kind == LIVE).status == PASS
    cfg = _config(tmp_path, "CREATE UNIQUE INDEX t_v ON t (v);", "INSERT INTO t (v) VALUES (7)", extra="live_rows: 2")
    live = next(r for r in run(cfg).results if r.check.kind == LIVE)
    assert live.status == FAIL
