#!/usr/bin/env python3
"""
run_audit.py - entrypoint of the brand-ai-readiness-audit marketplace.

Resolves the sibling skills from marketplace.json, collects the site once, runs every
analyzer against that single collection, then merges their findings into one report:
severities normalised, evidence preserved, actions prioritised, and proactive
recommendations added where no defect was found.

  python3 run_audit.py --url https://example.com --out-dir ./audit
  python3 run_audit.py --pack ./existing-pack --out-dir ./audit      # re-analyze, no refetch

Writes report.json (the contract) and report.md (the readable version) into --out-dir.
Recommend-only: nothing here modifies the audited site.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

VERSION = "1.0.0"
SEVERITIES = ["critical", "high", "medium", "low", "info"]
RANK = {s: i for i, s in enumerate(SEVERITIES)}
PRIORITY = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3", "info": "P4"}

# Analyzers, in pipeline order: reach -> read -> parse -> quote -> trust -> stay.
# Each entry is (skill id, script, output name). The entrypoint owns this ordering because
# the order is also the causal order in which problems should be fixed.
ANALYZERS = [
    ("crawl-access-audit", "check_access.py", "access"),
    ("render-extractability-audit", "check_render.py", "render"),
    ("structured-data-audit", "check_structured_data.py", "structured_data"),
    ("answerability-audit", "check_answerability.py", "answerability"),
    ("freshness-corroboration-audit", "check_freshness.py", "freshness"),
    ("engagement-audit", "check_engagement.py", "engagement"),
]


def write_note(out_dir, lines):
    """Persist a message to summary.txt.

    Failure paths previously reported only to stderr. When a harness discards stdout/stderr
    the run looks like it produced nothing, which invites re-running (and re-crawling the
    site) to see output that was never going to appear. Anything worth telling the caller
    is written to disk as well.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "summary.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def marketplace_root(start):
    path = os.path.abspath(start)
    for _ in range(6):
        if os.path.exists(os.path.join(path, "marketplace.json")):
            return path
        path = os.path.dirname(path)
    raise SystemExit("marketplace.json not found above " + start)


def skill_paths(root):
    with open(os.path.join(root, "marketplace.json")) as f:
        manifest = json.load(f)
    return {s["id"]: os.path.join(root, s["path"]) for s in manifest["skills"]}, manifest


def run(cmd, timeout):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def adjust_severity(f, sampled):
    """Deterministic severity normalisation.

    Two rules, both aimed at keeping the report honest:
      * A finding inferred rather than observed is demoted one level and carries a
        verification note, so weak evidence never presents as a certainty.
      * A defect confirmed across nearly the whole sample is promoted one level, because
        a site-wide failure is a different problem from a single bad page.
    """
    sev = f.get("severity", "medium")
    if sev == "info":
        return sev, "policy observation - not counted as a defect"
    reason = []
    if f.get("confidence") == "low" and RANK[sev] < RANK["low"]:
        sev = SEVERITIES[RANK[sev] + 1]
        reason.append("demoted: inferred from markup, not directly observed")
    ratio = (f.get("affected_count", 0) / sampled) if sampled else 0
    if (f.get("scales_with_coverage") and f.get("confidence") == "high"
            and ratio >= 0.8 and f.get("affected_count", 0) >= 3 and RANK[sev] > 0):
        sev = SEVERITIES[RANK[sev] - 1]
        reason.append(f"promoted: affects {f['affected_count']}/{sampled} sampled pages (site-wide)")
    return sev, "; ".join(reason)


