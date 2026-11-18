"""Release 2 of the application. It reads and writes users.given_name."""
import os
import sys
import uuid

import psycopg

mode = sys.argv[1]
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
    conn.execute(
        "INSERT INTO users (given_name, last_name, email) VALUES (%s, %s, %s)",
        ("Margaret", "Hamilton", f"v2-{uuid.uuid4().hex}@example.com"),
    )
    if mode == "test":
        rows = conn.execute("SELECT id, given_name, last_name FROM users ORDER BY id").fetchall()
        assert rows, "no users returned"
        missing = [r[0] for r in rows if not r[1] or not r[2]]
        assert not missing, f"users with empty names: {missing}"
        print(f"v2 ok: {len(rows)} users")
