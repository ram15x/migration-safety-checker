"""Scratch database management.

Every check runs in its own database cloned from a template that holds the
old release's schema and data, so checks cannot influence one another.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from urllib.parse import quote

from psycopg.conninfo import conninfo_to_dict, make_conninfo


class Database:
    def __init__(self, admin_url: str) -> None:
        self.admin_url = admin_url
        self._run_id = uuid.uuid4().hex[:8]
        self._counter = 0
        self._created: list[str] = []
        self._lock = threading.Lock()

    # -- connections -----------------------------------------------------
    def url(self, dbname: str) -> str:
        """A ``postgresql://`` URL for ``dbname``, the form application tooling expects."""
        parts = conninfo_to_dict(make_conninfo(self.admin_url, dbname=dbname))
        auth = quote(str(parts.get("user", "")), safe="")
        if parts.get("password"):
            auth += ":" + quote(str(parts["password"]), safe="")
        host = str(parts.get("host", "localhost"))
        query: list[str] = []
        if host.startswith("/"):  # unix socket directory
            query.append("host=" + quote(host, safe="/"))
            host = ""
        elif ":" in host:
            host = f"[{host}]"
        port = f":{parts['port']}" if parts.get("port") else ""
        if parts.get("sslmode"):
            query.append("sslmode=" + quote(str(parts["sslmode"]), safe=""))
        qs = "?" + "&".join(query) if query else ""
        at = "@" if auth else ""
        return f"postgresql://{auth}{at}{host}{port}/{quote(dbname, safe='')}{qs}"

    def _admin(self) -> psycopg.Connection:
        return psycopg.connect(self.admin_url, autocommit=True)

    def _connect(self, dbname: str) -> psycopg.Connection:
        return psycopg.connect(self.url(dbname), autocommit=True)

    # -- lifecycle -------------------------------------------------------
    def new_name(self, label: str) -> str:
        self._counter += 1
        safe = "".join(c if c.isalnum() else "_" for c in label)[:12]
        return f"migration_safety_checker_{self._run_id}_{self._counter}_{safe}".lower()

    def create(self, name: str, template: str | None = None) -> None:
        if template:
            stmt = sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(template)
            )
        else:
            stmt = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
        for attempt in range(6):
            try:
                with self._admin() as conn:
                    conn.execute(stmt)
                break
            except (psycopg.errors.ObjectInUse, psycopg.errors.LockNotAvailable,
                    psycopg.errors.SerializationFailure):
                # concurrent clones of one template can briefly collide
                if attempt == 5:
                    raise
                time.sleep(0.2 * (attempt + 1))
        with self._lock:
            self._created.append(name)

    def drop(self, name: str) -> None:
        with self._admin() as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
        with self._lock:
            if name in self._created:
                self._created.remove(name)

    def cleanup(self) -> None:
        with self._lock:
            pending = list(self._created)
        for name in pending:
            try:
                self.drop(name)
            except psycopg.Error:
                pass

    # -- work ------------------------------------------------------------
    def apply_files(self, dbname: str, root: Path, files: list[str]) -> None:
        with self._connect(dbname) as conn:
            for rel in files:
                conn.execute((root / rel).read_text())

    def violations(self, dbname: str, query: str) -> list[tuple]:
        with self._connect(dbname) as conn:
            return conn.execute(query).fetchall()
