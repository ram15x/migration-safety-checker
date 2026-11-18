"""A tiny stand-in "application" for the evaluation scenarios.

Each application version is described by the SQL it issues. ``write`` steps
are what the version does when it saves data; ``read`` steps are what it does
when it loads data and the assertions are what its code relies on.

    python simapp.py app.json write   # run the write steps
    python simapp.py app.json test    # run the write steps, then the read steps

This models an application by its SQL footprint, which is exactly the part of
an application that a database migration can break. It does not model any
logic outside the database.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg


def _render(value, uid: str):
    return value.replace("{uid}", uid) if isinstance(value, str) else value


def main() -> int:
    path, mode = sys.argv[1], sys.argv[2]
    spec = json.load(open(path))
    uid = uuid.uuid4().hex[:10]
    sections = ["write"] if mode == "write" else ["write", "read"]
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for section in sections:
            for step in spec.get(section, []):
                params = [_render(p, uid) for p in step.get("params", [])]
                cur = conn.execute(step["sql"], params)
                wants_rows = any(k in step for k in ("min_rows", "rows_nonnull", "scalar"))
                if not wants_rows:
                    continue
                rows = cur.fetchall()
                if "min_rows" in step:
                    assert len(rows) >= step["min_rows"], f"expected >= {step['min_rows']} rows, got {len(rows)}"
                if step.get("rows_nonnull"):
                    bad = [r for r in rows if any(v is None or v == "" for v in r)]
                    assert not bad, f"rows with empty values: {bad[:3]}"
                if "scalar" in step:
                    assert rows[0][0] == step["scalar"], f"expected {step['scalar']}, got {rows[0][0]}"
    print(f"{os.environ.get('MSC_APP', '?')} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
