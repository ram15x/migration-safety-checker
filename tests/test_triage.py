"""Failure triage (harness vs schema) and the per-phase isolation runs."""

import os
import sys

import pytest
from typer.testing import CliRunner

from migration_safety_checker.cli import app
from migration_safety_checker.config import load
from migration_safety_checker.matrix import build_checks
from migration_safety_checker.report import to_json, to_markdown
from migration_safety_checker.runner import APP, FAIL, HARNESS, PASS, SCHEMA, SKIPPED, classify, run

PY = sys.executable
needs_db = pytest.mark.skipif(
    not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL"),
    reason="MIGRATION_SAFETY_CHECKER_ADMIN_URL is not set",
)


@pytest.mark.parametrize(
    "code, text, expected",
    [
        (127, "bash: nope: command not found", HARNESS),
        (124, "timed out after 5s", HARNESS),
        (1, "ModuleNotFoundError: No module named 'app'", HARNESS),
        (1, "django.core.exceptions.ImproperlyConfigured: settings", HARNESS),
        (1, "psycopg.OperationalError: connection failed: Connection refused", HARNESS),
        (1, 'django.db.utils.ProgrammingError: column "body" does not exist', SCHEMA),
        (1, "psycopg.errors.NotNullViolation: null value in column", SCHEMA),
        (1, "AssertionError: rows with empty values", APP),
        (1, "FAILED tests/test_x.py::test_y - assert 1 == 2", APP),
    ],
)
def test_classify(code, text, expected):
    assert classify(code, text) == expected


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text("import os, psycopg\nc = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)\n" + body)
    return f"{PY} {path}"


def _config(tmp_path, phases: list[tuple[str, str]], old_test: str, new_test: str = "true", old_write: str | None = None):
    (tmp_path / "base.sql").write_text("CREATE TABLE t (id serial PRIMARY KEY, a text, b text);")
    lines = ["baseline: {sql: [base.sql]}", "phases:"]
    for i, (name, sql) in enumerate(phases):
        (tmp_path / f"p{i}.sql").write_text(sql)
        lines += [f"  - name: {name}", f"    sql: [p{i}.sql]"]
    write = f', write: "{old_write}"' if old_write else ""
    lines += ["apps:", f'  old: {{test: "{old_test}"{write}}}', f'  new: {{test: "{new_test}"}}']
    (tmp_path / "migration-safety-checker.yml").write_text("\n".join(lines) + "\n")
    return load(tmp_path / "migration-safety-checker.yml")


@needs_db
def test_command_that_cannot_run_is_a_harness_failure_and_inconclusive(tmp_path):
    cfg = _config(tmp_path, [("p", "SELECT 1;")], old_test="definitely-not-a-command-xyz")
    report = run(cfg)
    assert report.failed and all(r.cause == HARNESS for r in report.failed)
    assert report.verdict == "INCONCLUSIVE" and not report.safe
    assert "INCONCLUSIVE" in to_markdown(report)
    assert "Failure triage" in to_markdown(report)
    assert '"triage"' in to_json(report)


@needs_db
def test_schema_error_is_unsafe_and_classified_as_schema(tmp_path):
    cfg = _config(
        tmp_path, [("drop", "ALTER TABLE t DROP COLUMN a;")],
        old_test=_script(tmp_path, "old", "c.execute('SELECT a FROM t')\n"),
    )
    report = run(cfg)
    assert report.verdict == "UNSAFE"
    assert report.triage == {SCHEMA: 1}


@needs_db
def test_one_harness_failure_among_real_ones_does_not_hide_the_verdict(tmp_path):
    cfg = _config(
        tmp_path, [("drop", "ALTER TABLE t DROP COLUMN a;")],
        old_test=_script(tmp_path, "old", "c.execute('SELECT a FROM t')\n"),
        new_test="definitely-not-a-command-xyz",
    )
    report = run(cfg)
    assert report.verdict == "UNSAFE"
    assert set(report.triage) == {SCHEMA, HARNESS}


@needs_db
def test_cli_exit_codes_are_0_1_2(tmp_path):
    runner = CliRunner()
    safe = _config(tmp_path, [("p", "SELECT 1;")], old_test="true")
    assert runner.invoke(app, ["check", str(tmp_path / "migration-safety-checker.yml")]).exit_code == 0
    _config(tmp_path, [("p", "SELECT 1;")], old_test="definitely-not-a-command-xyz")
    assert runner.invoke(app, ["check", str(tmp_path / "migration-safety-checker.yml")]).exit_code == 2
    _config(
        tmp_path, [("drop", "ALTER TABLE t DROP COLUMN a;")],
        old_test=_script(tmp_path, "old", "c.execute('SELECT a FROM t')\n"),
    )
    assert runner.invoke(app, ["check", str(tmp_path / "migration-safety-checker.yml")]).exit_code == 1


def test_isolation_adds_diagnostic_checks_only_for_multi_phase_rollouts(tmp_path):
    one = _config(tmp_path, [("p", "SELECT 1;")], old_test="true")
    assert not [c for c in build_checks(one, isolate=True) if c.diagnostic]
    two = _config(tmp_path, [("a", "SELECT 1;"), ("b", "SELECT 1;")], old_test="true")
    assert not [c for c in build_checks(two) if c.diagnostic]
    diag = [c for c in build_checks(two, isolate=True) if c.diagnostic]
    assert {c.state.name for c in diag} == {"alone:a", "alone:b"}


@needs_db
def test_isolation_separates_the_phase_that_fails_from_one_that_only_inherits_it(tmp_path):
    # Phase "drop_a" breaks the old app. Phase "add_c" is unrelated, but the rollout
    # table also fails after it, because it runs on top of drop_a.
    cfg = _config(
        tmp_path,
        [("drop_a", "ALTER TABLE t DROP COLUMN a;"), ("add_c", "ALTER TABLE t ADD COLUMN c text;")],
        old_test=_script(tmp_path, "old", "c.execute('SELECT a FROM t')\n"),
    )
    report = run(cfg, isolate=True)
    rollout = {r.check.id: r.status for r in report.results if not r.check.diagnostic}
    assert rollout["old@after:drop_a"] == FAIL and rollout["old@after:add_c"] == FAIL
    alone = {r.check.id: r.status for r in report.diagnostic}
    assert alone["old@alone:drop_a"] == FAIL
    assert alone["old@alone:add_c"] == PASS
    md = to_markdown(report)
    assert "Each phase applied alone on the baseline" in md
    assert len(report.failed) == 2  # diagnostic results are not counted


@needs_db
def test_phase_that_depends_on_an_earlier_one_is_reported_as_not_applicable_alone(tmp_path):
    cfg = _config(
        tmp_path,
        [("add_c", "ALTER TABLE t ADD COLUMN c text;"), ("fill_c", "UPDATE t SET c = a;")],
        old_test="true",
    )
    report = run(cfg, isolate=True)
    fill = [r for r in report.diagnostic if r.check.state.name == "alone:fill_c"]
    assert fill and all(r.status == SKIPPED and "on its own" in r.detail for r in fill if r.check.required)
    assert report.verdict == "SAFE"
