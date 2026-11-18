# ReleaseCompat

Rolling deploys run two versions of your application against one database at the same time. Most migration tools check that the migration applies, and your test suite checks that the new code works on the final schema. Neither answers the question that actually breaks production: **does every version that is allowed to run keep working in every intermediate state of the rollout, and can you roll back?**

ReleaseCompat answers it by running the matrix.

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
FAIL  rollback@after:rename  old test after new write exited with code 1

verdict: UNSAFE (3 of 5 required checks failed)
```

Exit code is 0 for SAFE, 1 for UNSAFE, 2 for a config error, so it drops straight into CI. `--out DIR` writes `report.md`, `report.json` and `junit.xml`.

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

Commands receive `DATABASE_URL`, `RC_STATE` and `RC_APP`. See `examples/rename-column` for a complete safe and unsafe pair.

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

Reproduce: `python eval/scenarios.py && python eval/run_eval.py`.

## Architecture

```
config.py   YAML -> Config (phases, apps, invariants), env expansion, validation
db.py       template database, per-check clones, SQL file application, invariant queries
matrix.py   states x kinds -> checks, with required/optional flags from phase rules
runner.py   executes a check: clone, apply phases, run app commands, evaluate invariants
report.py   Markdown, JSON, JUnit
cli.py      plan | check | version
```

## Tech

Python 3.10+, PostgreSQL (psycopg 3), Typer, Rich, PyYAML, pytest.

## Build and run

```bash
pip install -e ".[dev]"
export RC_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
pytest -q
releasecompat plan examples/rename-column/unsafe.yml
releasecompat check examples/rename-column/safe.yml --out report/
```

The user in `RC_ADMIN_URL` needs permission to create and drop databases.
