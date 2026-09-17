#!/usr/bin/env python3
"""
validate_report.py - check an audit report against the required schema.

  python3 validate_report.py report.json

Deliberately dependency-free: the marketplace must run on a bare Python install. Exits 0 if
valid, 1 with a list of problems otherwise. The orchestrator runs this on every report so a
malformed report is caught here rather than by whoever consumes it.
"""

import json
import re
import sys

SEVERITIES = {"critical", "high", "medium", "low", "info"}
REQUIRED_FINDING = ("id", "title", "severity", "evidence", "suggested_action")
REQUIRED_SUMMARY = ("total_findings", "critical", "high", "medium")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


def validate(report):
    problems = []
    for key in ("site", "audited_at", "summary", "findings"):
        if key not in report:
            problems.append(f"missing top-level field: {key}")
    if problems:
        return problems

    if not isinstance(report["site"], str) or not report["site"].strip():
        problems.append("site must be a non-empty string")
    if not ISO.match(str(report["audited_at"])):
        problems.append(f"audited_at is not an ISO 8601 timestamp: {report['audited_at']!r}")

    summary = report["summary"]
    for key in REQUIRED_SUMMARY:
        if key not in summary:
            problems.append(f"summary missing counts field: {key}")

    findings = report["findings"]
    if not isinstance(findings, list):
        return problems + ["findings must be a list"]

    seen = set()
    counted = {s: 0 for s in SEVERITIES}
    for i, f in enumerate(findings):
        where = f.get("id", f"index {i}")
        for key in REQUIRED_FINDING:
            if key not in f:
                problems.append(f"finding {where}: missing required field {key}")
        if f.get("id") in seen:
            problems.append(f"duplicate finding id: {f.get('id')}")
        seen.add(f.get("id"))
        if f.get("severity") not in SEVERITIES:
            problems.append(f"finding {where}: invalid severity {f.get('severity')!r}")
        else:
            counted[f["severity"]] += 1
        if not str(f.get("evidence", "")).strip():
            problems.append(f"finding {where}: evidence is empty - every finding must be evidence-backed")
        action = f.get("suggested_action")
        if not isinstance(action, dict):
            problems.append(f"finding {where}: suggested_action must be an object")
        else:
            if not str(action.get("summary", "")).strip():
                problems.append(f"finding {where}: suggested_action.summary is empty")
            if not action.get("priority"):
                problems.append(f"finding {where}: suggested_action.priority is missing")

    if summary.get("total_findings") != len(findings):
        problems.append(f"summary.total_findings ({summary.get('total_findings')}) != "
                        f"len(findings) ({len(findings)})")
    for sev in ("critical", "high", "medium", "low"):
        if sev in summary and summary[sev] != counted[sev]:
            problems.append(f"summary.{sev} ({summary[sev]}) != counted {counted[sev]}")
    return problems


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: validate_report.py <report.json>")
        return 0 if len(sys.argv) == 2 else 2
    with open(sys.argv[1]) as f:
        report = json.load(f)
    problems = validate(report)
    if problems:
        print(f"INVALID - {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"Report valid: {len(report['findings'])} findings, schema and counts consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
