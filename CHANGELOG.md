# Changelog

## 0.2.0 (2026-11-18)

### Added
- `init-alembic`: generates a configuration from the Alembic revisions the new release adds, with notes on risky operations read from each revision's `upgrade()`. Options: `--migrate-first` (require the new release only after the last revision), `--url-part` (export host, port, user, password or database name for applications that assemble their own URL), `--url-env`, `--driver`, and a `{python}` placeholder for each release's interpreter. Every command runs through a generated wrapper, so application commands see the SQLAlchemy URL. Two bundled examples in `examples/alembic`: a NOT NULL column without a default is caught, a nullable one passes.
- Reusable composite GitHub Action (`action.yml`): installs the tool from the pinned ref, runs the check, fails unless the verdict matches `expect` (safe or unsafe), uploads `report.md` and `junit.xml`. Tested in this repository's CI against the bundled example.
- Evidence on two more real applications: full-stack-fastapi-template (two release pairs, nine mutants) and Mealie (three release pairs, four mutants); see `eval/real`.

### Fixed
- The template database is created as UTF8 when the cluster's default encoding is SQL_ASCII. On such a cluster psycopg returned bytes and the Alembic example tests failed inside SQLAlchemy (`cannot use a string pattern on a bytes-like object`).
- Committed result files no longer contain absolute paths of the machine they were produced on, and the evaluation runners scrub them.

### Known limits
- Same as 0.1.0, plus: the Action has only run in this repository's CI; the Mealie run used a read-only smoke test, not the application's suite; the three Mealie release pairs are all SAFE and say nothing about whether the tool finds hazards there.

## 0.1.0 (2026-11-18)

First tagged version.

### What it does
- Checks a Postgres migration for rolling-deploy safety. A rollout is a list of phases (SQL files or shell commands); each state gets a fresh database cloned from a template.
- Per state: old app tests, new app tests, forward (old writes, new reads) and rollback (new writes, old reads). Per phase: a live-data check that applies the phase over rows the running versions just wrote (`live_rows`, default 2; `live: false` turns it off). Your own SQL invariants run after every check.
- Verdicts SAFE (exit 0), UNSAFE (exit 1) and INCONCLUSIVE (exit 2, every failure was a harness failure). Setup errors also exit 2.
- Failed checks carry a cause: `schema`, `migration`, `invariant`, `app-test` or `harness`, assigned by pattern matching on exit codes and error text.
- `--isolate` also applies each phase alone on the baseline, as a diagnostic that never changes the verdict. `--jobs`, `--timeout` (process-group kill) and `--strict` are available.
- Reports in Markdown, JSON (with command logs) and JUnit.
- `init-django` builds a config from two Django checkouts by reading each release's own migration graph.

### Evidence in the repository
- 26 labelled synthetic scenarios (13 safe, 13 unsafe), compared with ordinary CI and Squawk.
- Three healthchecks release pairs (one control, two with real breakages).
- 26 labelled mutants of the healthchecks v3.9 schema; labels committed before the run.

### Known limits
- One real application (healthchecks), a chosen subset of its tests, and scenarios and mutants written by the tool's author.
- The Django adapter covers Django only; generated commands are bash (Linux, macOS, WSL).
- `--isolate` cannot separate chained Django migrations, because `migrate <app> <name>` applies the migration's dependencies.
- The `live_rows` default of 2 was chosen after the first live-data check, which wrote one row per version, missed healthchecks 0118.
- No measurement of lock time or migration duration; half-finished backfill states are added by hand (`partial_sql`).
- The failure cause is a heuristic.
