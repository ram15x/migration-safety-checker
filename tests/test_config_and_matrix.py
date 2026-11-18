from pathlib import Path

import pytest

from migration_safety_checker.config import ConfigError, load
from migration_safety_checker.matrix import build_checks, build_states

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "rename-column"


@pytest.fixture(autouse=True)
def admin_url(monkeypatch):
    monkeypatch.setenv("MSC_ADMIN_URL", "postgresql://localhost/postgres")


def test_loads_example_config():
    cfg = load(EXAMPLES / "safe.yml")
    assert cfg.name == "rename-column-safe"
    assert [p.name for p in cfg.phases] == ["expand", "contract"]
    assert cfg.phases[1].old_may_run is False


def test_states_follow_phase_order():
    cfg = load(EXAMPLES / "safe.yml")
    names = [s.name for s in build_states(cfg)]
    assert names == ["baseline", "after:expand", "after:contract"]


def test_old_app_not_required_after_it_is_retired():
    cfg = load(EXAMPLES / "safe.yml")
    checks = {c.id: c for c in build_checks(cfg)}
    assert checks["old@baseline"].required
    assert checks["old@after:expand"].required
    assert not checks["old@after:contract"].required
    assert checks["new@after:contract"].required


def test_rollback_only_where_both_versions_can_run():
    cfg = load(EXAMPLES / "safe.yml")
    checks = {c.id: c for c in build_checks(cfg)}
    assert checks["rollback@after:expand"].required
    assert not checks["rollback@baseline"].required
    assert not checks["rollback@after:contract"].required


def test_partial_state_is_inserted_before_its_phase(tmp_path):
    (tmp_path / "base.sql").write_text("select 1;")
    (tmp_path / "p.sql").write_text("select 1;")
    (tmp_path / "half.sql").write_text("select 1;")
    (tmp_path / "msc.yml").write_text(
        """
baseline: {sql: [base.sql]}
phases:
  - name: backfill
    sql: [p.sql]
    partial_sql: [half.sql]
apps:
  old: {test: "true"}
  new: {test: "true"}
"""
    )
    names = [s.name for s in build_states(load(tmp_path / "msc.yml"))]
    assert names == ["baseline", "partial:backfill", "after:backfill"]


def test_missing_baseline_is_an_error(tmp_path):
    (tmp_path / "msc.yml").write_text("phases: [{name: a, sql: [x.sql]}]\napps: {old: {test: t}, new: {test: t}}\n")
    with pytest.raises(ConfigError, match="baseline"):
        load(tmp_path / "msc.yml")


def test_missing_sql_file_is_reported(tmp_path):
    (tmp_path / "base.sql").write_text("select 1;")
    (tmp_path / "msc.yml").write_text(
        "baseline: {sql: [base.sql]}\nphases: [{name: a, sql: [nope.sql]}]\napps: {old: {test: t}, new: {test: t}}\n"
    )
    with pytest.raises(ConfigError, match="nope.sql"):
        load(tmp_path / "msc.yml")


def test_unset_env_var_without_default_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MSC_ADMIN_URL")
    with pytest.raises(ConfigError, match="MSC_ADMIN_URL"):
        load(EXAMPLES / "safe.yml")


def test_duplicate_phase_names_rejected(tmp_path):
    (tmp_path / "base.sql").write_text("select 1;")
    (tmp_path / "p.sql").write_text("select 1;")
    (tmp_path / "msc.yml").write_text(
        "baseline: {sql: [base.sql]}\nphases: [{name: a, sql: [p.sql]}, {name: a, sql: [p.sql]}]\n"
        "apps: {old: {test: t}, new: {test: t}}\n"
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load(tmp_path / "msc.yml")


def test_forward_check_needs_overlap_and_an_old_write_command():
    cfg = load(EXAMPLES / "safe.yml")
    checks = {c.id: c for c in build_checks(cfg)}
    assert checks["forward@after:expand"].required
    assert not checks["forward@baseline"].required
    assert not checks["forward@after:contract"].required
