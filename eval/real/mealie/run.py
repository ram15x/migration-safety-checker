"""Run migration-safety-checker on the Mealie release pairs and mutants in labels.yml.

Usage: APPS=<dir holding mea-<tag>/ checkouts, mvenv-<tag>/ virtualenvs and mutants/> OUT=<scratch dir> python run.py [id ...]
Needs MIGRATION_SAFETY_CHECKER_ADMIN_URL. MODE=default|migrate-first. Writes results-<mode>.json next to this file.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
APPS = Path(os.environ["APPS"])
OUT = Path(os.environ.get("OUT", "/tmp/mealie-eval"))
PARTS = [f"--url-part=POSTGRES_{n}={p}" for n, p in
         (("SERVER", "host"), ("PORT", "port"), ("USER", "user"), ("PASSWORD", "password"), ("DB", "dbname"))]
MODE = os.environ.get("MODE", "default")      # "migrate-first" adds --migrate-first to init-alembic
ENV = ["--env=PRODUCTION=True", "--env=DB_ENGINE=postgres", f"--env=DATA_DIR={OUT}/data"]


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


def release(tag):
    return APPS / f"mea-{tag}", str(APPS / f"mvenv-{tag}" / "bin" / "python")


def mutant(name):
    return APPS / "mutants" / name, str(APPS / "mvenv-v3.25.0" / "bin" / "python")


def case(case_id, old, new):
    out = OUT / MODE / case_id
    gen = ["migration-safety-checker", "init-alembic", "--old", str(old[0]), "--new", str(new[0]),
           "--old-python", old[1], "--new-python", new[1], "--config", "mealie/alembic/alembic.ini",
           "--test", f"{{python}} {HERE}/smoke_read.py", "--out", str(out), *PARTS, *ENV, *(["--migrate-first"] if MODE == "migrate-first" else [])]
    made = subprocess.run(gen, capture_output=True, text=True)
    if made.returncode != 0:
        return {"id": case_id, "error": "init-alembic failed: " + (made.stdout + made.stderr)[-500:]}
    run = subprocess.run(["migration-safety-checker", "check", str(out / "migration-safety-checker.yml"),
                          "--jobs", "3", "--out", str(out / "report")], capture_output=True, text=True)
    path = out / "report" / "report.json"
    if not path.exists():
        return {"id": case_id, "error": "check wrote no report (exit %d): %s" % (run.returncode, (run.stdout + run.stderr)[-500:])}
    rep = json.loads(path.read_text())
    return {
        "id": case_id, "verdict": rep["verdict"], "exit": run.returncode,
        "first_failing_state": rep["first_failing_state"], "triage": rep["triage"],
        "phases": sum(1 for c in rep["checks"] if c["kind"] == "old" and c["state"].startswith("after:")),
        "checks": {c["id"]: {"status": c["status"], "cause": c["cause"], "required": c["required"],
                             "detail": c["detail"][:200]} for c in rep["checks"] if not c["diagnostic"]},
    }


def main():
    labels = yaml.safe_load((HERE / "labels.yml").read_text())
    wanted = set(sys.argv[1:])
    plan = [("P1_v3.18.0_to_v3.19.0", release("v3.18.0"), release("v3.19.0")),
            ("P2_v3.19.0_to_v3.25.0", release("v3.19.0"), release("v3.25.0")),
            ("P3_v3.25.0_to_v3.26.0", release("v3.25.0"), release("v3.26.0"))]
    plan += [(m["id"], release("v3.19.0"), mutant(m["id"])) for m in labels["mutants"]]
    results = []
    for case_id, old, new in plan:
        if wanted and case_id not in wanted:
            continue
        print("running", case_id, flush=True)
        res = case(case_id, old, new)
        print("  ->", res.get("verdict", res.get("error")), res.get("first_failing_state"), flush=True)
        results.append(res)
    (HERE / ("results-default-mode.json" if MODE == "default" else "results-migrate-first.json")).write_text(json.dumps(scrub(results), indent=2) + "\n")


if __name__ == "__main__":
    main()