def proactive(meta, findings_by_check, pages_seen):
    """Improvements worth making even where no defect was detected.

    Each is gated on what the site already has, so the list is specific rather than generic.
    """
    recs = []
    probes = meta.get("probes", {})
    site = meta.get("site", "the site")
    have = set(findings_by_check)

    def add(rid, title, why, steps, effort="medium"):
        recs.append({"id": rid, "title": title, "rationale": why, "steps": steps,
                     "effort": effort, "priority": "P4"})

    if not probes.get("llms_txt", {}).get("looks_valid"):
        add("R-001", "Publish an llms.txt index of the pages worth citing",
            "A short, plain-text map of the site's canonical explanation pages costs an hour and gives "
            "agents an unambiguous entry point. Adoption is still partial, so treat it as cheap insurance "
            "rather than a fix - it does not substitute for the HTML being readable.",
            ["List the 10-20 URLs that best answer the questions the brand wants to win.",
             "Give each a one-line description in the brand's own words.",
             "Serve it at /llms.txt as text/plain and keep it in sync with the sitemap."],
            effort="low")

    add("R-002", "Publish a canonical facts page and keep every profile identical to it",
        "Agreement across independent sources is what turns a claim into a repeatable fact. A single "
        "authoritative page - legal name, founding year, headquarters, leadership, one-sentence description, "
        "key numbers with dates - gives everyone who describes the brand the same wording to copy.",
        ["Create /about/facts (or a press kit page) with the canonical values as plain text.",
         "Copy that exact wording into Wikidata, LinkedIn, Crunchbase, directories and press materials.",
         "Add a review cadence so the page and the profiles are updated together."])

    if "ANS-008" not in have:
        add("R-003", "Build question-shaped pages for the questions you want to be the answer to",
            "Assistants match questions to passages. Pages organised around the questions buyers actually ask "
            "give a direct match; pages organised around themes give none.",
            ["Mine sales calls, support tickets and site-search logs for the real questions.",
             "Write one page or section per question, answering in the first sentence.",
             "Mark up as FAQPage and keep each answer self-contained enough to quote alone."])

    add("R-004", "Publish comparison and alternatives pages you control",
        "Comparison questions are where assistants pick winners, and they are usually answered from "
        "third-party listicles. An honest, specific, current comparison on the brand's own domain is a "
        "citable source for exactly those queries.",
        ["Write '<brand> vs <named competitor>' and '<category> alternatives' pages.",
         "Be accurate about where the competitor is stronger - one-sided pages get discounted, and the "
         "credibility is what earns the citation.",
         "Include a specification table with dated figures."])

    add("R-005", "Publish original data that other sites will cite",
        "The most durable way to be repeated by machines is to be repeated by people. Original benchmarks, "
        "survey results or industry data get linked and quoted, which puts the brand name on domains you do "
        "not control - exactly the corroboration that makes a fact safe to repeat.",
        ["Pick one question your operational data can answer that no one else has published.",
         "Publish it as an HTML page with a table, a method note, a date and a clear citation line.",
         "Refresh it annually so it stays the current reference."])

    add("R-006", "Measure the outcome directly and repeatedly",
        "Everything else in this report is a proxy. The real measure is what assistants say when asked about "
        "the brand, and whether that changes after the fixes land. Expect that measure to be noisy: these "
        "systems weight answers by what they already hold about the person asking - earlier turns, stated "
        "preferences, location - so the same question can return different brands to different people. A "
        "single test account measures one reader, not the market.",
        ["Write 20 prompts covering the brand name, its category, its competitors and its top buying questions.",
         "Run them monthly across the major assistants and record the answers, the sources cited, and any "
         "factual errors.",
         "Run each prompt from a clean session and from at least one account with relevant history, and from "
         "the locations you actually sell into - a result that only appears for someone already primed to "
         "find you is not evidence you are winning the query.",
         "Track the share of answers that cite the brand's own domain as the primary metric, and record the "
         "spread across contexts rather than a single number.",
         "Segment AI-referral traffic in analytics separately - it lands deep and behaves differently from "
         "search traffic."],
        effort="low")

    if any(p.get("counts", {}).get("form", 0) for p in pages_seen):
        add("R-007", "Make outbound email survive automated summarisation",
            "Inboxes increasingly show a generated summary instead of the message. Substance carried in images, "
            "or buried under filler, does not reach the summary and effectively disappears.",
            ["Put the point in the first two lines as plain text.",
             "Never carry the offer, date or price only inside an image - repeat them in text.",
             "Cut boilerplate that dilutes the summarisable content.",
             "Keep a text/plain alternative part in every campaign."],
            effort="low")

    add("R-008", "Give the site a stable, quotable vocabulary",
        "Consistent naming is what lets separate mentions be recognised as the same entity. Products renamed "
        "across pages, or described differently in each place, fragment into several apparent things.",
        ["Fix one canonical name and one one-line description per product, and use them verbatim everywhere.",
         "Add a glossary page defining the terms the brand uses in its own way.",
         "Avoid internal codenames in public copy."])

    return recs


