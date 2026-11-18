# Changelog

## Unreleased

- Reusable composite GitHub Action (`action.yml`): installs the tool, runs the check, fails unless the verdict matches `expect` (safe or unsafe), uploads `report.md` and `junit.xml`. Tested in this repository's CI.
- Evidence on two more real applications: full-stack-fastapi-template (two release pairs, nine mutants) and Mealie (three release pairs, four mutants); see `eval/real`.
- `init-alembic`: generate a configuration from the Alembic revisions the new release adds, with notes on risky operations read from each revision's `upgrade()`. Two bundled examples (`examples/alembic`: NOT NULL column without a default is caught, a nullable one passes). Application commands run through the same wrapper, so they see the SQLAlchemy URL; `--url-part` exports host, port, user, password or database name for applications that assemble their own URL.

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
