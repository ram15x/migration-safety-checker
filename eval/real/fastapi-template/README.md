# Second real application: full-stack-fastapi-template (FastAPI, SQLModel, Alembic, Postgres)

Source: <https://github.com/fastapi/full-stack-fastapi-template>, tags 0.6.0 (`1b8b784`), 0.7.0 (`af08676`),
0.9.0 (`e4022a9`) and 0.10.0 (`d40de23`). The repository's own Dockerfiles use Python 3.10; so do the
virtualenvs here. Dependencies came from `poetry.lock` for 0.6.0 and 0.7.0 and from `pyproject.toml` (latest
compatible versions, no lock file) for 0.9.0 and 0.10.0.

Expected results are in `labels.yml`, written from the migrations and the application code before the tool
ran on any of these cases. (One line was later quoted so the file parses as YAML; its wording did not change.)
`run.py` regenerates everything; `results.json` is its output.

**This is not a count of bugs in the upstream project.** The project documents running `alembic upgrade head`
as a pre-start step before the new containers serve traffic. A rolling deploy where the previous release keeps
serving while the migration runs is a stricter scenario than the one it was built for. An UNSAFE verdict here
says "the previous release would break in that scenario", nothing more.

## What was run

The old release's tests, the new release's tests, a write script and a read script (`write.py`, `read.py`) that
use each release's own models, and the tool's live-data check. The baseline is seeded through the old release.
Every test file runs in its own pytest process (see harness problems). Only the app's own suites that ran
are covered; nothing else of the application was exercised.

## Results

Real release pairs:

| Pair | Revisions | Label | Tool verdict | Matches |
|---|---|---|---|---|
| 0.9.0 to 0.10.0 | 1 (adds nullable `created_at`) | SAFE | SAFE | yes |
| 0.6.0 to 0.7.0, `--migrate-first` | 3 (varchar(255), integer ids to UUID, cascade foreign key) | UNSAFE | UNSAFE, first failing state `after:d98dd8ec85a3` | yes |
| 0.6.0 to 0.7.0, default mode | same | UNSAFE | UNSAFE, first failing state `after:9c0a54914c78` | yes, as predicted in `labels.yml` |

For 0.6.0 to 0.7.0 the checks that failed are the ones predicted: after the UUID revision the old release cannot
insert a user (the new `id` column has no server-side default) and cannot read UUID ids into an integer field.
The new release passes on the final state. In default mode the new release also fails after the first revision
(the id column is still an integer), which is a consequence of the mode, not of the migrations; `--migrate-first`
avoids it. Two details differed from the labels: the live-data check before the second and third revision was
reported as *skipped* ("could not write live rows") where I had written FAIL, and the rollback failure is
classified `app-test` rather than `schema`.

Mutants of the 0.10.0 `created_at` migration (old release 0.9.0), nine, each labelled in `labels.yml`:

| Mutant | Label | Tool | How it was caught |
|---|---|---|---|
| M1 NOT NULL, no default | UNSAFE | UNSAFE | `migration` failure: the phase cannot apply over the seeded rows |
| M2 NOT NULL with server default | SAFE | SAFE | |
| M3 drop `user.full_name` | UNSAFE | UNSAFE | `schema` failures |
| M4 rename `user.email` | UNSAFE | UNSAFE | `schema` failures |
| M5 add index | SAFE | SAFE | |
| M6 `email` to varchar(10) | UNSAFE | UNSAFE | `migration` failure over the seeded rows |
| M7 unique constraint on `item.title` | UNSAFE | **SAFE (miss, predicted)** | the tests and `write.py` use unique titles |
| M8 check constraint, title not blank | SAFE | SAFE | |
| M9 add unrelated table | SAFE | SAFE | |

Of 5 mutants labelled UNSAFE the tool caught 4 (recall 4/5); of the 4 it reported UNSAFE, all 4 were labelled
UNSAFE (precision 4/4). Counting the three real-pair runs too, 11 of 12 verdicts equal the label; the twelfth (M7)
is the miss I predicted. M1 and M6 are caught because the baseline holds rows; on an empty
database they would pass the migration and only the old release's inserts would reveal M1.

## What this does not show

- Two release pairs and nine mutants of one application, with labels written by the tool's author. The mutants are
  my choices, not a sample of real mistakes. The mutation results are a sanity check, not an estimate of
  accuracy.
- 0.9.0 to 0.10.0 is a single nullable column, the easiest case. It shows the tool does not raise a false alarm
  there, not that it finds hazards.
- 0.9.0 and 0.10.0 use unlocked dependency versions, so the environment is not the one upstream tested.
- It says nothing about locks, long-running migrations or data volume.

## Harness problems met on the way

These are setup problems, listed because the tool reported some of them as results:

1. First attempt used Python 3.12. An upstream test in 0.9.0 fails there for a reason unrelated to the schema, and
   the tool reported `UNSAFE` with first failing state `baseline` (cause `app-test`). Rebuilt on Python 3.10.
2. 0.6.0 and 0.7.0 read their settings from `backend/.env`, not `../.env`. Without them the baseline migration
   could not start and `check` stopped with exit 2 and no report. The settings are now passed with `--env`.
3. A wrong test path (pytest exit 4, a usage error) was reported as `app-test` failures. The cause classifier
   cannot tell a usage error from a failing test.
4. Missing email settings produced 14 errors in the 0.6.0 suite, again at the baseline.
5. The 0.6.0 suite is order dependent: `test_reset_password` changes the superuser's password, so the next module's
   login fails when the whole suite shares one process, even on a clean database. Each test file therefore runs in
   its own pytest process.

Weakness this exposed in the tool: when the old release's own tests fail on the unmodified baseline, the verdict
is still `UNSAFE` (first failing state `baseline`) instead of a warning that the suite is red before any migration
runs. Not fixed in this round.
