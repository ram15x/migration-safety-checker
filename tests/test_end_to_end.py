"""End-to-end tests. They need a reachable Postgres server in MIGRATION_SAFETY_CHECKER_ADMIN_URL."""

import os
from pathlib import Path

import pytest

from migration_safety_checker.config import load
from migration_safety_checker.report import to_json, to_junit, to_markdown
from migration_safety_checker.runner import run

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "rename-column"

pytestmark = pytest.mark.skipif(
    not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL"), reason="MIGRATION_SAFETY_CHECKER_ADMIN_URL is not set"
)


def test_expand_contract_rename_is_safe():
    report = run(load(EXAMPLES / "safe.yml"))
    assert report.safe, [r.detail for r in report.failed]
    assert len(report.required) == 6


def test_direct_rename_is_unsafe_for_old_app_and_rollback():
    report = run(load(EXAMPLES / "unsafe.yml"))
    assert not report.safe
    assert {r.check.id for r in report.failed} == {
        "old@after:rename",
        "forward@after:rename",
        "rollback@after:rename",
    }


def test_reports_render():
    report = run(load(EXAMPLES / "unsafe.yml"))
    assert "Verdict: UNSAFE" in to_markdown(report)
    assert '"verdict": "UNSAFE"' in to_json(report)
    assert "<failure" in to_junit(report)


def test_scratch_databases_are_cleaned_up():
    import psycopg

    run(load(EXAMPLES / "safe.yml"))
    with psycopg.connect(os.environ["MIGRATION_SAFETY_CHECKER_ADMIN_URL"], autocommit=True) as conn:
        leftovers = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'migration_safety_checker\\_%'"
        ).fetchall()
    assert leftovers == []
