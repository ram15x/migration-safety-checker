"""The Alembic adapter: revision diffing, operation notes, rendering and an end-to-end run."""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from migration_safety_checker.adapters import alembic as adapter
from migration_safety_checker.adapters.alembic import Options, Release, Revision
from migration_safety_checker.adapters.django import AdapterError
from migration_safety_checker.config import load

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "alembic"


def _opts(tmp_path: Path, **kw) -> Options:
    for side in ("old", "new"):
        (tmp_path / side).mkdir(exist_ok=True)
        (tmp_path / side / "alembic.ini").write_text("")
    return Options(
        old=Release(tmp_path / "old", sys.executable, "pytest -q"),
        new=Release(tmp_path / "new", sys.executable, "pytest -q"),
        out=tmp_path / "out", **kw,
    )


def test_new_revisions_keeps_only_those_missing_from_old():
    old = [Revision("a1", "first", [])]
    new = old + [Revision("b2", "second", ["add_column"]), Revision("c3", "third", [])]
    assert [r.rev for r in adapter.new_revisions(old, new)] == ["b2", "c3"]


def test_notes_flag_breaking_and_data_operations():
    r = Revision("b2", "x", ["drop_column", "execute", "add_column", "alter_column[not-null]"])
    assert r.notes == [
        "drop_column: removes a column",
        "alter_column[not-null]: makes a column NOT NULL; old writes and existing NULLs can break it",
        "execute: raw SQL or data migration",
    ]


def test_label_is_readable_and_bounded():
    assert Revision("ae1027a6acf", "Add phone number!", []).label == "ae1027a6acf_add_phone_number"
    assert Revision("ae1", "", []).label == "ae1"
    assert len(Revision("x" * 12, "word " * 40, []).label) <= 60


def test_generate_writes_a_loadable_config(tmp_path, monkeypatch):
    seen = {
        "old": [Revision("r1", "create", ["create_table"])],
        "new": [
            Revision("r1", "create", ["create_table"]),
            Revision("r2", "add col", ["add_column[not-null-no-default]"]),
            Revision("r3", "backfill", ["execute"]),
            Revision("r4", "drop old", ["drop_column"]),
        ],
    }
    monkeypatch.setattr(adapter, "discover", lambda rel, opts: seen["old" if rel.directory.name == "old" else "new"])
    path, phases = adapter.generate(_opts(tmp_path, seed="python seed.py", write="python w.py", env={"K": "v"}))
    assert [p.rev for p in phases] == ["r2", "r3", "r4"]
    cfg = load(path)
    assert [p.name for p in cfg.phases] == ["r2_add_col", "r3_backfill", "r4_drop_old"]
    assert "upgrade r4" in cfg.phases[2].run[0]
    assert "upgrade heads" in cfg.baseline_run[0] and cfg.baseline_run[1].endswith("exec 'python seed.py'")
    assert " exec 'pytest -q'" in cfg.old.test and f"PYTHONPATH={tmp_path / 'old'}" in cfg.old.test and str(tmp_path / "old") in cfg.old.test
    assert str(tmp_path / "new") in cfg.new.write and "K=v" in cfg.new.write
    text = path.read_text()
    assert "drop_column: removes a column" in text and "add partial_run" in text
    assert (tmp_path / "out" / adapter.WRAPPER_NAME).is_file()


def test_python_placeholder_is_replaced_per_release(tmp_path, monkeypatch):
    opts = _opts(tmp_path)
    opts.old.python, opts.new.python = "/venv/old/bin/python", "/venv/new/bin/python"
    opts.old.test = opts.new.test = "{python} -m pytest"
    monkeypatch.setattr(adapter, "discover", lambda rel, o: [Revision("r1", "x", [])] + ([Revision("r2", "y", [])] if rel is opts.new else []))
    cfg = load(adapter.generate(opts)[0])
    assert "exec '/venv/old/bin/python -m pytest'" in cfg.old.test
    assert "exec '/venv/new/bin/python -m pytest'" in cfg.new.test


def test_migrate_first_requires_the_new_release_only_after_the_last_revision(tmp_path, monkeypatch):
    seen = {"old": [Revision("r1", "a", [])], "new": [Revision("r1", "a", []), Revision("r2", "b", []), Revision("r3", "c", [])]}
    monkeypatch.setattr(adapter, "discover", lambda rel, o: seen["old" if rel.directory.name == "old" else "new"])
    both = load(adapter.generate(_opts(tmp_path))[0])
    assert [p.new_may_run for p in both.phases] == [True, True]
    first = load(adapter.generate(_opts(tmp_path, migrate_first=True))[0])
    assert [p.new_may_run for p in first.phases] == [False, True]
    assert [p.old_may_run for p in first.phases] == [True, True]


def test_missing_config_test_command_and_no_new_revisions_are_errors(tmp_path, monkeypatch):
    opts = _opts(tmp_path)
    (tmp_path / "new" / "alembic.ini").unlink()
    with pytest.raises(AdapterError, match="no alembic.ini"):
        adapter.generate(opts)
    (tmp_path / "new" / "alembic.ini").write_text("")
    opts.new.test = " "
    with pytest.raises(AdapterError, match="no test command"):
        adapter.generate(opts)
    opts.new.test = "pytest"
    monkeypatch.setattr(adapter, "discover", lambda rel, o: [Revision("r1", "x", [])])
    with pytest.raises(AdapterError, match="nothing to check"):
        adapter.generate(opts)


