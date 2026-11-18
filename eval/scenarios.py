"""The evaluation scenarios and the generator that turns them into config folders.

Every scenario has a ground-truth label, written down BEFORE any tool ran:

  unsafe: at least one application version that is allowed to run in some
          deployment state would fail, or would misread data written by the other version.
  safe:   every allowed combination works.

The labels come from the scenario design, not from any tool output. They were
written by the project author and have not been reviewed by an independent engineer.

    python scenarios.py            # writes eval/scenarios/<id>/...
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "scenarios"

BASE_SQL = """
CREATE TABLE users (
    id          serial PRIMARY KEY,
    first_name  text NOT NULL,
    last_name   text NOT NULL,
    email       text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'active'
                CONSTRAINT users_status_check CHECK (status IN ('active', 'inactive')),
    nickname    text,
    legacy_flag boolean,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
    id      serial PRIMARY KEY,
    user_id int NOT NULL REFERENCES users(id),
    qty     int NOT NULL
);
INSERT INTO users (first_name, last_name, email) VALUES
    ('Grace', 'Hopper', 'grace@x.test'),
    ('Alan', 'Turing', 'alan@x.test'),
    ('Katherine', 'Johnson', 'katherine@x.test');
INSERT INTO orders (user_id, qty) VALUES (1, 2), (2, 5);
"""

PEOPLE_SQL = """
CREATE TABLE people (
    id        serial PRIMARY KEY,
    full_name text NOT NULL,
    email     text NOT NULL UNIQUE
);
INSERT INTO people (full_name, email) VALUES
    ('Grace Hopper', 'grace-p@x.test'),
    ('Alan Turing', 'alan-p@x.test');
"""


# -- helpers -----------------------------------------------------------------
def ins(table: str, **cols):
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return {"sql": f"INSERT INTO {table} ({names}) VALUES ({marks})", "params": list(cols.values())}


def sel(sql: str, **assertions):
    return {"sql": sql, **assertions}


def users_app(first="first_name", last="last_name", table="users", extra=None, read_extra=None,
              fixed_name=None, status_check=False):
    """An application version that saves and loads users."""
    cols = {
        first: fixed_name[0] if fixed_name else "Ada-{uid}",
        last: fixed_name[1] if fixed_name else "Lovelace",
        "email": "app-{uid}@x.test",
    }
    cols.update(extra or {})
    read = [sel(f"SELECT id, {first}, {last} FROM {table}", min_rows=1, rows_nonnull=True)]
    read += read_extra or []
    if status_check:
        read.append(sel("SELECT count(*) FROM users WHERE status NOT IN ('active', 'inactive')", scalar=0))
    return {"write": [ins(table, **cols)], "read": read}


@dataclass
class Phase:
    name: str
    sql: str
    partial_sql: str | None = None
    old_may_run: bool = True
    new_may_run: bool = True


@dataclass
class Scenario:
    id: str
    label: str  # "safe" | "unsafe"
    why: str
    phases: list[Phase]
    old: dict
    new: dict
    extra_baseline: str = ""
    invariants: list[dict] = field(default_factory=list)


SYNC_SURNAME = """
CREATE FUNCTION users_sync_surname() RETURNS trigger AS $$
BEGIN
    IF NEW.surname IS NULL THEN NEW.surname := NEW.last_name; END IF;
    IF NEW.last_name IS NULL THEN NEW.last_name := NEW.surname; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER users_sync_surname BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_sync_surname();
"""

SYNC_GIVEN = """
CREATE FUNCTION users_sync_given() RETURNS trigger AS $$
BEGIN
    IF NEW.given_name IS NULL THEN NEW.given_name := NEW.first_name; END IF;
    IF NEW.first_name IS NULL THEN NEW.first_name := NEW.given_name; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER users_sync_given BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_sync_given();
"""

SYNC_PEOPLE = """
CREATE FUNCTION people_sync() RETURNS trigger AS $$
BEGIN
    IF NEW.full_name IS NULL THEN
        NEW.full_name := NEW.first_name || ' ' || NEW.last_name;
    END IF;
    IF NEW.first_name IS NULL OR NEW.last_name IS NULL THEN
        NEW.first_name := split_part(NEW.full_name, ' ', 1);
        NEW.last_name  := split_part(NEW.full_name, ' ', 2);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER people_sync BEFORE INSERT OR UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION people_sync();
