#!/usr/bin/env python3
"""
Entry-point runner for the audit-orchestrator skill.
Thin CLI over audit_engine.AuditEngine.run_all — exactly the logic described
in the SKILL.md Procedure section.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from audit_engine import AuditEngine, AuditorSession  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_audit",
        description=(
            "Run the full Brand AI-Readiness audit against a target website. "
            "Emits the fixed-schema JSON report on stdout."
        ),
    )
    p.add_argument("url", help="Target domain (example.com) or URL (https://example.com).")
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON report (default: compact JSON).",
    )
    p.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Write the report to this file path instead of stdout.",
    )
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    session = AuditorSession()
    engine = AuditEngine(session)
    try:
        report = engine.run_all(args.url)
    finally:
        session.close()

    text = json.dumps(report, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
