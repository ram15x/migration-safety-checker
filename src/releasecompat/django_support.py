"""Helpers for running a Django project's own tests against a prepared database.

Django's test runner builds its own database by running the project's
migrations, which hides exactly the states ReleaseCompat constructs. These
helpers point the runner at the database named in ``DATABASE_URL`` and skip
database creation. Use them from a small settings module:

    from myproject.settings import *
    from releasecompat.django_support import database_from_env
    DATABASES = {"default": database_from_env()}
    TEST_RUNNER = "releasecompat.django_support.SchemaCloneRunner"

The module needs only Django and the standard library, so it can be put on
``PYTHONPATH`` of any application virtualenv.
"""

from __future__ import annotations

import os
import shlex
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from django.db import connections
from django.test.runner import DiscoverRunner


def database_from_env(var: str = "DATABASE_URL") -> dict[str, Any]:
    """Build a Django ``DATABASES`` entry from a PostgreSQL URL or libpq string."""
    raw = os.environ.get(var)
    if not raw:
        raise RuntimeError(f"{var} is not set")
    if "://" in raw:
        u = urlparse(raw)
        host = parse_qs(u.query).get("host", [u.hostname or ""])[0]
        name, user, password = u.path.lstrip("/"), u.username, u.password
        port = u.port
    else:
        kv = dict(item.split("=", 1) for item in shlex.split(raw))
        name, user, password = kv.get("dbname", ""), kv.get("user"), kv.get("password")
        host, port = kv.get("host", ""), kv.get("port")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(name),
        "USER": unquote(user) if user else "",
        "PASSWORD": unquote(password) if password else "",
        "HOST": host or "",
        "PORT": str(port) if port else "",
    }


class SchemaCloneMixin:
    """Run the suite on an empty copy of the prepared database's schema.

    Most suites assume empty tables. The prepared database keeps its rows, so
    the runner clones it, empties the clone's application tables, runs the
    tests there and drops the clone. Django bookkeeping tables and permissions
    are kept. The migrations are not run: the tests see exactly the schema
    ReleaseCompat built for this state.

    Projects with their own ``DiscoverRunner`` subclass combine the two:
    ``class Runner(SchemaCloneMixin, TheirRunner): pass``.
    """

    _KEEP = ("django_", "auth_permission")

    def setup_databases(self, **kwargs: Any) -> list[Any]:
        conn = connections["default"]
        source = conn.settings_dict["NAME"]
        clone = f"{source}_t"
        conn.close()
        with conn._nodb_cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{clone}" TEMPLATE "{source}"')
        conn.settings_dict["NAME"] = clone
        with conn.cursor() as cur:
            cur.execute(
                "SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname = 'public'"
            )
            tables = [r[0] for r in cur.fetchall() if not r[0].startswith(self._KEEP)]
            if tables:
                cur.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
        return [(source, clone)]

    def teardown_databases(self, old_config: Any, **kwargs: Any) -> None:
        conn = connections["default"]
        for source, clone in old_config:
            conn.close()
            conn.settings_dict["NAME"] = source
            with conn._nodb_cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)')


class SchemaCloneRunner(SchemaCloneMixin, DiscoverRunner):
    """``SchemaCloneMixin`` on Django's default runner."""
