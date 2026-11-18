"""The Django adapter: migration diffing, rendering and failure messages."""

import sys
from pathlib import Path

import pytest

from migration_safety_checker.adapters import django as adapter
from migration_safety_checker.adapters.django import AdapterError, Migration, Options, Release
from migration_safety_checker.config import load


def _opts(tmp_path: Path, **kw) -> Options:
    for side in ("old", "new"):
        (tmp_path / side).mkdir(exist_ok=True)
        (tmp_path / side / "manage.py").write_text("")
    return Options(
        old=Release(tmp_path / "old", sys.executable), new=Release(tmp_path / "new", sys.executable),
        settings="proj.settings", out=tmp_path / "out", test_labels="app.tests", **kw,
    )


def test_new_migrations_keeps_only_those_missing_from_old():
    old = [Migration("a", "0001_x", []), Migration("a", "0002_y", [])]
    new = old + [Migration("a", "0003_z", ["AddField"]), Migration("b", "0001_q", [])]
    assert [m.name for m in adapter.new_migrations(old, new)] == ["0003_z", "0001_q"]


def test_notes_flag_breaking_and_data_operations():
    m = Migration("a", "0004", ["RemoveField", "RunPython", "AddField"])
    assert m.notes == ["RemoveField: removes a column", "RunPython: data migration"]


def test_generate_writes_a_loadable_config(tmp_path, monkeypatch):
    seen = {
        "old": [Migration("app", "0001_initial", ["CreateModel"])],
        "new": [
            Migration("app", "0001_initial", ["CreateModel"]),
            Migration("app", "0002_add", ["AddField"]),
            Migration("app", "0003_backfill", ["RunPython"]),
            Migration("app", "0004_drop", ["RemoveField"]),
        ],
    }
    monkeypatch.setattr(
        adapter, "discover",
        lambda rel, settings, env: seen["old" if rel.directory.name == "old" else "new"],
    )
    path, phases = adapter.generate(_opts(tmp_path))
    assert [p.name for p in phases] == ["0002_add", "0003_backfill", "0004_drop"]
    cfg = load(path)
    assert [p.name for p in cfg.phases] == ["app.0002_add", "app.0003_backfill", "app.0004_drop"]
    assert "migrate app 0004_drop" in cfg.phases[2].run[0]
    assert "app.tests" in cfg.old.test and str(tmp_path / "old") in cfg.old.test
    text = path.read_text()
    assert "RemoveField: removes a column" in text
    assert "add partial_sql" in text
    overlay = (tmp_path / "out" / "msc_settings.py").read_text()
    assert "from proj.settings import *" in overlay and "database_from_env" in overlay


def test_generate_rejects_a_release_with_nothing_new(tmp_path, monkeypatch):
    same = [Migration("app", "0001_initial", [])]
    monkeypatch.setattr(adapter, "discover", lambda *a, **k: same)
    with pytest.raises(AdapterError, match="no migrations"):
        adapter.generate(_opts(tmp_path))


def test_generate_rejects_a_checkout_without_manage_py(tmp_path):
    opts = _opts(tmp_path)
    (tmp_path / "new" / "manage.py").unlink()
    with pytest.raises(AdapterError, match="new checkout has no manage.py"):
        adapter.generate(opts)


def test_discover_reports_the_project_error(tmp_path):
    rel = Release(tmp_path, sys.executable)  # no Django, no settings
    with pytest.raises(AdapterError, match="could not load migrations"):
        adapter.discover(rel, "nope.settings", {})
