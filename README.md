# ReleaseCompat

[![ci](https://github.com/ram15x/ReleaseCompat/actions/workflows/ci.yml/badge.svg)](https://github.com/ram15x/ReleaseCompat/actions/workflows/ci.yml)

Rolling deploys run two versions of your application against one database at the same time. Migration tools check that the migration applies, and your test suite checks that the new code works on the final schema. The question that breaks production is different: **does every version that is allowed to run keep working in every intermediate state of the rollout, and can you roll back?**

Teams often answer part of it by hand, running the previous release's tests against the new schema in CI. ReleaseCompat automates that across every state of the rollout, in both directions.

## How it works

You describe the rollout as ordered phases (expand, backfill, contract, ...) and say which application versions may run after each one. ReleaseCompat builds a database for every state, runs your real test commands against it, and reports a verdict.

For each state (baseline, mid-backfill, after each phase) it runs up to four checks:

| Check | What runs | Catches |
|---|---|---|
| old | old app's tests | the migration broke the version still serving traffic |
| new | new app's tests | the new version needs something the migration hasn't delivered yet |
| forward | old app writes, new app reads | rows the old version creates that the new version can't use |
| rollback | new app writes, old app reads | rows the new version creates that a rollback can't read |

Your own SQL invariants run after every check. Each check gets a fresh clone of a template database, so checks are isolated and repeatable.

```
$ releasecompat check releasecompat.yml
pass  old@baseline
FAIL  old@after:rename       old test exited with code 1
pass  new@after:rename
FAIL  forward@after:rename   old write exited with code 1
FAIL  rollback@after:rename  old read after new write exited with code 1

verdict: UNSAFE (3 of 5 required checks failed)
```

Exit code is 0 for SAFE, 1 for UNSAFE, 2 for a setup error, so it drops straight into CI.

| Option | Effect |
|---|---|
| `--out DIR` | writes `report.md`, `report.json` (with command logs) and `junit.xml` |
| `--jobs N` | runs N checks at once, each in its own database |
| `--timeout S` | seconds allowed per command; a timed-out command is killed with its whole process group |
| `--strict` | exit 2 if a check could not run because the config has no `write` command for it |

Reports name the first failing state, since later states build on it.

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

Commands receive `DATABASE_URL` (a `postgresql://` URL), `RC_STATE` and `RC_APP`. A phase or the baseline can use `run:` (shell commands, for migrations written in Python) instead of or after `sql:`. An app can add `read:` to say how it reads existing data; the forward and rollback checks use it, and fall back to `test`. See `examples/rename-column` for a complete safe and unsafe pair.

## Generate the config from Django migrations

Point it at two checkouts, the release serving traffic and the one being rolled out:

```bash
releasecompat init-django \
  --old ../app-v1 --old-python ../app-v1/.venv/bin/python \
  --new ../app-v2 --new-python ../app-v2/.venv/bin/python \
  --settings mysite.settings --tests "orders.tests billing.tests" \
  --seed seed.py --write write.py --read read.py --out releasecompat/
releasecompat check releasecompat/rc.yml --jobs 4
```

Each release's own Django reports its migration graph. Every migration only the new release has becomes a phase, with a comment on risky operations (column drops, renames, constraint changes, data migrations). A generated settings overlay points the project at the prepared database, and the suites run on an empty clone of that schema without running migrations. Half-finished backfill states are added by hand with `partial_sql`.

## Results on a real application

[healthchecks](https://github.com/healthchecks/healthchecks) (Django, PostgreSQL, 120+ migrations): three release pairs, real migrations replayed as phases, the releases' own code and test modules as the apps.

| Release pair | Migrations replayed | Verdict | Cause |
|---|---|---|---|
| v3.2 to v3.3 | add nullable `badge_key` | SAFE | |
| v3.4 to v3.5 | move `Ping.body` to `body_raw`, drop `body` (plus two later migrations) | UNSAFE | dropping `api_ping.body` breaks v3.4's tests, ping endpoint and reads |
| v3.9 to v3.10 | `badge_key` default, backfill, NOT NULL | UNSAFE | v3.9 cannot create checks once the column is NOT NULL; earlier states give v3.10 checks with `None` in their badge URL |

Each run takes 15 to 50 seconds. Configs, scripts and findings are in `eval/real`; `eval/real/healthchecks/generate.sh` regenerates the three configs with `init-django`.

UNSAFE here means unsafe under a rolling deploy, where the old version keeps running during the migration. It is not a bug in healthchecks.

Scope: three release pairs of one application, with a chosen subset of its tests. The v3.9 to v3.10 badge URL finding comes from a read check written for this run.

## Results on a labelled scenario set

20 migration scenarios (10 safe, 10 unsafe) covering renames, drops, type changes, constraints, column splits, backfills, table renames and rollback hazards. Labels were fixed from each scenario's design before any tool was run. Predictions below mean "tool says block this migration".

| Tool | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| ReleaseCompat | 10 | 0 | 0 | 10 | 1.00 | 1.00 |
| New app's tests on final schema (ordinary CI) | 0 | 0 | 10 | 10 | n/a | 0.00 |
| Old app's tests on final schema | 7 | 0 | 3 | 10 | 1.00 | 0.70 |
| Squawk 2.73, default rules | 10 | 10 | 0 | 0 | 0.50 | 1.00 |
| Squawk 2.73, timeout/idempotence rules excluded | 9 | 7 | 1 | 3 | 0.56 | 0.90 |

The three unsafe cases that final-state testing cannot see are the column split without sync, the rollback with unreadable new values, and the half-finished backfill. Those are the ones that need the intermediate-state and rollback checks.

Squawk is a static linter for lock and style hazards. It answers a different question (will this statement block writes) and its findings here reflect that, so the comparison shows the two are complementary, not that one replaces the other.

Scope of this evidence: the scenarios are synthetic, written by the project author, and applications are modelled by their SQL footprint (`eval/simapp.py`). This demonstrates the mechanism; it is not a measurement on real production applications.

Reproduce: `python eval/scenarios.py && python eval/run_eval.py`. The Squawk rows appear when Squawk is installed (`npm install squawk-cli@2.73.0`, then `SQUAWK=path/to/squawk`); without it they are omitted.

## Architecture

```
config.py                YAML -> Config (phases, apps, invariants), env expansion, validation
db.py                    template database, per-check clones, SQL files, invariant queries
matrix.py                states x kinds -> checks, with required/optional flags from phase rules
runner.py                executes a check: clone, apply phases, run app commands, invariants;
                         process-group timeouts, parallel jobs
report.py                Markdown, JSON, JUnit
cli.py                   plan | check | init-django | version
adapters/django.py       migration graph -> phases -> config
django_support.py        run a Django suite on an empty clone of the prepared schema
django_runner.py         the project's own test runner plus that behaviour
```

## Tech

Python 3.10+, PostgreSQL (psycopg 3), Typer, Rich, PyYAML, pytest. The Django adapter runs against the project's own Django (5.x in the healthchecks runs). CI: GitHub Actions with a Postgres 16 service.

## Build and run

```bash
pip install -e ".[dev]"
export RC_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
pytest -q                      # 33 tests, most need the database
releasecompat plan examples/rename-column/unsafe.yml
releasecompat check examples/rename-column/safe.yml --out report/
```

The user in `RC_ADMIN_URL` needs permission to create and drop databases. The generated Django commands are bash, so use Linux, macOS or WSL.

## License

MIT