"""

PEOPLE_EXPAND = """
ALTER TABLE people ADD COLUMN first_name text, ADD COLUMN last_name text;
UPDATE people SET first_name = split_part(full_name, ' ', 1), last_name = split_part(full_name, ' ', 2);
"""


def people_old():
    return {
        "write": [ins("people", full_name="Old Person-{uid}", email="po-{uid}@x.test")],
        "read": [sel("SELECT id, full_name FROM people", min_rows=1, rows_nonnull=True)],
    }


def people_new():
    return {
        "write": [ins("people", first_name="New-{uid}", last_name="Person", email="pn-{uid}@x.test")],
        "read": [sel("SELECT id, first_name, last_name FROM people", min_rows=1, rows_nonnull=True)],
    }


def orders_app():
    return {
        "write": [ins("orders", user_id=1, qty=7)],
        "read": [sel("SELECT id, qty FROM orders", min_rows=1, rows_nonnull=True)],
    }


SCENARIOS: list[Scenario] = [
    Scenario(
        "01_rename_column_direct", "unsafe",
        "Column renamed in a single step. Old instances still use last_name.",
        [Phase("rename", "ALTER TABLE users RENAME COLUMN last_name TO surname;")],
        users_app(), users_app(last="surname"),
    ),
    Scenario(
        "02_rename_expand_contract", "safe",
        "Column renamed with expand, a sync trigger, and a contract step after the old version is retired.",
        [
            Phase("expand", "ALTER TABLE users ADD COLUMN surname text;\n" + SYNC_SURNAME
                  + "UPDATE users SET surname = last_name;"),
            Phase("contract", "DROP TRIGGER users_sync_surname ON users;\nDROP FUNCTION users_sync_surname();\n"
                  "ALTER TABLE users DROP COLUMN last_name;", old_may_run=False),
        ],
        users_app(), users_app(last="surname"),
    ),
    Scenario(
        "03_drop_column_in_use", "unsafe",
        "Drops a column that the old version still reads and writes.",
        [Phase("drop", "ALTER TABLE users DROP COLUMN nickname;")],
        users_app(extra={"nickname": "ada"}, read_extra=[sel("SELECT id, nickname FROM users", min_rows=1)]),
        users_app(),
    ),
    Scenario(
        "04_drop_column_unused", "safe",
        "Drops a column that neither version touches.",
        [Phase("drop", "ALTER TABLE users DROP COLUMN legacy_flag;")],
        users_app(), users_app(),
    ),
    Scenario(
        "05_add_nullable_column", "safe",
        "Adds a nullable column. Only the new version uses it.",
        [Phase("add", "ALTER TABLE users ADD COLUMN bio text;")],
        users_app(),
        users_app(extra={"bio": "hello"}, read_extra=[sel("SELECT id, bio FROM users", min_rows=1)]),
    ),
    Scenario(
        "06_add_not_null_no_default", "unsafe",
        "Adds a NOT NULL column without a default. The old version never sets it.",
        [Phase("add", "ALTER TABLE users ADD COLUMN tier text;\nUPDATE users SET tier = 'free';\n"
               "ALTER TABLE users ALTER COLUMN tier SET NOT NULL;")],
        users_app(), users_app(extra={"tier": "pro"}),
    ),
    Scenario(
        "07_add_not_null_with_default", "safe",
        "Adds a NOT NULL column with a default. The old version inherits the default.",
        [Phase("add", "ALTER TABLE users ADD COLUMN tier text NOT NULL DEFAULT 'free';")],
        users_app(), users_app(extra={"tier": "pro"}),
    ),
    Scenario(
        "08_widen_int_to_bigint", "safe",
        "Widens an integer column to bigint. Both versions write small integers.",
        [Phase("widen", "ALTER TABLE orders ALTER COLUMN qty TYPE bigint;")],
        orders_app(), orders_app(),
    ),
    Scenario(
        "09_incompatible_type_change", "unsafe",
        "Changes status from text to integer. The old version still writes text.",
        [Phase("retype", "ALTER TABLE users DROP CONSTRAINT users_status_check;\n"
               "ALTER TABLE users ALTER COLUMN status DROP DEFAULT;\n"
               "ALTER TABLE users ALTER COLUMN status TYPE integer USING (CASE status WHEN 'active' THEN 1 ELSE 0 END);\n"
               "ALTER TABLE users ALTER COLUMN status SET DEFAULT 1;")],
        users_app(extra={"status": "active"}), users_app(extra={"status": 1}),
    ),
    Scenario(
        "10_check_rejects_old_values", "unsafe",
        "Tightens a CHECK so it no longer accepts a value the old version still writes.",
        [Phase("tighten", "ALTER TABLE users DROP CONSTRAINT users_status_check;\n"
               "ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active', 'suspended'));")],
        users_app(extra={"status": "inactive"}), users_app(extra={"status": "suspended"}),
    ),
    Scenario(
        "11_unique_rejects_old_duplicates", "unsafe",
        "Adds a unique constraint on names. The old version can still create duplicate names.",
        [Phase("unique", "ALTER TABLE users ADD CONSTRAINT users_name_key UNIQUE (first_name, last_name);")],
        users_app(fixed_name=("Alan", "Turing")), users_app(),
    ),
    Scenario(
        "12_split_column_without_sync", "unsafe",
        "Splits full_name into two columns with a one-off backfill and no sync. "
        "Rows written by the old version have empty first/last names.",
        [Phase("expand", PEOPLE_EXPAND + "ALTER TABLE people ALTER COLUMN full_name DROP NOT NULL;")],
        people_old(), people_new(), extra_baseline=PEOPLE_SQL,
    ),
    Scenario(
        "13_split_column_with_sync", "safe",
        "Splits full_name into two columns with a sync trigger and a contract step.",
        [
            Phase("expand", PEOPLE_EXPAND + SYNC_PEOPLE),
            Phase("contract", "DROP TRIGGER people_sync ON people;\nDROP FUNCTION people_sync();\n"
                  "ALTER TABLE people DROP COLUMN full_name;", old_may_run=False),
        ],
        people_old(), people_new(), extra_baseline=PEOPLE_SQL,
    ),
    Scenario(
        "14_rollback_unreadable_new_values", "unsafe",
        "The new version writes status values the old version cannot interpret. "
        "A rollback leaves rows the old code misreads.",
        [Phase("relax", "ALTER TABLE users DROP CONSTRAINT users_status_check;")],
        users_app(status_check=True), users_app(extra={"status": "ACTIVE"}),
    ),
    Scenario(
        "15_rollback_dual_representation", "safe",
        "The new version also writes the old representation, so a rollback is safe.",
        [Phase("add", "ALTER TABLE users ADD COLUMN state text;")],
        users_app(status_check=True), users_app(extra={"status": "active", "state": "ACTIVE"}),
    ),
    Scenario(
        "16_add_index_plain", "safe",
        "Creates an index without CONCURRENTLY. Compatible, though it takes a lock; "
        "this measures compatibility, not locking.",
        [Phase("index", "CREATE INDEX idx_users_email_lower ON users (lower(email));")],
        users_app(), users_app(),
    ),
    Scenario(
        "17_drop_not_null", "safe",
        "Drops a NOT NULL constraint. Both versions still write the value.",
        [Phase("relax", "ALTER TABLE users ALTER COLUMN last_name DROP NOT NULL;")],
        users_app(), users_app(),
    ),
    Scenario(
        "18_rename_table_with_view", "safe",
        "Renames a table and keeps the old name working through an updatable view.",
        [Phase("rename", "ALTER TABLE users RENAME TO accounts;\nCREATE VIEW users AS SELECT * FROM accounts;")],
        users_app(), users_app(table="accounts"),
    ),
    Scenario(
        "19_rename_table_direct", "unsafe",
        "Renames a table in one step. Old instances still query the old name.",
        [Phase("rename", "ALTER TABLE users RENAME TO accounts;")],
        users_app(), users_app(table="accounts"),
    ),
    Scenario(
        "20_partial_backfill_new_reads_only_new", "unsafe",
        "The new version is deployed while a backfill is half done and reads only the new column.",
        [
            Phase("expand", "ALTER TABLE users ADD COLUMN given_name text;\n" + SYNC_GIVEN, new_may_run=False),
            Phase("backfill", "UPDATE users SET given_name = first_name WHERE given_name IS NULL;",
                  partial_sql="UPDATE users SET given_name = first_name WHERE given_name IS NULL AND id % 2 = 0;"),
        ],
        users_app(), users_app(first="given_name"),
    ),
]


def _yaml_list(items: list[str], indent: str) -> str:
    return "[" + ", ".join(items) + "]"


def generate() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for sc in SCENARIOS:
        d = OUT / sc.id
        d.mkdir()
        (d / "baseline.sql").write_text(BASE_SQL + sc.extra_baseline)
        phase_lines = []
        for i, ph in enumerate(sc.phases, start=1):
            fname = f"phase_{i}_{ph.name}.sql"
            (d / fname).write_text(ph.sql.strip() + "\n")
            phase_lines.append(f"  - name: {ph.name}")
            phase_lines.append(f"    sql: [{fname}]")
            if ph.partial_sql:
                pname = f"phase_{i}_{ph.name}_partial.sql"
                (d / pname).write_text(ph.partial_sql.strip() + "\n")
                phase_lines.append(f"    partial_sql: [{pname}]")
            phase_lines.append(f"    old_may_run: {str(ph.old_may_run).lower()}")
            phase_lines.append(f"    new_may_run: {str(ph.new_may_run).lower()}")
        (d / "old.json").write_text(json.dumps(sc.old, indent=2))
        (d / "new.json").write_text(json.dumps(sc.new, indent=2))
        (d / "label.json").write_text(json.dumps({"label": sc.label, "why": sc.why}, indent=2))
        cfg = [
            f"name: {sc.id}",
            "baseline:",
            "  sql: [baseline.sql]",
            "phases:",
            *phase_lines,
            "apps:",
            '  old: { test: "python ../../simapp.py old.json test", write: "python ../../simapp.py old.json write" }',
            '  new: { test: "python ../../simapp.py new.json test", write: "python ../../simapp.py new.json write" }',
        ]
        (d / "releasecompat.yml").write_text("\n".join(cfg) + "\n")
    print(f"generated {len(SCENARIOS)} scenarios in {OUT}")


if __name__ == "__main__":
    generate()
