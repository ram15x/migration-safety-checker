# migration-safety-checker

[![ci](https://github.com/ram15x/migration-safety-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/ram15x/migration-safety-checker/actions/workflows/ci.yml)

Rolling deploys run two versions of your application against one database at the same time. Migration tools check that the migration applies, and your test suite checks that the new code works on the final schema. The question that breaks production is different: **does every version that is allowed to run keep working in every intermediate state of the rollout, and can you roll back?**

Teams often answer part of it by hand, running the previous release's tests against the new schema in CI. migration-safety-checker automates that across every state of the rollout, in both directions. It reads Django migrations and Alembic revisions to build the rollout for you, and ships as a CLI and as a GitHub Action.

## How it works

You describe the rollout as ordered phases (expand, backfill, contract, ...) and say which application versions may run after each one. migration-safety-checker builds a database for every state, runs your real test commands against it, and reports a verdict.

For each state (baseline, mid-backfill, after each phase) it runs up to four checks, and each phase gets one more:

| Check | What runs | Catches |
|---|---|---|
| old | old app's tests | the migration broke the version still serving traffic |
| new | new app's tests | the new version needs something the migration hasn't delivered yet |
| forward | old app writes, new app reads | rows the old version creates that the new version can't use |
| rollback | new app writes, old app reads | rows the new version creates that a rollback can't read |

| live data | old and new app write rows, then the phase is applied over them | a migration that fails on rows the running versions created (NOT NULL over NULLs, a unique index over duplicates, a constraint over old values) |

The live-data check applies each phase on top of rows the versions running before it just wrote, so a migration that cannot apply to production data is reported as a migration failure, not as a later symptom. Each version writes `live_rows` rows (default 2, because some failures, such as a unique index, only appear with more than one row). It is skipped for a phase when no `write` command is configured, and can be turned off with `live: false`.

Your own SQL invariants run after every check. Each check gets a fresh clone of a template database, so checks are isolated and repeatable.

```
$ migration-safety-checker check migration-safety-checker.yml
pass  old@baseline
pass  live@before:rename
FAIL  old@after:rename       old test exited with code 1
pass  new@after:rename
FAIL  forward@after:rename   old write exited with code 1
FAIL  rollback@after:rename  old read after new write exited with code 1

verdict: UNSAFE (3 of 6 required checks failed)
first failing state: after:rename
```

Exit code is 0 for SAFE, 1 for UNSAFE, and 2 for a setup error or an INCONCLUSIVE run (every failure was a harness failure), so it drops straight into CI.

| Option | Effect |
|---|---|
| `--out DIR` | writes `report.md`, `report.json` (with command logs) and `junit.xml` |
| `--jobs N` | runs N checks at once, each in its own database |
| `--timeout S` | seconds allowed per command; a timed-out command is killed with its whole process group |
| `--isolate` | also applies each phase alone on the baseline and tests it (diagnostic only; never changes the verdict) |
| `--strict` | exit 2 if a check could not run because the config has no `write` command for it |

Reports name the first failing state, since later states build on it.

### Reading a failure

Every failed check carries a cause, and the report summarises them:

| Cause | Meaning |
|---|---|
| `schema` | an app command failed with a database error (missing column, violated constraint) |
| `migration` | a phase did not apply, including over live rows |
| `invariant` | one of your SQL invariants returned rows |
| `app-test` | an app command failed with no database or harness error in its output, for example an assertion; the schema or the test itself may be the cause |
| `harness` | the command could not run properly: timeout, command not found, import error, no connection |

A run whose failures are all `harness` is INCONCLUSIVE, not UNSAFE: nothing in it shows the migration is unsafe. The cause is assigned by pattern matching on exit codes and error text, so it is a hint, not proof.

With `--isolate`, each phase is also applied alone on the baseline and the four checks run on it, shown beside the rollout results. A failure that appears alone belongs to that phase; one that appears only in the rollout comes from the phases before it. A phase that cannot be applied to the baseline by itself (it uses something an earlier phase created) is reported as such. Isolation works for independent SQL phases. It cannot separate chained Django migrations, because `migrate <app> <name>` applies the migration's dependencies as well.

## Configuration

```yaml
name: rename-user-column
baseline:
  sql: [schema.sql]
phases:
  - name: expand
    sql: [safe/01_expand.sql]
  - name: backfill
    sql: [safe/backfill.sql]
    partial_sql: [safe/backfill_half.sql]   # also tests the half-done state
  - name: contract
    sql: [safe/02_contract.sql]
    old_may_run: false                       # old version is retired by now
apps:
  old: { test: "pytest tests/old", write: "python seed_old.py" }
  new: { test: "pytest tests/new", write: "python seed_new.py" }
invariants:
  - name: emails unique
    sql: "SELECT email FROM users GROUP BY email HAVING count(*) > 1"
```

Commands receive `DATABASE_URL` (a `postgresql://` URL), `MIGRATION_SAFETY_CHECKER_STATE` and `MIGRATION_SAFETY_CHECKER_APP`. A phase or the baseline can use `run:` (shell commands, for migrations written in Python) instead of or after `sql:`. An app can add `read:` to say how it reads existing data; the forward and rollback checks use it, and fall back to `test`. See `examples/rename-column` for a complete safe and unsafe pair.

## Generate the config from Django migrations

Point it at two checkouts, the release serving traffic and the one being rolled out:

```bash
migration-safety-checker init-django \
  --old ../app-v1 --old-python ../app-v1/.venv/bin/python \
  --new ../app-v2 --new-python ../app-v2/.venv/bin/python \
  --settings mysite.settings --tests "orders.tests billing.tests" \
  --seed seed.py --write write.py --read read.py --out checks/
migration-safety-checker check checks/migration-safety-checker.yml --jobs 4
```

Each release's own Django reports its migration graph. Every migration only the new release has becomes a phase, with a comment on risky operations (column drops, renames, constraint changes, data migrations). A generated settings overlay points the project at the prepared database, and the suites run on an empty clone of that schema without running migrations. Half-finished backfill states are added by hand with `partial_sql`.

## Generate the config from Alembic revisions

The same idea for SQLAlchemy projects that use Alembic. Each release's own Alembic reports its revision graph; every revision only the new release has becomes a phase.

```bash
migration-safety-checker init-alembic \
  --old ../app-v1 --old-python ../app-v1/.venv/bin/python \
  --new ../app-v2 --new-python ../app-v2/.venv/bin/python \
  --test "pytest -q tests" --write "python scripts/write.py" --read "python scripts/read.py" \
  --driver psycopg --out checks/
migration-safety-checker check checks/migration-safety-checker.yml
```

The adapter writes a small wrapper next to the configuration, and every command in the configuration runs through it: the migrations (`alembic upgrade <revision>`), the seed, and your test, write and read commands. The wrapper puts the prepared database's URL in `sqlalchemy.url` and in every variable named by `--url-env` (default `DATABASE_URL`). `--driver psycopg` turns `postgresql://` into `postgresql+psycopg://`. For an application that builds its own URL from separate settings, `--url-part POSTGRES_SERVER=host --url-part POSTGRES_DB=dbname` (also `port`, `user`, `password`) exports each part under the name the application reads. Notes on risky operations (drops, renames, NOT NULL, new constraints, raw SQL) come from reading each revision's `upgrade()` with `ast`, so they are a hint and can miss operations hidden in helper functions; every new revision becomes a phase either way.

`--migrate-first` is for releases whose revisions all run before the new code starts (a pre-start `alembic upgrade head`): the new release is then required only after the last revision. `{python}` in `--test`, `--seed`, `--write` and `--read` stands for each release's own interpreter, so one command string works for both.

Limits, stated plainly:
- Your `env.py` has to take its database from `sqlalchemy.url`, from a variable named in `--url-env`, or from settings assembled out of variables you map with `--url-part`. One that hard-codes its own connection cannot be pointed at the prepared database.
- Phases are applied with `alembic upgrade <revision>`, which also applies that revision's ancestors, so `--isolate` cannot separate chained revisions.
- Revision order comes from `ScriptDirectory.walk_revisions`; branching histories were not tested.

## Use it in CI: the GitHub Action

One step in another repository's workflow installs the tool from the ref you pin, runs the check, fails the job unless the verdict matches the expectation (`safe` by default), and uploads `report.md` and `junit.xml` as a workflow artifact. The job needs a Postgres service and `MIGRATION_SAFETY_CHECKER_ADMIN_URL` (or the `admin-url` input).

```yaml
name: rollout-safety
on: pull_request
jobs:
  check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      MIGRATION_SAFETY_CHECKER_ADMIN_URL: postgresql://postgres:postgres@localhost:5432/postgres
    steps:
      - uses: actions/checkout@v5
      # set up whatever your checks and both app versions need here (dependencies, the old release checkout, ...)
      - uses: ram15x/migration-safety-checker@main    # pin a tag once one includes action.yml
        with:
          config: checks/migration-safety-checker.yml
```

Inputs: `config` (required), `admin-url`, `expect` (`safe` or `unsafe`), `jobs`, `extra-args` (for example `--isolate --strict`), `python-version`, `report-dir`, `upload`, `artifact-name`. Output: `verdict` (SAFE, UNSAFE or INCONCLUSIVE, the last also for a setup error). Use a different `artifact-name` for each use in one workflow run.

This repository's own CI runs the action three times against the bundled example (`examples/rename-column`): once expecting SAFE, once expecting UNSAFE, and once with a wrong expectation that must fail the step. The action file has not been run in anyone else's repository. The v0.1.0 tag predates `action.yml` and `init-alembic`, so `uses:` must point at `main` (or a later tag) for now.

## Results on a real application

[healthchecks](https://github.com/healthchecks/healthchecks) (Django, PostgreSQL, 120+ migrations): three release pairs, real migrations replayed as phases, the releases' own code and test modules as the apps.

| Release pair | Migrations replayed | Verdict | Cause |
|---|---|---|---|
| v3.2 to v3.3 | add nullable `badge_key` | SAFE | |
| v3.4 to v3.5 | move `Ping.body` to `body_raw`, drop `body` (plus two later migrations) | UNSAFE | dropping `api_ping.body` breaks v3.4's tests, ping endpoint and reads |
| v3.9 to v3.10 | `badge_key` default, backfill, NOT NULL | UNSAFE | v3.9 cannot create checks once the column is NOT NULL; 0118 fails to apply over two or more checks created by v3.9 (live-data check); earlier states give v3.10 checks with `None` in their badge URL |

Each run takes 15 to 50 seconds. Configs, scripts and findings are in `eval/real`; `eval/real/healthchecks/generate.sh` regenerates the three configs with `init-django`.

UNSAFE here means unsafe under a rolling deploy, where the old version keeps running during the migration. It is not a bug in healthchecks.

Scope: three release pairs of one application, with a chosen subset of its tests. The v3.9 to v3.10 badge URL finding comes from a read check written for this run.

## Results on a second real application

[full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (FastAPI, SQLModel, Alembic, PostgreSQL, 5 migrations): two release pairs and nine mutants of the 0.10.0 migration. Expected results were written before the runs. Both real pairs matched them: 0.9.0 to 0.10.0 (one nullable column) is SAFE, and 0.6.0 to 0.7.0 (integer ids replaced by UUIDs) is UNSAFE because the old release can no longer insert users. Of the nine mutants, 4 of the 5 labelled UNSAFE were caught (the miss, a unique constraint the tests never violate, was predicted) and all 4 labelled SAFE passed.

This is the same kind of evidence as the healthchecks runs, on a small application with few migrations, with labels and mutants written by the tool's author. It does not show how the tool performs on production data, and the project's own deploy flow is a pre-start `alembic upgrade head`, not a rolling deploy, so UNSAFE here is not a bug in that project. Setup problems that the tool reported as results (a Python version mismatch, a suite that is order dependent, a wrong test path) are listed in `eval/real/fastapi-template/README.md`.

## Results on an Alembic application with more than 30 migrations

[Mealie](https://github.com/mealie-recipes/mealie) (66 Alembic revisions, PostgreSQL supported): three release pairs and four mutants, `--migrate-first`. The pairs are SAFE as labelled; the dropped-column and renamed-column mutants are caught; the NOT NULL mutant with no default is missed, as predicted, because Mealie's own test suite runs migrations itself and could not be used, so the only application check is a read-only smoke test of every mapped table. The three real pairs are all SAFE, so they show that the adapter runs a 66-revision Alembic history on Postgres without false alarms, not that the tool finds real hazards in Mealie. Details, including the candidates that were rejected, are in `eval/real/mealie/README.md`.

## Results on a labelled scenario set

26 migration scenarios (13 safe, 13 unsafe) covering renames, drops, type changes, constraints, column splits, backfills, table renames, rollback hazards and constraints added over live rows. Labels were fixed from each scenario's design before any tool was run. Scenarios 21 to 26 were written after the live-data check existed, to exercise it. Predictions below mean "tool says block this migration".

| Tool | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| migration-safety-checker | 13 | 0 | 0 | 13 | 1.00 | 1.00 |
| New app's tests on final schema (ordinary CI) | 0 | 0 | 13 | 13 | n/a | 0.00 |
| Old app's tests on final schema | 7 | 0 | 6 | 13 | 1.00 | 0.54 |
| Squawk 2.73, default rules | 13 | 13 | 0 | 0 | 0.50 | 1.00 |
| Squawk 2.73, timeout/idempotence rules excluded | 12 | 10 | 1 | 3 | 0.55 | 0.92 |

Six unsafe cases are invisible to testing on the final schema: the column split without sync, the rollback with unreadable new values, the half-finished backfill, and the three constraints added over live rows (21 to 23). The first three need the intermediate-state and rollback checks; the last three need the live-data check, and with it switched off (`live: false`) the tool catches 10 of the 13 unsafe scenarios and misses exactly 21 to 23.

Run with isolation, the later phase of each of the five multi-phase scenarios (02, 13, 20, 21, 26) cannot be applied to the baseline on its own, so isolation changes none of these results. None of the failures in this set is a harness failure.

Squawk is a static linter for lock and style hazards. It answers a different question (will this statement block writes) and its findings here reflect that, so the comparison shows the two are complementary, not that one replaces the other.

Scope of this evidence: the scenarios are synthetic, written by the project author, and applications are modelled by their SQL footprint (`eval/simapp.py`). This demonstrates the mechanism; it is not a measurement on real production applications.

Reproduce: `python eval/scenarios.py && python eval/run_eval.py`. The Squawk rows appear when Squawk is installed (`npm install squawk-cli@2.73.0`, then `SQUAWK=path/to/squawk`); without it they are omitted.

## Results on mutated real migrations

26 mutants of the healthchecks v3.9 schema (13 labelled unsafe, 13 safe), covering dropped and renamed columns that v3.9 uses, NOT NULL without default, tightened constraints, and additions, widenings and indexes. Each label was written from the v3.9 source and committed before the run; the tool's author wrote both the mutants and the labels.

| Tool | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| migration-safety-checker | 13 | 0 | 0 | 13 | 1.00 | 1.00 |
| Old app's tests on the final schema | 12 | 0 | 1 | 13 | 1.00 | 0.92 |
| Squawk 2.73, default rules | 13 | 13 | 0 | 0 | 0.50 | 1.00 |

No misses, no false positives and no harness failures (every failed check was a schema or migration error). This is not a hard test: ordinary old-app tests on the final schema catch 12 of the 13 unsafe mutants, and the one they miss (shrinking a `varchar`) is caught by the live-data check. With 13 unsafe mutants the true recall could be near 0.8, and the set covers one application. Details and the reproduce command are in `eval/mutation`.

## Limits

- Evidence comes from three real applications (healthchecks, full-stack-fastapi-template, Mealie), a synthetic scenario set and mutants. Labels and mutants were written by the tool's author. Treat the numbers as a demonstration of the mechanism, not as an accuracy estimate.
- It runs only the tests you point it at. A hazard that none of those tests, your write and read commands, or the live rows exercise is not found; the unique-constraint mutants and the NOT NULL mutant on Mealie are examples.
- The Mealie run used a read-only smoke test instead of the application's suite, so it cannot see write-path hazards.
- Failure causes (`schema`, `migration`, `app-test`, `harness`, `invariant`) come from pattern matching on exit codes and output. A pytest usage error, for example, shows up as `app-test`.
- If the old release's own tests are already red on the unmodified baseline, the verdict is still UNSAFE with `baseline` as the first failing state. Check `old@baseline` first.
- Phases default to "both versions may run after every phase". For a release whose migrations all run before the new code starts (a pre-start `alembic upgrade head`), use `--migrate-first` with `init-alembic`, or set `new_may_run: false` by hand; otherwise intermediate states report failures that are a consequence of the mode.
- A SAFE verdict says the checks that ran passed. It says nothing about lock time, migration duration or data volume.
- `--isolate` cannot separate chained Django migrations or Alembic revisions, because applying one applies its ancestors.
- Not on PyPI. The action has been run only in this repository's CI.

## Architecture

```
config.py                YAML -> Config (phases, apps, invariants), env expansion, validation
db.py                    template database, per-check clones, SQL files, invariant queries
matrix.py                states x kinds -> checks, with required/optional flags from phase rules
runner.py                executes a check: clone, apply phases, run app commands, invariants;
                         process-group timeouts, parallel jobs
report.py                Markdown, JSON, JUnit
cli.py                   plan | check | init-django | init-alembic | version
adapters/django.py       migration graph -> phases -> config
adapters/alembic.py      Alembic revision graph -> phases -> config, plus a wrapper script
django_support.py        run a Django suite on an empty clone of the prepared schema
django_runner.py         the project's own test runner plus that behaviour
action.yml               composite GitHub Action: install, check, verdict gate, report upload
```

## Tech

Python 3.10+, PostgreSQL (psycopg 3), Typer, Rich, PyYAML, pytest. The adapters run against the project's own Django (5.x in the healthchecks runs) or Alembic. The Alembic adapter also needs alembic in each release's environment. Tested range: Python 3.10 with SQLAlchemy 2.0.58 and Python 3.13 with SQLAlchemy 2.1.9, Alembic 1.20.1, psycopg 3.3, Postgres 16 (UTF8 and SQL_ASCII clusters, TCP and unix socket). SQLAlchemy 1.4 was not tested. CI: GitHub Actions with a Postgres 16 service.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install "git+https://github.com/ram15x/migration-safety-checker.git@v0.1.0"
git clone https://github.com/ram15x/migration-safety-checker && cd migration-safety-checker    # for the bundled examples
export MIGRATION_SAFETY_CHECKER_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
migration-safety-checker check examples/rename-column/safe.yml
```

The example commands call `python`, so run them with the virtual environment active. Installed from GitHub in a clean virtual environment on Python 3.13, and from a local checkout with the full test suite on Python 3.10. Not published on PyPI. The v0.1.0 tag does not include `init-alembic` or the action; install from `main` (`pip install "git+https://github.com/ram15x/migration-safety-checker.git"`) to get them.

## Build and run

```bash
pip install -e ".[dev]"
export MIGRATION_SAFETY_CHECKER_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
pytest -q                      # 72 tests, most need the database
migration-safety-checker plan examples/rename-column/unsafe.yml
migration-safety-checker check examples/rename-column/safe.yml --out report/
```

The user in `MIGRATION_SAFETY_CHECKER_ADMIN_URL` needs permission to create and drop databases. A cluster whose default encoding is SQL_ASCII makes psycopg return text as bytes (SQLAlchemy then fails reading the server version); the tool creates its template database as UTF8 in that case. Your own application's databases are not touched by this. The generated Django commands are bash, so use Linux, macOS or WSL.

## License

MIT
