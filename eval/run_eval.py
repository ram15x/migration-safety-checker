"""Runs every scenario through migration-safety-checker and the baselines and prints a confusion table.

Predictions ("unsafe" = the tool says the migration should be blocked):
  migration-safety-checker : any required check in the matrix fails
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

from migration_safety_checker import config as cfgmod
from migration_safety_checker import runner

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
    causes: dict[str, int] = {}
    inconclusive: list[str] = []
    isolation: list[str] = []
    for d in sorted((HERE / "scenarios").iterdir()):
        label = json.loads((d / "label.json").read_text())["label"]
        cfg = cfgmod.load(d / "migration-safety-checker.yml")
        rep = runner.run(cfg, isolate=True)
        for cause, n in rep.triage.items():
            causes[cause] = causes.get(cause, 0) + n
        if rep.verdict == "INCONCLUSIVE":
            inconclusive.append(d.name)
        if rep.diagnostic:
            alone = sorted({r.check.state.name.split(":", 1)[1] for r in rep.diagnostic if r.status == runner.FAIL})
            skipped = sorted({r.check.state.name.split(":", 1)[1] for r in rep.diagnostic if r.status == runner.SKIPPED and "on its own" in r.detail})
            isolation.append(f"{d.name}: fail alone: {alone or 'none'}; cannot apply alone: {skipped or 'none'}")
        final = f"after:{cfg.phases[-1].name}"
        by = {(r.check.kind, r.check.state.name): r.status for r in rep.results}
        pred = {
            "migration-safety-checker": rep.verdict == "UNSAFE",
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
    print("failures of this tool by cause (all scenarios):", causes or "none")
    print("inconclusive scenarios (harness failures only):", inconclusive or "none")
    print("phases applied alone on the baseline (multi-phase scenarios):")
    for line in isolation:
        print("  " + line)
    print()
    if not SQUAWK:
        print("squawk not found (set SQUAWK or put it on PATH): its rows are omitted\n")
    tools = list(rows[0][2])
    w = max(len(t) for t in tools)
    print(f"{'tool':{w}s} TP FP FN TN  precision recall")
    out = {}
    for t in tools:
        tp = sum(1 for _, l, p, _ in rows if l == "unsafe" and p[t])
        fp = sum(1 for _, l, p, _ in rows if l == "safe" and p[t])
        fn = sum(1 for _, l, p, _ in rows if l == "unsafe" and not p[t])
        tn = sum(1 for _, l, p, _ in rows if l == "safe" and not p[t])
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        print(f"{t:{w}s} {tp:2d} {fp:2d} {fn:2d} {tn:2d}  {prec:9.2f} {rec:6.2f}")
        out[t] = dict(tp=tp, fp=fp, fn=fn, tn=tn)
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "results.json").write_text(json.dumps(
        {"rows": [{"scenario": n, "label": l, "pred": p, "failed": f} for n, l, p, f in rows], "summary": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