# Pairs of checks that legitimately observe ONE root cause from two stages of the pipeline.
# Both are correct in isolation, but a reader gets the same remedy twice, in two dimensions,
# occupying two slots in "Do these first". The earlier stage owns the finding; the later one
# folds into it as a consequence clause.
#
# This is composition work and belongs here rather than in either skill: neither analyzer can
# see the other's output, and that separation is deliberate.
ROOT_CAUSE_MERGES = [
    {"owner": "ACC-016", "defers": "ENG-006b",
     "clause": "The same latency also costs visitors on arrival, so this one fix serves both "
               "the discoverability and the engagement side."},
]


def merge_root_causes(findings):
    """Fold a deferring finding into its owner when both describe the same root cause."""
    by_id = {f["check_id"]: f for f in findings}
    dropped = []
    for rule in ROOT_CAUSE_MERGES:
        owner, defer = by_id.get(rule["owner"]), by_id.get(rule["defers"])
        if not owner or not defer:
            continue
        # Only merge when they really are about the same pages.
        shared = set(owner.get("affected_urls", [])) & set(defer.get("affected_urls", []))
        if not shared:
            continue
        owner["evidence"] = owner["evidence"].rstrip() + " " + rule["clause"]
        owner["affected_urls"] = sorted(set(owner.get("affected_urls", [])) |
                                        set(defer.get("affected_urls", [])))
        owner["affected_count"] = len(owner["affected_urls"])
        dropped.append(rule["defers"])
    return [f for f in findings if f["check_id"] not in dropped], dropped


