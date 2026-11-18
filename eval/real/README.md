# Runs on a real application: healthchecks

[healthchecks](https://github.com/healthchecks/healthchecks) is an open-source Django application on PostgreSQL with 120+ migrations. Each run takes two of its tagged releases, treats the older one as the version still serving traffic and the newer one as the version being rolled out, and replays the real migrations between them as phases. Application commands are the releases' own code and test suites.

| Config | Migrations replayed | Verdict | Failed checks |
|---|---|---|---|
| `control-v3.2-to-v3.3.yml` | 0103 add nullable `badge_key` | SAFE | 0 of 5 |
| `v3.4-to-v3.5.yml` | 0108 move `Ping.body` to `body_raw`, 0109 drop `body` | UNSAFE | 3 of 13 |
| `v3.9-to-v3.10.yml` | 0116 default, 0117 backfill, 0118 `badge_key` NOT NULL | UNSAFE | 5 of 17 |

Each config takes roughly 15 to 50 seconds on a 2-core cloud machine; `--jobs 4` took the v3.9 to v3.10 run from 46s to 26s with identical results.

## Generated configs

The three hand-written configs above were the first pass. `migration-safety-checker init-django` now builds the same thing from two checkouts: it asks each release's own Django for its migration graph, turns every migration only the new release has into a phase, and writes a settings overlay so the suites run against the prepared database. `generate.sh` runs it for all three pairs.

Run from generated configs, the control is SAFE and v3.9 to v3.10 is UNSAFE with the same failures (4 of 13 required checks; the hand-written config had two extra checks for a half-finished backfill, which the adapter leaves for you to add as `partial_sql`). The generated v3.4 to v3.5 config is UNSAFE with 9 of 17 failed, because it found two more migrations in that release than I had listed by hand, 0110 and 0111. All 9 failures trace to the dropped `body` column from 0109 and repeat in the later states, so 0110 and 0111 add no new cause. The matrix does not say whether they would be safe on their own.

## What the runs found

**v3.4 to v3.5.** The move of ping bodies is safe in every state, including a half-moved backfill. Dropping `api_ping.body` in 0109 breaks the v3.4 code: its test suite, its ping endpoint and reads after a v3.5 write fail with `column "body" does not exist`. This is a true positive, and not a surprise: the column is dropped in the same release that stops using it. In a rolling deploy it is the contract step that has to wait for the old version to retire.

**v3.9 to v3.10.** 0118 makes `badge_key` NOT NULL. Two kinds of failure:

- After 0118, v3.9 cannot create checks (`NotNullViolation`), so its own tests and its write path fail. True positive.
- In every state before 0118, a check created by v3.9 has no `badge_key`, and v3.10 renders its `badge_url` as `.../b/2/None.svg`. Nothing crashes. This failure comes from the read check written for this run (`read_checks.py`), which asserts that no API URL contains `None`. It is a judgement about usable output, not a test the application ships.

Across the runs, no check passed that I believe should have failed, and the control passed. That is three migration pairs of one application, not a measurement of accuracy.

## What it did not catch

- **A contract migration meeting rows the old version still writes.** In v3.9 to v3.10, 0118 would fail to apply in production if v3.9 created a NULL `badge_key` row between the backfill and the constraint. migration-safety-checker only reports the symptom (`forward@after:backfill` fails). It never applies a phase on top of data written by the other version. That is a gap in the matrix.
- **Locks and duration.** Nothing here measures how long a migration holds a lock on a large table. A linter such as Squawk covers that.
- **Unselected tests.** Each side ran a chosen subset of the application's tests (ping, badge and check modules), not the whole suite. A break outside those modules is invisible.

## Problems in migration-safety-checker that the real application exposed

All fixed in this commit.

1. `DATABASE_URL` was a libpq `key=value` string, not a URL, so URL-based tooling could not parse it. It is now a `postgresql://` URL, including the socket-directory form.
2. Phases and the baseline could only be SQL files, which cannot express data migrations written in Python. They can now also run shell commands (`run`, plus `partial_sql` for half-done states).
3. Test suites that assume empty tables failed on a seeded baseline. Apps can now define a `read` command for the forward and rollback checks, and `migration_safety_checker.django_support` runs a Django suite on an empty clone of the prepared schema without running migrations.
4. An invariant whose SQL could not run (for example, it named a column the phase had dropped) crashed the tool with a traceback. It is now a failed check with the database error.

Still open: a check that fails because of a harness problem looks the same as one that fails because of the migration. The first control run was a false UNSAFE caused by a write script that assumed an API field absent in older releases.

## Reproduce

```bash
export MIGRATION_SAFETY_CHECKER_ADMIN_URL=postgresql://postgres@localhost:5432/postgres
eval/real/healthchecks/setup.sh            # checkouts and virtualenvs in $HC_APPS (default /tmp/apps)
migration-safety-checker check eval/real/healthchecks/v3.4-to-v3.5.yml --out report/
```
