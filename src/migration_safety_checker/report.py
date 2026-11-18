"""Report rendering: Markdown, JSON and JUnit XML."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from .matrix import FORWARD, LIVE, NEW, OLD, ROLLBACK
from .runner import APP, FAIL, HARNESS, INVARIANT, MIGRATION, PASS, SCHEMA, SKIPPED, CheckResult, Report

_ICON = {PASS: "pass", FAIL: "FAIL", SKIPPED: "-"}

_CAUSE_TEXT = {
    SCHEMA: "an app command failed with a database error (a missing column, a violated constraint): caused by the schema",
    MIGRATION: "a phase did not apply",
    INVARIANT: "one of your SQL invariants returned rows",
    APP: "an app command failed with no database or harness error in its output (an assertion, say): "
         "may be caused by the schema or by the test itself",
    HARNESS: "the command could not run properly (timeout, command not found, import error, no connection): "
             "says nothing about the migration",
}


def _grid(report: Report) -> dict[str, dict[str, CheckResult]]:
    grid: dict[str, dict[str, CheckResult]] = {}
    for r in report.results:
        if r.check.kind == LIVE or r.check.diagnostic:
            continue
        grid.setdefault(r.check.state.name, {})[r.check.kind] = r
    return grid


def _isolation_section(report: Report) -> list[str]:
    """Each phase applied alone on the baseline, next to the cumulative result for the same phase."""
    cumulative: dict[str, list[CheckResult]] = {}
    for r in report.results:
        if r.check.diagnostic or r.check.kind == LIVE or ":" not in r.check.state.name:
            continue
        cumulative.setdefault(r.check.state.name.split(":", 1)[1], []).append(r)
    alone: dict[str, dict[str, CheckResult]] = {}
    for r in report.diagnostic:
        alone.setdefault(r.check.state.name.split(":", 1)[1], {})[r.check.kind] = r
    lines = [
        "", "### Each phase applied alone on the baseline", "",
        "Diagnostic only: these states do not occur in the rollout, so they never change the verdict. "
        "A failure here belongs to that phase itself; a failure that appears only in the rollout table "
        "above comes from the phases before it.", "",
        "| Phase alone | Old app | New app | Old writes, new reads | New writes, old reads | In the rollout |",
        "|---|---|---|---|---|---|",
    ]
    for phase, cells in alone.items():
        row = [phase] + [_ICON[cells[k].status] for k in (OLD, NEW, FORWARD, ROLLBACK)]
        rollout = cumulative.get(phase, [])
        row.append("FAIL" if any(r.status == FAIL for r in rollout) else "pass")
        lines.append("| " + " | ".join(row) + " |")
    skipped = [r for r in report.diagnostic if r.status == SKIPPED and "on its own" in r.detail]
    for r in {r.check.state.name: r for r in skipped}.values():
        lines.append("")
        lines.append(f"`{r.check.state.name}`: {r.detail}")
    return lines


def to_markdown(report: Report) -> str:
    required = report.required
    lines = [
        f"# migration-safety-checker report: {report.name}",
        "",
        f"**Verdict: {report.verdict}** "
        f"({len(report.failed)} of {len(required)} required checks failed)",
        "",
    ] + ([
        "INCONCLUSIVE: every failed check was a harness failure, so nothing here shows the migration is unsafe. "
        "Fix the commands and run again.", "",
    ] if report.verdict == "INCONCLUSIVE" else []) + [
        "| Database state | Old app | New app | Old writes, new reads | New writes, old reads (rollback) |",
        "|---|---|---|---|---|",
    ]
    for state, cells in _grid(report).items():
        row = [state]
        for kind in (OLD, NEW, FORWARD, ROLLBACK):
            row.append(_ICON[cells[kind].status])
        lines.append("| " + " | ".join(row) + " |")
    live = [r for r in report.results if r.check.kind == LIVE]
    if live:
        lines += ["", "| Phase applied over live rows | Result |", "|---|---|"]
        lines += [f"| {r.check.phase.name} | {_ICON[r.status]} |" for r in live]
    if report.triage:
        lines += ["", "### Failure triage", "", "| Cause | Failed checks | Meaning |", "|---|---|---|"]
        lines += [f"| {c} | {n} | {_CAUSE_TEXT[c]} |" for c, n in report.triage.items()]
        lines += ["", "The cause comes from pattern matching on exit codes and error text, so treat it as a hint."]
    if report.diagnostic:
        lines += _isolation_section(report)
    if report.first_failing_state:
        lines += [
            "",
            f"First failing state: `{report.first_failing_state}`. Later states build on it, "
            "so a failure there can repeat in every state after it.",
        ]
    if report.unconfigured:
        lines += ["", "**Not run, because the config has no command for them:**"]
        lines += [f"- {r.check.id}: {r.detail}" for r in report.unconfigured]
    lines.append("")
    lines.append("`-` means the check does not apply in that state (for example the old version is already retired).")

    if report.failed:
        lines += ["", "## Failures", ""]
        for r in report.failed:
            lines.append(f"### {r.check.id} ({r.cause or 'unclassified'})")
            lines.append("")
            lines.append(r.detail)
            if r.log:
                lines += ["", "```", r.log, "```"]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_json(report: Report) -> str:
    payload = {
        "name": report.name,
        "verdict": report.verdict,
        "required": len(report.required),
        "failed": len(report.failed),
        "first_failing_state": report.first_failing_state,
        "not_run_unconfigured": [r.check.id for r in report.unconfigured],
        "triage": report.triage,
        "isolation": [
            {"id": r.check.id, "status": r.status, "cause": r.cause, "detail": r.detail}
            for r in report.diagnostic
        ],
        "checks": [
            {
                "id": r.check.id,
                "state": r.check.state.name,
                "kind": r.check.kind,
                "required": r.check.required,
                "status": r.status,
                "cause": r.cause,
                "diagnostic": r.check.diagnostic,
                "detail": r.detail,
                "log": r.log,
                "seconds": round(r.duration, 3),
            }
            for r in report.results
        ],
    }
    return json.dumps(payload, indent=2)


def to_junit(report: Report) -> str:
    suite = ET.Element(
        "testsuite",
        name=f"migration-safety-checker:{report.name}",
        tests=str(len(report.results)),
        failures=str(len(report.failed)),
        skipped=str(sum(1 for r in report.results if r.status == SKIPPED)),
    )
    for r in report.results:
        case = ET.SubElement(
            suite, "testcase", classname=r.check.state.name, name=r.check.id,
            time=f"{r.duration:.3f}",
        )
        if r.status == FAIL:
            failure = ET.SubElement(case, "failure", message=r.detail)
            failure.text = r.log
        elif r.status == SKIPPED:
            ET.SubElement(case, "skipped", message=r.detail)
    ET.indent(suite)
    return ET.tostring(suite, encoding="unicode")
