"""Shell-command phases, read commands, DATABASE_URL format and invariant errors."""

import os
import sys

import pytest

from migration_safety_checker.config import ConfigError, load
from migration_safety_checker.db import Database
from migration_safety_checker.runner import run

pytestmark = pytest.mark.skipif(
    not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL"), reason="MIGRATION_SAFETY_CHECKER_ADMIN_URL is not set"
)

PY = sys.executable


def _write(tmp_path, body: str):
    (tmp_path / "migration-safety-checker.yml").write_text(body)
    return tmp_path / "migration-safety-checker.yml"


def _sql_cmd(tmp_path, name: str, sql: str) -> str:
    """A script that runs one statement against DATABASE_URL, returned as a command."""
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import os, psycopg\n"
        "c = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)\n"
        f"c.execute({sql!r})\n"
    )
    return f"{PY} {script}"


def test_database_url_is_a_postgresql_url():
    url = Database("host=h port=5433 user=u password=p@w").url("migration_safety_checker_x")
    assert url == "postgresql://u:p%40w@h:5433/migration_safety_checker_x"


def test_database_url_supports_socket_directories():
    url = Database("host=/var/run/postgresql user=postgres").url("migration_safety_checker_x")
    assert url == "postgresql://postgres@/migration_safety_checker_x?host=/var/run/postgresql"


def test_run_phases_build_the_state_and_read_overrides_test(tmp_path):
    create = _sql_cmd(tmp_path, "create", "CREATE TABLE t(id int, v text)")
    expand = _sql_cmd(tmp_path, "expand", "ALTER TABLE t ADD COLUMN w text")
    wrote = _sql_cmd(tmp_path, "wrote", "INSERT INTO t VALUES (1, '1')")
    old_test = _sql_cmd(tmp_path, "old_test", "SELECT v FROM t")
    needs_w = _sql_cmd(tmp_path, "needs_w", "SELECT w FROM t")
    cfg = _write(tmp_path, f"""
baseline:
  run: ["{create}"]
phases:
  - name: expand
    run: ["{expand}"]
apps:
  old: {{test: "{old_test}", write: "{wrote}"}}
  new: {{test: "exit 1", read: "{needs_w}", write: "{wrote}"}}
""")
    report = run(load(cfg))
    # new.test always fails, but forward and rollback use read, which passes
    by_id = {r.check.id: r.status for r in report.results}
    assert by_id["forward@after:expand"] == "pass"
    assert by_id["rollback@after:expand"] == "pass"
    assert by_id["new@after:expand"] == "fail"


def test_failing_run_phase_is_reported_not_raised(tmp_path):
    cfg = _write(tmp_path, f"""
baseline:
  sql: [schema.sql]
phases:
  - name: boom
    run: ['exit 3']
apps:
  old: {{test: 'true'}}
  new: {{test: 'true'}}
""")
    (tmp_path / "schema.sql").write_text("CREATE TABLE t(id int);")
    report = run(load(cfg))
    failed = [r for r in report.results if r.status == "fail"]
    assert failed and "migration failed before the application ran" in failed[0].detail


def test_invariant_that_cannot_run_fails_cleanly(tmp_path):
    cfg = _write(tmp_path, """
baseline: {sql: [schema.sql]}
phases:
  - {name: p, sql: [p.sql]}
apps:
  old: {test: 'true'}
  new: {test: 'true'}
invariants:
  - {name: broken, sql: 'SELECT nope FROM t'}
""")
    (tmp_path / "schema.sql").write_text("CREATE TABLE t(id int);")
    (tmp_path / "p.sql").write_text("SELECT 1;")
    report = run(load(cfg))
    assert not report.safe
    assert "could not run" in report.failed[0].detail


def test_phase_needs_sql_or_run(tmp_path):
    cfg = _write(tmp_path, """
baseline: {sql: [s.sql]}
phases: [{name: p}]
apps: {old: {test: 'true'}, new: {test: 'true'}}
""")
    (tmp_path / "s.sql").write_text("SELECT 1;")
    with pytest.raises(ConfigError):
        load(cfg)
