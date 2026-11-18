"""Run migration-safety-checker on the real release pairs and mutants listed in labels.yml and compare with the labels.

Usage: APPS=<dir holding tpl-<tag>/ checkouts and venv-<tag>/ virtualenvs> OUT=<scratch dir> python run.py [case-id ...]
Needs MIGRATION_SAFETY_CHECKER_ADMIN_URL. Writes results.json next to this file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
APPS = Path(os.environ["APPS"])
OUT = Path(os.environ.get("OUT", "/tmp/fastapi-template-eval"))
# The older releases keep their tests in app/tests, the newer ones in tests/. Each test file runs in its own pytest
# process: the 0.6.0 suite is order dependent (test_reset_password changes the superuser's password, which makes the
# login in test_users fail when the whole suite shares one database session), so one process is red on a clean database.
TEST = ("for f in $(find app/tests tests -name 'test_*.py' 2>/dev/null | sort); do "
        "{python} -m pytest -q -p no:cacheprovider \"$f\"; rc=$?; [ $rc -eq 0 -o $rc -eq 5 ] || exit 1; done")
PARTS = [f"--url-part=POSTGRES_{n}={p}" for n, p in
         (("SERVER", "host"), ("PORT", "port"), ("USER", "user"), ("PASSWORD", "password"), ("DB", "dbname"))]


# Settings the application's own docker setup would provide. 0.6.0 and 0.7.0 read them from the
# environment (their .env lookup is relative to backend/), 0.9.0 and 0.10.0 from ../.env.
ENV = ["PROJECT_NAME=Full Stack FastAPI Project", "SECRET_KEY=eval-secret-key-not-for-production-0123456789",
       "FIRST_SUPERUSER=admin@example.com", "FIRST_SUPERUSER_PASSWORD=eval-superuser-password",
       "SMTP_HOST=localhost", "EMAILS_FROM_EMAIL=info@example.com"]


def scrub(value):
    """Replace this machine's absolute paths in a result with placeholders so committed files carry none."""
    if isinstance(value, str):
        for real, placeholder in ((str(HERE), "<repo>/eval/real/%s" % HERE.name), (str(APPS), "<workdir>"), (str(OUT), "<scratch>")):
            value = value.replace(real, placeholder)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    return value


def release(tag: str) -> tuple[Path, str]:
    return APPS / f"tpl-{tag}" / "backend", str(APPS / f"venv-{tag}" / "bin" / "python")


def mutant(name: str) -> tuple[Path, str]:
    return APPS / "mutants" / name / "backend", str(APPS / "venv-0.10.0" / "bin" / "python")


def case(case_id: str, old, new, extra=()) -> dict:
    out = OUT / case_id
    gen = ["migration-safety-checker", "init-alembic", "--old", str(old[0]), "--new", str(new[0]),
           "--old-python", old[1], "--new-python", new[1], "--test", TEST,
           "--seed", f"{{python}} {HERE}/write.py", "--write", f"{{python}} {HERE}/write.py",
           "--read", f"{{python}} {HERE}/read.py", "--out", str(out), *PARTS, *[f"--env={e}" for e in ENV], *extra]
    made = subprocess.run(gen, capture_output=True, text=True)
    if made.returncode != 0:
        return {"id": case_id, "error": "init-alembic failed: " + (made.stdout + made.stderr)[-400:]}
    run = subprocess.run(["migration-safety-checker", "check", str(out / "migration-safety-checker.yml"),
                          "--jobs", "3", "--out", str(out / "report")], capture_output=True, text=True)
    rep = json.loads((out / "report" / "report.json").read_text())
    return {
        "id": case_id, "verdict": rep["verdict"], "exit": run.returncode,
        "first_failing_state": rep["first_failing_state"], "triage": rep["triage"],
        "checks": {c["id"]: {"status": c["status"], "cause": c["cause"], "required": c["required"],
                             "detail": c["detail"][:200]} for c in rep["checks"] if not c["diagnostic"]},
    }


def main() -> None:
    labels = yaml.safe_load((HERE / "labels.yml").read_text())
    wanted = set(sys.argv[1:])
    results = []
    plan = [("R1_0.9.0_to_0.10.0", release("0.9.0"), release("0.10.0"), ())]
    plan += [("R2_0.6.0_to_0.7.0_migrate_first", release("0.6.0"), release("0.7.0"), ("--migrate-first",))]
    plan += [("R2_0.6.0_to_0.7.0_default", release("0.6.0"), release("0.7.0"), ())]
    plan += [(m["id"], release("0.9.0"), mutant(m["id"]), ()) for m in labels["mutants"]]
    for case_id, old, new, extra in plan:
        if wanted and case_id not in wanted:
            continue
        print("running", case_id, flush=True)
        res = case(case_id, old, new, extra)
        print("  ->", res.get("verdict", res.get("error")), res.get("first_failing_state"), flush=True)
        results.append(res)
    path = HERE / "results.json"
    path.write_text(json.dumps(scrub(results), indent=2) + "\n")
    print("wrote", path)


if __name__ == "__main__":
    main()
