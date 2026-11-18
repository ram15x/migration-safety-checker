"""Run every mutant in mutants.yml through migration-safety-checker and compare with its label.

    HC_APPS=/tmp/apps MIGRATION_SAFETY_CHECKER_ADMIN_URL=postgresql://... python eval/mutation/run.py [out.json]

Needs eval/real/healthchecks/setup.sh to have been run (checkouts and virtualenvs).
The labels in mutants.yml were written and committed before the first run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from migration_safety_checker import config as cfgmod
from migration_safety_checker import runner

HERE = Path(__file__).resolve().parent
HC = HERE.parent / "real" / "healthchecks"
OUT = HERE / "out"
SQUAWK = os.environ.get("SQUAWK")
HOUSEKEEPING = "require-lock-timeout,require-statement-timeout,prefer-robust-stmts"

TESTS = " ".join(
    f"hc.api.tests.{m}"
    for m in (
        "test_check_model", "test_create_check", "test_get_check", "test_ping", "test_get_pings",
        "test_channel_model", "test_list_channels", "test_notification_status",
    )
)


def write_config(mutant: dict) -> Path:
    d = OUT / mutant["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "phase.sql").write_text(mutant["sql"].strip() + "\n")
    app = f"$HC_SCRIPTS/app.sh v3.9 python manage.py test {TESTS} --noinput"
    write = "$HC_SCRIPTS/app.sh v3.9 script write_mutation.py"
    (d / "migration-safety-checker.yml").write_text(textwrap.dedent(f"""\
        name: {mutant['id']}
        timeout: 300
        baseline:
          run:
            - $HC_SCRIPTS/app.sh v3.9 python manage.py migrate -v0
            - $HC_SCRIPTS/app.sh v3.9 script seed_checks.py
        phases:
          - name: mutate
            sql: [phase.sql]
        apps:
          old: {{ test: "{app}", write: "{write}" }}
          new: {{ test: "{app}", write: "{write}" }}
        """))
    return d / "migration-safety-checker.yml"


def squawk(path: Path, exclude: str = "") -> bool | None:
    if not SQUAWK:
        return None
    args = ["--exclude", exclude] if exclude else []
    return subprocess.run([SQUAWK, *args, str(path)], capture_output=True, text=True, timeout=60).returncode != 0


def run_one(mutant: dict) -> dict:
    cfg_path = write_config(mutant)
    rep = runner.run(cfgmod.load(cfg_path))
    failed = [r for r in rep.failed]
    after_old = next(r for r in rep.results if r.check.id == "old@after:mutate")
    sql = cfg_path.parent / "phase.sql"
    return {
        "id": mutant["id"], "family": mutant["family"], "label": mutant["label"],
        "verdict": rep.verdict,
        "failed": [r.check.id for r in failed],
        "causes": {r.check.id: r.cause for r in failed},
        "details": {r.check.id: r.detail[:200] for r in failed},
        "old_tests_on_final_schema_fail": after_old.status == runner.FAIL,
        "squawk_default": squawk(sql), "squawk_compat": squawk(sql, HOUSEKEEPING),
    }


def metrics(rows: list[dict], pred) -> dict:
    tp = sum(1 for r in rows if r["label"] == "unsafe" and pred(r))
    fn = sum(1 for r in rows if r["label"] == "unsafe" and not pred(r))
    fp = sum(1 for r in rows if r["label"] == "safe" and pred(r))
    tn = sum(1 for r in rows if r["label"] == "safe" and not pred(r))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}


def main() -> int:
    mutants = yaml.safe_load((HERE / "mutants.yml").read_text())["mutants"]
    os.environ.setdefault("HC_SCRIPTS", str(HC))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(run_one, mutants))
    for r in rows:
        mark = "ok  " if (r["verdict"] == "UNSAFE") == (r["label"] == "unsafe") else "MISS" if r["label"] == "unsafe" else "FP  "
        print(f"{mark} {r['id']:34s} label={r['label']:6s} verdict={r['verdict']:12s} {', '.join(r['failed'])}")
    summary = {
        "migration-safety-checker": metrics(rows, lambda r: r["verdict"] == "UNSAFE"),
        "old-tests-on-final-schema": metrics(rows, lambda r: r["old_tests_on_final_schema_fail"]),
    }
    if SQUAWK:
        summary["squawk-default"] = metrics(rows, lambda r: r["squawk_default"])
        summary["squawk-compat"] = metrics(rows, lambda r: r["squawk_compat"])
    print()
    for name, m in summary.items():
        p = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
        rc = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
        print(f"{name:28s} TP {m['tp']:2d} FP {m['fp']:2d} FN {m['fn']:2d} TN {m['tn']:2d}  precision {p}  recall {rc}")
    print("inconclusive:", [r["id"] for r in rows if r["verdict"] == "INCONCLUSIVE"] or "none")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
