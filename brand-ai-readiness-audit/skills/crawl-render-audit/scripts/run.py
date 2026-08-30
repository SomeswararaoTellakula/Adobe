#!/usr/bin/env python3
"""Standalone runner for the crawl-render-audit skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "audit-orchestrator" / "scripts"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from audit_engine import AuditorSession, AuditEngine  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    session = AuditorSession()
    engine = AuditEngine(session)
    try:
        resp, soup = session.fetch_html(args.url if args.url.startswith("http") else "https://" + args.url)
        findings = engine.audit_crawlability(args.url, resp, soup) + engine.audit_render(args.url, resp, soup)
    finally:
        session.close()

    print(json.dumps(findings, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
