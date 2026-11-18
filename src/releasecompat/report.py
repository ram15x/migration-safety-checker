"""Report rendering: Markdown, JSON and JUnit XML."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from .matrix import FORWARD, NEW, OLD, ROLLBACK
from .runner import FAIL, PASS, SKIPPED, CheckResult, Report

_ICON = {PASS: "pass", FAIL: "FAIL", SKIPPED: "-"}


def _grid(report: Report) -> dict[str, dict[str, CheckResult]]:
    grid: dict[str, dict[str, CheckResult]] = {}
    for r in report.results:
        grid.setdefault(r.check.state.name, {})[r.check.kind] = r
    return grid


def to_markdown(report: Report) -> str:
    required = report.required
    lines = [
        f"# ReleaseCompat report: {report.name}",
        "",
        f"**Verdict: {report.verdict}** "
        f"({len(report.failed)} of {len(required)} required checks failed)",
        "",
        "| Database state | Old app | New app | Old writes, new reads | New writes, old reads (rollback) |",
        "|---|---|---|---|---|",
    ]
    for state, cells in _grid(report).items():
        row = [state]
        for kind in (OLD, NEW, FORWARD, ROLLBACK):
            row.append(_ICON[cells[kind].status])
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("`-` means the check does not apply in that state (for example the old version is already retired).")

    if report.failed:
        lines += ["", "## Failures", ""]
        for r in report.failed:
            lines.append(f"### {r.check.id}")
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
        "checks": [
            {
                "id": r.check.id,
                "state": r.check.state.name,
                "kind": r.check.kind,
                "required": r.check.required,
                "status": r.status,
                "detail": r.detail,
                "seconds": round(r.duration, 3),
            }
            for r in report.results
        ],
    }
    return json.dumps(payload, indent=2)


def to_junit(report: Report) -> str:
    suite = ET.Element(
        "testsuite",
        name=f"releasecompat:{report.name}",
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
