"""Runs every scenario through ReleaseCompat and the baselines and prints a confusion table.

Predictions ("unsafe" = the tool says the migration should be blocked):
  releasecompat : any required check in the matrix fails
  ci-new-final  : the new version's tests fail on the final schema (what ordinary CI does)
  ci-old-final  : the old version's tests fail on the final schema
  squawk-default: Squawk reports any finding on the migration files (default rules)
  squawk-compat : same, with the three housekeeping rules (lock/statement timeout, idempotence) excluded
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from releasecompat import config as cfgmod
from releasecompat import runner

HERE = Path(__file__).resolve().parent
SQUAWK = os.environ.get("SQUAWK") or shutil.which("squawk")


HOUSEKEEPING = "require-lock-timeout,require-statement-timeout,prefer-robust-stmts"


def squawk_unsafe(root: Path, cfg, exclude: str = "") -> bool | None:
    if not SQUAWK:
        return None
    files = [f for ph in cfg.phases for f in ph.sql]
    args = ["--exclude", exclude] if exclude else []
    r = subprocess.run([SQUAWK, *args, *[str(root / f) for f in files]], capture_output=True, text=True, timeout=60)
    return r.returncode != 0


def main() -> int:
    rows = []
    for d in sorted((HERE / "scenarios").iterdir()):
        label = json.loads((d / "label.json").read_text())["label"]
        cfg = cfgmod.load(d / "releasecompat.yml")
        rep = runner.run(cfg)
        final = f"after:{cfg.phases[-1].name}"
        by = {(r.check.kind, r.check.state.name): r.status for r in rep.results}
        pred = {
            "releasecompat": not rep.safe,
            "ci-new-final": by.get(("new", final)) == runner.FAIL,
            "ci-old-final": by.get(("old", final)) == runner.FAIL,
            "squawk-default": squawk_unsafe(d, cfg),
            "squawk-compat": squawk_unsafe(d, cfg, HOUSEKEEPING),
        }
        pred = {k: v for k, v in pred.items() if v is not None}  # drop tools that could not run
        failed = ", ".join(f"{r.check.kind}@{r.check.state.name}" for r in rep.failed)
        rows.append((d.name, label, pred, failed))
        print(f"{d.name:42s} label={label:6s} " + " ".join(f"{k}={'U' if v else 'S'}" for k, v in pred.items()) + (f"  [{failed}]" if failed else ""), flush=True)

    print()
    if not SQUAWK:
        print("squawk not found (set SQUAWK or put it on PATH): its rows are omitted\n")
    tools = list(rows[0][2])
    print(f"{'tool':15s} TP FP FN TN  precision recall")
    out = {}
    for t in tools:
        tp = sum(1 for _, l, p, _ in rows if l == "unsafe" and p[t])
        fp = sum(1 for _, l, p, _ in rows if l == "safe" and p[t])
        fn = sum(1 for _, l, p, _ in rows if l == "unsafe" and not p[t])
        tn = sum(1 for _, l, p, _ in rows if l == "safe" and not p[t])
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        print(f"{t:15s} {tp:2d} {fp:2d} {fn:2d} {tn:2d}  {prec:9.2f} {rec:6.2f}")
        out[t] = dict(tp=tp, fp=fp, fn=fn, tn=tn)
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "results.json").write_text(json.dumps(
        {"rows": [{"scenario": n, "label": l, "pred": p, "failed": f} for n, l, p, f in rows], "summary": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