def test_discover_reports_a_project_that_cannot_be_loaded(tmp_path):
    pytest.importorskip("alembic")
    opts = _opts(tmp_path)
    opts.out.mkdir()
    (opts.out / adapter.WRAPPER_NAME).write_text(adapter.WRAPPER)
    with pytest.raises(AdapterError, match="could not load revisions"):
        adapter.discover(opts.old, opts)


def _wrapper_namespace():
    pytest.importorskip("alembic")
    ns: dict = {"__name__": "wrapper"}
    exec(adapter.WRAPPER.replace("main(sys.argv)", ""), ns)
    return ns


def test_wrapper_reads_operations_from_upgrade(tmp_path):
    ns = _wrapper_namespace()
    f = tmp_path / "rev.py"
    f.write_text(
        "import sqlalchemy as sa\nfrom alembic import op\n\n"
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('a', sa.Text, nullable=False))\n"
        "    op.add_column('t', sa.Column('b', sa.Text, nullable=False, server_default='x'))\n"
        "    op.add_column('t', sa.Column('c', sa.Text))\n"
        "    op.alter_column('t', 'a', nullable=False)\n"
        "    op.alter_column('t', 'b', new_column_name='bb')\n"
        "    op.alter_column('t', 'c', type_=sa.Integer)\n"
        "    with op.batch_alter_table('t') as batch_op:\n"
        "        batch_op.drop_column('c')\n"
        "    op.execute('update t set a = 1')\n\n"
        "def downgrade():\n    op.drop_column('t', 'zzz')\n"
    )
    assert ns["operations"](str(f)) == [
        "add_column[not-null-no-default]", "add_column", "add_column", "alter_column[not-null]",
        "alter_column[rename]", "alter_column[type]", "batch_alter_table", "drop_column", "execute",
    ]
    assert ns["operations"](str(tmp_path / "missing.py")) == []


def test_wrapper_builds_the_sqlalchemy_url(monkeypatch):
    ns = _wrapper_namespace()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.delenv("MIGRATION_SAFETY_CHECKER_DRIVER", raising=False)
    assert ns["sqlalchemy_url"]() == "postgresql://u:p@h:5432/d"
    monkeypatch.setenv("MIGRATION_SAFETY_CHECKER_DRIVER", "psycopg")
    assert ns["sqlalchemy_url"]() == "postgresql+psycopg://u:p@h:5432/d"


def test_wrapper_exports_url_parts_and_runs_a_command_with_them(tmp_path, monkeypatch):
    ns = _wrapper_namespace()
    monkeypatch.setenv("DATABASE_URL", "postgresql://us%40er:p%3Aw@db.local:6543/state_1")
    monkeypatch.setenv("MIGRATION_SAFETY_CHECKER_DRIVER", "psycopg")
    monkeypatch.setenv("MIGRATION_SAFETY_CHECKER_URL_ENV", "DATABASE_URL,APP_DB")
    monkeypatch.setenv("MIGRATION_SAFETY_CHECKER_URL_PARTS", "PGH=host,PGP=port,PGU=user,PGW=password,PGD=dbname")
    for name in ("APP_DB", "PGH", "PGP", "PGU", "PGW", "PGD"):
        monkeypatch.delenv(name, raising=False)
    url = ns["apply_environment"]()
    assert url == "postgresql+psycopg://us%40er:p%3Aw@db.local:6543/state_1"
    assert os.environ["APP_DB"] == url
    assert [os.environ[k] for k in ("PGH", "PGP", "PGU", "PGW", "PGD")] == ["db.local", "6543", "us@er", "p:w", "state_1"]


def test_init_alembic_rejects_a_bad_url_part(tmp_path):
    from typer.testing import CliRunner

    from migration_safety_checker.cli import app

    res = CliRunner().invoke(app, [
        "init-alembic", "--old", str(tmp_path), "--new", str(tmp_path), "--test", "true", "--url-part", "X=bogus",
    ])
    assert res.exit_code == 2 and "--url-part expects" in res.output


needs_stack = pytest.mark.skipif(
    not os.environ.get("MIGRATION_SAFETY_CHECKER_ADMIN_URL")
    or not all(importlib.util.find_spec(m) for m in ("alembic", "sqlalchemy")),
    reason="needs MIGRATION_SAFETY_CHECKER_ADMIN_URL and alembic + sqlalchemy installed",
)


def _run_example(tmp_path, new: str):
    from migration_safety_checker.runner import run

    opts = Options(
        old=Release(EXAMPLES / "v1", sys.executable, f"{sys.executable} -m pytest -q test_app.py"),
        new=Release(EXAMPLES / new, sys.executable, f"{sys.executable} -m pytest -q test_app.py"),
        out=tmp_path, driver="psycopg",
        write=f"{sys.executable} app.py write", read=f"{sys.executable} app.py read",
    )
    path, phases = adapter.generate(opts)
    assert [p.rev for p in phases] == ["0002"]
    return run(load(path))


@needs_stack
def test_example_unsafe_add_not_null_column_is_caught(tmp_path):
    report = _run_example(tmp_path, "v2-unsafe")
    assert report.verdict == "UNSAFE"
    assert {r.check.id for r in report.failed} == {
        "live@before:0002_add_phone", "old@after:0002_add_phone", "forward@after:0002_add_phone",
    }


@needs_stack
def test_example_nullable_add_column_is_safe(tmp_path):
    report = _run_example(tmp_path, "v2-safe")
    assert report.safe, [r.detail for r in report.failed]