def build_report(meta, results, pages_seen):
    pages = meta.get("pages_fetched", 0)
    raw = []
    verification_tasks = []
    notes = []
    for name, payload in results.items():
        for f in payload.get("findings", []):
            f["dimension"] = payload.get("dimension", name)
            f["source_skill"] = payload.get("skill", name)
            raw.append(f)
        verification_tasks.extend(payload.get("agent_verification_tasks", []))
        for note in payload.get("notes", []):
            notes.append(f"{payload.get('skill', name)}: {note}")

    findings = []
    for f in raw:
        sev, reason = adjust_severity(f, pages)
        findings.append({**f, "severity": sev, "severity_note": reason})

    findings, merged_away = merge_root_causes(findings)
    if merged_away:
        notes.append("audit-orchestrator: folded " + ", ".join(merged_away) +
                     " into the earlier-stage finding describing the same root cause, so the "
                     "remedy is listed once.")

    order = {"crawl-access-audit": 0, "render-extractability-audit": 1, "structured-data-audit": 2,
             "answerability-audit": 3, "freshness-corroboration-audit": 4, "engagement-audit": 5}
    findings.sort(key=lambda f: (RANK[f["severity"]], order.get(f["source_skill"], 9), f["check_id"]))

    for i, f in enumerate(findings, 1):
        f["id"] = f"F-{i:03d}"
        f["suggested_action"]["priority"] = PRIORITY[f["severity"]]

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    by_dimension = {}
    for f in findings:
        by_dimension[f["dimension"]] = by_dimension.get(f["dimension"], 0) + 1

    top = [{"finding_id": f["id"], "priority": f["suggested_action"]["priority"],
            "action": f["suggested_action"]["summary"], "affects": f["affected_count"]}
           for f in findings if f["severity"] in ("critical", "high", "medium")][:8]

    limitations = [
        "Findings are based on a sample of pages, not a full crawl; counts are relative to that sample.",
        "The collector does not execute JavaScript, which mirrors how most AI fetchers behave but means "
        "post-render content is inferred unless a rendered capture was supplied.",
        "Off-site corroboration and entity ambiguity cannot be measured from the site alone and are emitted "
        "as verification tasks.",
        "Third-party crawler user-agents are never impersonated; their access is evaluated statically "
        "against robots.txt.",
    ]
    if meta.get("budget_exhausted"):
        limitations.append("The time budget was reached during collection, so fewer pages were sampled than requested.")
    if meta.get("aborted") == "robots_disallow":
        limitations.append("robots.txt disallows this auditor, so only robots-level checks could run.")
    # A tiny sample must not be presented with the same authority as a full one. This goes
    # first in the list because it changes how every other finding should be read.
    home_probe = meta.get("probes", {}).get("home", {})
    walled = bool((home_probe.get("status") in (401, 403, 429) or home_probe.get("challenge_markers")
                   or home_probe.get("cf_mitigated"))
                  and not any(p.get("status") == 200 and p.get("parse_ok") for p in pages_seen))

    # When a wall blocked everything, the small-sample advice ("re-run once the site exposes
    # a crawlable path") points at the wrong cause: the link graph is not the problem, the
    # refusal is. Say that instead, not as well.
    if walled:
        pass
    elif pages and pages < 3:
        limitations.insert(0, (
            f"ONLY {pages} PAGE(S) COULD BE SAMPLED. Every finding in this report describes that page, "
            "not the site. "
            "Treat this report as a homepage audit and re-run once the site exposes a crawlable path to its "
            "other pages (see the crawl-path finding, if present)."))
    elif pages and pages < 8:
        limitations.insert(0, (
            f"Only {pages} pages could be sampled, so template-level conclusions are weakly supported; "
            "findings describe the pages listed rather than the site as a whole."))
    # Goes last so its insert(0) lands on top of every other limitation: it changes how the
    # whole report should be read.
    if walled:
        # Distinguish "nothing was readable" from "pages were refused but robots.txt was not".
        # The second is common - WAFs usually exempt robots.txt - and in that case the
        # robots-derived findings are real evidence, not guesses.
        robots_readable = meta.get("robots", {}).get("status") in (200, 404)
        limitations.insert(0, (
            "THE SITE REFUSED THIS CLIENT for every page (bot wall / WAF challenge). robots.txt was "
            "readable, so any robots-based finding here is evidence-backed; everything that needed a "
            "successful page fetch was skipped, not guessed. This is not a clean bill of health for the "
            "rest. Re-run from an allowlisted client or IP to audit the site's own pages."
            if robots_readable else
            "THE SITE REFUSED THIS CLIENT (bot wall / WAF challenge). No page could be read, so the only "
            "finding supported by evidence is the block itself. Checks needing a successful fetch were "
            "skipped, not guessed - this is not a clean bill of health for anything else. Re-run from an "
            "allowlisted client or IP to audit the site's own configuration."))

    if meta.get("discovery_rounds", 1) > 1:
        limitations.append("The sitemap was absent or thin, so additional pages were discovered by following "
                           "links from already-fetched pages.")
    if any(sm.get("truncated") for sm in meta.get("sitemaps", [])):
        limitations.append("One or more sitemap files were larger than the fetch cap and were truncated before "
                           "parsing; discovered URL and sampled-page counts are a lower bound, not a full count.")

    report = {
        "site": meta.get("site", ""),
        "audited_at": meta.get("audited_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audit_version": VERSION,
        "scope": {
            "origin": meta.get("origin", ""),
            # pages_sampled is the number findings are actually computed over, so it counts
            # pages that were fetched AND parsed. Reporting fetched-but-unreadable pages here
            # made the scope block contradict every "N/M pages" evidence line - a bot-walled
            # site claimed 4 pages sampled while no page could be read at all.
            "pages_sampled": len([p for p in pages_seen
                                  if p.get("status") == 200 and p.get("parse_ok")]),
            "pages_fetched": pages,
            "urls_discovered": meta.get("discovered", 0),
            "page_classes": meta.get("page_classes", []),
            "site_type": meta.get("site_type", "unknown"),
            "rendered_capture": bool(meta.get("config", {}).get("rendered_capture")),
            "robots_respected": True,
            "collection_seconds": meta.get("elapsed_s"),
        },
        "summary": {
            "total_findings": len(findings),
            "critical": counts["critical"], "high": counts["high"],
            "medium": counts["medium"], "low": counts["low"], "info": counts["info"],
            "by_dimension": by_dimension,
            "top_actions": top,
        },
        "findings": findings,
        "proactive_recommendations": proactive(meta, {f["check_id"] for f in findings}, pages_seen),
        "agent_verification_tasks": verification_tasks,
        "analyzer_notes": notes,
        "limitations": limitations,
    }
    return report


def to_markdown(r):
    s = r["summary"]
    lines = [f"# AI-readiness audit - {r['site']}", "",
             f"Audited {r['audited_at']} - {s['total_findings']} findings across "
             f"{r['scope']['pages_sampled']} sampled pages (site type: {r['scope']['site_type']}).", "",
             "| Severity | Count |", "| --- | --- |"]
    for sev in SEVERITIES:
        lines.append(f"| {sev} | {s[sev]} |")
    lines += ["", "## Do these first", ""]
    for a in s["top_actions"]:
        lines.append(f"- **{a['priority']} ({a['finding_id']})** {a['action']}")
    # Findings are split by severity. A flat list of 20+ items is not an action list, and
    # the rubric asks for a report a non-expert could act on: the things that matter go
    # above the fold, the long tail goes behind a fold, and nothing is dropped.
    major = [f for f in r["findings"] if f["severity"] in ("critical", "high", "medium")]
    minor = [f for f in r["findings"] if f["severity"] in ("low", "info")]

    lines += ["", "## Findings that matter", ""]
    if not major:
        lines += ["No critical, high or medium findings. See the minor findings below and the "
                  "proactive recommendations.", ""]
    for f in major:
        lines += [f"### {f['id']} - {f['title']}",
                  f"*{f['severity'].upper()} - {f['dimension']} - confidence {f['confidence']} - "
                  f"{f['affected_count']} page(s) - via {f['source_skill']}*", "",
                  f"**Evidence.** {f['evidence']}", ""]
        if f.get("severity_note"):
            lines += [f"*Severity note: {f['severity_note']}.*", ""]
        if f.get("verify_by"):
            lines += [f"*To confirm: {f['verify_by']}*", ""]
        lines += [f"**Fix ({f['suggested_action']['priority']}).** {f['suggested_action']['summary']}", ""]
        for step in f["suggested_action"].get("steps", []):
            lines.append(f"- {step}")
        lines += ["", f"*Expected effect: {f['suggested_action'].get('expected_effect', '')}*", ""]
        if f.get("affected_urls"):
            lines += ["<details><summary>Affected URLs</summary>", ""]
            lines += [f"- {u}" for u in f["affected_urls"]]
            lines += ["", "</details>", ""]
    if minor:
        lines += ["## Minor findings and opportunities", "",
                  f"{len(minor)} lower-severity item(s). Worth fixing, but none of them is why the brand is "
                  "or is not being cited today.", "",
                  "<details><summary>Show minor findings</summary>", ""]
        for f in minor:
            lines += [f"### {f['id']} - {f['title']}",
                      f"*{f['severity'].upper()} - {f['dimension']} - confidence {f['confidence']} - "
                      f"{f['affected_count']} page(s) - via {f['source_skill']}*", "",
                      f"**Evidence.** {f['evidence']}", ""]
            if f.get("severity_note"):
                lines += [f"*Severity note: {f['severity_note']}.*", ""]
            if f.get("verify_by"):
                lines += [f"*To confirm: {f['verify_by']}*", ""]
            lines += [f"**Fix ({f['suggested_action']['priority']}).** {f['suggested_action']['summary']}", ""]
            for step in f["suggested_action"].get("steps", []):
                lines.append(f"- {step}")
            lines.append("")
        lines += ["</details>", ""]

    lines += ["## Recommended beyond the defects found", ""]
    for rec in r["proactive_recommendations"]:
        lines += [f"### {rec['id']} - {rec['title']} ({rec['effort']} effort)", "", rec["rationale"], ""]
        lines += [f"- {st}" for st in rec["steps"]]
        lines.append("")
    if r["agent_verification_tasks"]:
        lines += ["## Verification tasks (need the open web)", ""]
        for t in r["agent_verification_tasks"]:
            lines.append(f"- **{t['id']} {t['purpose']}** - search `{t['query']}` - {t['check']}")
        lines.append("")
    lines += ["## Limitations", ""] + [f"- {l}" for l in r["limitations"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Run the full brand AI-readiness audit.")
    ap.add_argument("--url", help="Site to audit")
    ap.add_argument("--pack", help="Reuse an existing site pack instead of collecting")
    ap.add_argument("--out-dir", default="./audit-output")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--budget", type=float, default=240.0,
                    help="Total wall-clock budget in seconds for the WHOLE audit "
                         "(collection + all analyzers). The brief requires under 5 minutes; "
                         "this is enforced as a hard deadline, not a target.")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args()
    if not args.url and not args.pack:
        ap.error("one of --url or --pack is required")

    root = marketplace_root(os.path.dirname(os.path.abspath(__file__)))
    skills, manifest = skill_paths(root)
    os.makedirs(args.out_dir, exist_ok=True)
    pack = args.pack or os.path.join(args.out_dir, "pack")

    # One deadline governs the whole run. Collection gets the larger share because it is
    # network-bound; the analyzers are CPU-only over an already-collected pack and finish
    # in well under a second each in practice. Each stage is capped against the time that
    # actually remains, so the total cannot drift past the budget however slow a site is.
    deadline = time.time() + args.budget
    collect_budget = round(args.budget * 0.6, 1)

    if not args.pack:
        collector = os.path.join(skills["crawl-access-audit"], "scripts", "collect_site_pack.py")
        cmd = [sys.executable, collector, "--url", args.url, "--out", pack,
               "--max-pages", str(args.max_pages), "--budget", str(collect_budget),
               "--timeout", str(args.timeout)]
        if args.keep_raw:
            cmd.append("--keep-raw")
        # Rough feasibility check before spending the budget. Observed throughput on real
        # sites is around one page per second including the politeness delay, so a large
        # --max-pages against the default budget will be cut off partway.
        need = args.max_pages * 1.3
        if need > collect_budget:
            fits = int(collect_budget / 1.3)
            print(f"[warn] --max-pages {args.max_pages} needs roughly {need:.0f}s to collect but the budget "
                  f"allows {collect_budget:.0f}s (~{fits} pages). Collection will stop early. "
                  f"Re-run with --budget {int(need / 0.6) + 60} for the full sample.", file=sys.stderr)

        code, stdout, stderr = run(cmd, timeout=collect_budget + 30)
        print(stdout.strip() or stderr.strip(), file=sys.stderr)
        if code == 3:
            print("Collection blocked by robots.txt - emitting a robots-only report.", file=sys.stderr)
        elif code in (4, 5):
            # 4 = the URL was not usable, 5 = the site could not be reached at all.
            # Neither is a finding about the site, so no report is written.
            try:
                detail = json.loads(stderr.strip().splitlines()[-1])
            except (ValueError, IndexError):
                detail = {}
            if code == 4:
                head = f"THE URL COULD NOT BE USED - {args.url!r}"
                body = [f"Reason: {detail.get('error', 'malformed URL')}.", "",
                        "Pass a plain http(s) URL with no surrounding quotes, brackets or",
                        "whitespace, for example:  --url https://example.com"]
            else:
                head = f"THE SITE COULD NOT BE REACHED - {detail.get('origin', args.url)}"
                body = [f"Reason: {detail.get('error', 'no response')}.", "",
                        "No report was written, because nothing about the site was observed.",
                        "Reporting 'no sitemap' or 'robots.txt misconfigured' here would blame",
                        "the site for a request that never arrived.", "",
                        "Check the hostname, your network, and whether the site blocks this client."]
            write_note(args.out_dir, [head, ""] + body)
            print(head, file=sys.stderr)
            return 2
        elif code not in (0, 3):
            reason = (f"the collector exceeded its {collect_budget:.0f}s time budget and was stopped"
                      if code == 124 else f"the collector exited with code {code}")
            write_note(args.out_dir, [
                f"AUDIT DID NOT COMPLETE - {args.url}", "",
                f"No report was produced because {reason}.", "",
                "What to do:",
                f"  * Raise the budget:   --budget {int(args.max_pages * 1.3 / 0.6) + 60}",
                f"  * Or sample fewer:    --max-pages {max(10, int(collect_budget / 1.3))}",
                "", "Collector output:", "  " + (stderr.strip()[:600] or "(none)"),
            ])
            print(f"Collector failed (exit {code}): {stderr[:400]}", file=sys.stderr)
            print(f"Wrote diagnosis to {os.path.join(args.out_dir, 'summary.txt')}", file=sys.stderr)
            return 2

    meta_path = os.path.join(pack, "meta.json")
    if not os.path.exists(meta_path):
        write_note(args.out_dir, [
            "AUDIT DID NOT COMPLETE", "",
            f"No collection metadata at {meta_path}, so there is nothing to analyze.",
            "The collection step did not finish writing its output.", "",
            "Re-run with a larger --budget, or with a smaller --max-pages.",
        ])
        print(f"No pack metadata at {meta_path}; nothing to analyze.", file=sys.stderr)
        return 2
    with open(meta_path) as f:
        meta = json.load(f)

    results = {}
    skipped = []
    for i, (skill_id, script, name) in enumerate(ANALYZERS):
        remaining = deadline - time.time()
        left = len(ANALYZERS) - i
        if remaining <= 2:
            skipped.append(skill_id)
            continue
        # Share what is left evenly across the analyzers still to run, so one slow stage
        # cannot starve the rest, and floor it so a stage always gets a usable slice.
        slice_s = max(10.0, remaining / left)
        path = os.path.join(skills[skill_id], "scripts", script)
        out_file = os.path.join(args.out_dir, f"findings-{name}.json")
        code, stdout, stderr = run([sys.executable, path, "--pack", pack, "--out", out_file],
                                   timeout=slice_s)
        if code != 0:
            print(f"[warn] {skill_id} exited {code}: {stderr[:300]}", file=sys.stderr)
            skipped.append(skill_id)
            continue
        with open(out_file) as f:
            results[name] = json.load(f)
    if skipped:
        print(f"[warn] analyzers not completed within budget: {', '.join(skipped)}", file=sys.stderr)

    pages_seen = []
    pages_path = os.path.join(pack, "pages.jsonl")
    if os.path.exists(pages_path):
        with open(pages_path) as f:
            pages_seen = [json.loads(line) for line in f if line.strip()]

    report = build_report(meta, results, pages_seen)
    if skipped:
        report["limitations"].insert(0, (
            "The audit did not complete within its time budget: "
            f"{', '.join(skipped)} did not run, so their dimensions are absent from this report. "
            "Re-run with a larger --budget, or with --pack to re-analyze the existing collection."))
        report["scope"]["analyzers_skipped"] = skipped
    with open(os.path.join(args.out_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.out_dir, "report.md"), "w") as f:
        f.write(to_markdown(report))

    validator = os.path.join(skills["audit-orchestrator"], "scripts", "validate_report.py")
    code, stdout, stderr = run([sys.executable, validator, os.path.join(args.out_dir, "report.json")], timeout=30)
    print(stdout.strip() or stderr.strip(), file=sys.stderr)

    s = report["summary"]
    # Also write the summary to disk. Some agent harnesses and CI wrappers capture files
    # but discard stdout; printing the only human-readable confirmation to a stream that
    # may be swallowed makes a successful run look like a failed one, and invites the
    # caller to re-run (and re-crawl the site) to see output that already exists.
    summary_lines = [
        f"AI-readiness audit - {report['site']}",
        f"Audited {report['audited_at']}",
        "",
        f"Pages sampled : {report['scope']['pages_sampled']} (of {report['scope']['urls_discovered']} discovered)",
        f"Site type     : {report['scope']['site_type']}",
        f"Findings      : {s['total_findings']}  "
        f"(critical {s['critical']}, high {s['high']}, medium {s['medium']}, low {s['low']}, info {s['info']})",
        "",
        "Do these first:",
    ]
    for a in s["top_actions"][:5]:
        summary_lines.append(f"  {a['priority']} ({a['finding_id']}) {a['action']}")
    if report["limitations"]:
        summary_lines += ["", "Read this first:", "  " + report["limitations"][0]]
    summary_lines += ["", "Full report: report.md (readable) / report.json (structured)"]
    with open(os.path.join(args.out_dir, "summary.txt"), "w") as f:
        f.write("\n".join(summary_lines) + "\n")

    print(json.dumps({
        "site": report["site"], "pages_sampled": report["scope"]["pages_sampled"],
        "total_findings": s["total_findings"],
        "counts": {k: s[k] for k in SEVERITIES},
        "report_json": os.path.join(args.out_dir, "report.json"),
        "report_md": os.path.join(args.out_dir, "report.md"),
        "summary_txt": os.path.join(args.out_dir, "summary.txt"),
        "schema_valid": code == 0,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
