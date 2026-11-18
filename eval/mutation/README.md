# Mutations of the healthchecks schema

26 mutants (13 labelled unsafe, 13 labelled safe), each one phase applied to the real healthchecks v3.9 schema. The release serving traffic is v3.9 (code and test modules); both app slots run v3.9 code, so this measures whether the release that is already running survives each phase. The new release is not modelled.

The operations follow the real migrations in `eval/real` (move and drop a column, default, backfill, NOT NULL) and the four families asked for: drop a column the old release uses, add NOT NULL without default, rename a column, tighten a constraint. The safe mutants are additions, widenings, indexes and expand steps.

**Labels were written first.** `mutants.yml` holds each label with the v3.9 source lines that justify it, and was committed (67a20b9) before the study was run. No mutant was run before that commit. One sanity run of U01, to time the setup, came after it and before the full study. The author of the tool wrote the mutants and the labels; nobody else reviewed them.

Apps: tests are `test_check_model`, `test_create_check`, `test_get_check`, `test_ping`, `test_get_pings`, `test_channel_model`, `test_list_channels`, `test_notification_status`. Write is `write_mutation.py` (create a check through the API, then ping it). Baseline: all real v3.9 migrations plus 8 seeded checks.

## Result

| Tool | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| migration-safety-checker | 13 | 0 | 0 | 13 | 1.00 | 1.00 |
| Old app's tests on the final schema | 12 | 0 | 1 | 13 | 1.00 | 0.92 |
| Squawk 2.73, default rules | 13 | 13 | 0 | 0 | 0.50 | 1.00 |
| Squawk 2.73, timeout/idempotence rules excluded | 13 | 10 | 0 | 3 | 0.57 | 1.00 |

- **Misses:** none. **False positives:** none. **Inconclusive runs:** none.
- **Harness failures:** none. All 52 failed checks on the unsafe mutants were classified `schema` (50) or `migration` (2, the phase failing to apply over live rows).
- Not a hard test. Plain old-app tests on the final schema already catch 12 of 13. The one they miss, U12 (shrinking `name` to `varchar(10)`), is caught by the live-data check: the phase fails over rows the old release wrote.
- With 13 unsafe mutants, a perfect recall is also consistent with a true recall around 0.8 (one-sided 95% bound, exact binomial). One application, one schema, mutants written by the tool's author: this shows the mechanism catches these classes of change, not how often it would on other systems.
- Squawk answers a different question (lock and style hazards), so its row says little about this one.
- Not covered: lock durations, mutations that need a specific data shape the write script does not produce, a new release that differs from the old one, and applications other than healthchecks.

## Reproduce

```bash
eval/real/healthchecks/setup.sh                # needs network access to GitHub and PyPI
export HC_APPS=/tmp/apps MIGRATION_SAFETY_CHECKER_ADMIN_URL=postgresql://postgres@localhost:5432/postgres
python eval/mutation/run.py                    # takes about 4 minutes on 2 cores; SQUAWK=path adds the Squawk rows
```

Per-mutant results are in `results.json`.
