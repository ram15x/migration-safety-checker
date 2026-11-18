# Alembic application with more than 30 migrations: Mealie

Source: <https://github.com/mealie-recipes/mealie>, tags v3.18.0 (`8b9149a1`), v3.19.0 (`d70978cd`),
v3.25.0 (`932fcae7`) and v3.26.0 (`84b2677f`).

Checked before relying on it: the repository holds 66 Alembic revisions in `mealie/alembic/versions` at the
latest tag and 111 tags; `mealie/alembic/env.py` is a plain Alembic `env.py` that takes its URL from the
application settings; the Postgres provider is in `mealie/core/settings/db_providers.py` (psycopg2, `POSTGRES_*`
variables). Other candidates looked at and not used: Redash (28 revisions, under 30), CTFd (35 revisions, but
Flask-Migrate: `env.py` needs a Flask application, and `create_app` runs the migrations itself), Apache Superset
and Indico (410 and 178 revisions; their test suites manage their own databases and were not attempted).

Expected results are in `labels.yml`, written from the migrations before the tool ran on any Mealie case.

**This is not a list of upstream bugs.** Mealie migrates itself when it starts. A rolling deploy with the previous
release serving during the migration is not how it is designed to ship.

## The check used, and why it is weak

Mealie's own test suite cannot be pointed at a prepared database state: `tests/conftest.py` runs the migrations
itself when it is imported and under pytest-xdist the Postgres mode creates its own per-worker databases. So the application-level
check is `smoke_read.py`: a read-only script that selects from every table the release's own ORM models map. It
catches a column the model needs but the schema lacks (drop, rename, type the model cannot load). It does **not**
write, so it cannot see a NOT NULL column that breaks inserts, and no write or read command is configured, so the
live-data, forward and rollback checks did not run. The baseline tables are empty.

## Results

Run with `--migrate-first` (every revision is applied before the new release starts, which matches how Mealie
migrates). `results-migrate-first.json`; the default-mode run is `results-default-mode.json`.

| Case | Revisions | Label | Tool | Matches |
|---|---|---|---|---|
| v3.18.0 to v3.19.0 | 1 (three new tables) | SAFE | SAFE | yes |
| v3.19.0 to v3.25.0 | 1 (nullable users column) | SAFE | SAFE | yes |
| v3.25.0 to v3.26.0 | 4 (nullable column, two new tables, an image-reference data backfill, link table plus nullable column filled with UUIDs) | SAFE | SAFE | yes |
| MM1 drop `users.full_name` | mutant of v3.25.0 | UNSAFE | UNSAFE | yes |
| MM2 rename `recipes.name` | mutant | UNSAFE | UNSAFE | yes |
| MM3 NOT NULL, no default | mutant | UNSAFE | **SAFE (miss, predicted)** | the check never writes |
| MM4 NOT NULL with server default | mutant | SAFE | SAFE | yes |

In default mode (the new release required after every revision) v3.25.0 to v3.26.0 is reported UNSAFE: the new
release's models cannot be read after the first of its four revisions, three failing checks, cause `schema`. That
is the mode's assumption, not a hazard of the migrations, and is why `--migrate-first` is the right mode here.

## What this does not show

- The three real pairs are SAFE and labelled SAFE, so they show only that the adapter runs Mealie's Alembic
  history on Postgres and raises no false alarm. They give no evidence that the tool finds a real hazard in
  Mealie.
- For the revision that fills `notes.reference_id` for existing rows, notes written by the old release after the
  backfill keep NULL there. Whether the new release copes is a data-semantics question no check here can see.
- The mutants are mine; 2 of 3 UNSAFE ones are caught and the third is a predicted miss.
- `python-ldap` could not be compiled in the workspace and was left out of the four virtualenvs; the smoke test
  does not import it. `psycopg2-binary` was added, as it is an optional extra of Mealie.
- The Mealie checks run on Python 3.12 (the tags pin it) against Postgres 16.

## Setup problems met before the tool ran

Missing `psycopg2` and the required `PRODUCTION` setting made the first manual migration attempts fail. The
adapter runs received `PRODUCTION`, `DB_ENGINE` and `DATA_DIR` through `--env`. None of the tool runs above hit a
harness failure.
