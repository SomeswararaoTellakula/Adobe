#!/usr/bin/env python3
"""
check_engagement.py - stage 6 of the audit: the visitor arrived. Do they stay?

Offline analyzer over a site pack. Traffic from an assistant behaves differently from search
traffic: the visitor lands deep rather than on the homepage, arrives holding a specific claim
the assistant made, and is checking whether the page confirms it. They have already been
given an answer, so a page that makes them hunt for it has nothing to hold them with.

This analyzer looks for what breaks that landing: no orientation, blocked first paint,
mobile failures, dead ends, and pages that cannot be linked into at the relevant section.

  python3 check_engagement.py --pack ./pack [--out findings.json]
"""

import argparse
import json
import os
import sys
import urllib.parse

SKILL = "engagement-audit"
DIMENSION = "engagement.landing"

DEEP_CLASSES = {"product", "pricing", "article", "docs", "about", "other"}


def load_pack(pack):
    with open(os.path.join(pack, "meta.json")) as f:
        meta = json.load(f)
    pages, path = [], os.path.join(pack, "pages.jsonl")
    if os.path.exists(path):
        with open(path) as f:
            pages = [json.loads(line) for line in f if line.strip()]
    return meta, pages


def finding(check_id, title, severity, confidence, urls, evidence, action, effect,
            steps=(), scales=False, verify="", tags=()):
    urls = sorted(set(urls))
    return {
        "check_id": check_id, "title": title, "severity": severity, "confidence": confidence,
        "affected_urls": urls[:20], "affected_count": len(urls), "evidence": evidence,
        "scales_with_coverage": scales, "verify_by": verify, "tags": list(tags),
        "suggested_action": {"summary": action, "steps": list(steps), "expected_effect": effect},
    }


def emit(findings, notes, out):
    payload = {"skill": SKILL, "dimension": DIMENSION, "version": "1.0.0",
               "findings": sorted(findings, key=lambda f: f["check_id"]), "notes": notes}
    text = json.dumps(payload, indent=2)
    if out:
        with open(out, "w") as f:
            f.write(text)
    else:
        print(text)


def analyze(meta, pages):
    out, notes = [], []
    ok = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    deep = [p for p in ok if p.get("page_class") in DEEP_CLASSES]
    n = len(ok)
    probes = meta.get("probes", {})
    if not ok:
        notes.append("No successfully parsed pages in the pack; engagement checks skipped.")
        return out, notes

    # -- ENG-001: deep-landing orientation ------------------------------------
    disoriented = [p for p in deep
                   if not p.get("has_breadcrumb") and not p.get("brand_in_first_chunk") and p.get("h1")]
    if deep and len(disoriented) >= max(2, 0.5 * len(deep)):
        out.append(finding(
            "ENG-001", "Deep pages do not tell the visitor where they have landed", "medium", "medium",
            [p["url"] for p in disoriented],
            f"{len(disoriented)}/{len(deep)} deep page(s) show no breadcrumb trail and do not name the brand in "
            "the opening content. A visitor sent here by an assistant arrives with no memory of navigating in, "
            "so the page has to re-establish whose site this is and what part of it they are in.",
            "Add a breadcrumb trail and make the opening lines state the brand, the page's subject and its "
            "place in the site.",
            "The visitor can orient in one glance instead of bouncing back to the assistant.",
            steps=["Render a breadcrumb on every non-home template and mark it up as BreadcrumbList.",
                   "Ensure the H1 states the subject in full ('Pro plan pricing - Acme') rather than a "
                   "fragment ('Pro').",
                   "Include a one-line 'what this page is' summary under the H1.",
                   "Assume the visitor has never seen the homepage - because increasingly they have not."],
            scales=True, tags=("orientation", "deep-landing")))

    # -- ENG-002: blocked first paint -----------------------------------------
    blocked = [p for p in ok if p.get("modal_hints", 0) >= 2 or p.get("auto_open_hints")]
    if blocked and len(blocked) >= max(2, 0.4 * n):
        # On nearly every page this is one banner in the shared template, not N separate
        # defects. Listing 98 URLs invites someone to go and edit 98 pages; saying it is
        # one shared component points at the single place the fix belongs.
        site_wide_overlay = len(blocked) >= max(3, int(0.85 * n))
        shape = (f"{len(blocked)}/{n} sampled pages carry it, so this is one shared component - "
                 "almost certainly the consent or cookie banner in the base template - rather than "
                 "a per-page defect. Fix it once, in that template."
                 if site_wide_overlay else
                 f"{len(blocked)}/{n} pages carry it, so specific templates are responsible.")
        out.append(finding(
            "ENG-002", ("A site-wide overlay covers the content on arrival" if site_wide_overlay
                        else "Overlays are likely to cover the content on arrival"),
            "medium", "low",
            [p["url"] for p in blocked],
            "Markup for modals, consent walls or auto-opening overlays was found"
            + (" including timer- or exit-intent-triggered ones" if any(p.get("auto_open_hints") for p in blocked)
               else "") + f". {shape} "
            "A visitor who arrived for one specific fact and is met by an interstitial usually leaves rather "
            "than dismissing it.",
            "Delay or remove interstitials for first-time deep landings, and never cover the content a visitor "
            "arrived to read.",
            "The fact the visitor came for is visible immediately, which is the whole opportunity.",
            steps=["Suppress newsletter and promo modals on the first pageview of a session.",
                   "Keep consent banners compact and non-blocking; never gate content behind them where "
                   "the law does not require it.",
                   "Delay chat widgets until there is a scroll or dwell signal.",
                   "Check on mobile, where an overlay covers proportionally far more of the screen."],
            verify="Load a deep page in a fresh private window on a phone and note what covers the content.",
            tags=("interstitial", "first-paint")))

    # -- ENG-003: dead ends ---------------------------------------------------
    dead_ends = [p for p in deep if p.get("links_internal_main", 0) == 0 and p.get("word_count", 0) >= 80]
    if dead_ends:
        out.append(finding(
            "ENG-003", "Content pages offer no next step", "medium", "high", [p["url"] for p in dead_ends],
            f"{len(dead_ends)}/{len(deep)} deep page(s) contain no in-content internal links - only "
            "navigation furniture. A visitor who is satisfied by the answer has nowhere to go next, and one "
            "who is not has nowhere to look.",
            "Add contextual links in the body to the obvious next questions, and one clear task-continuing "
            "call to action.",
            "Converts a single answered question into a session instead of a bounce.",
            steps=["From each page, ask 'what does someone who just read this want next?' and link to it "
                   "in the body text, not just the nav.",
                   "Add a specific CTA that continues the task ('See plan limits', 'Book a 20-minute "
                   "walkthrough') rather than a generic 'Learn more'.",
                   "Link related pages reciprocally so the cluster is navigable from any entry point."],
            scales=True, tags=("next-step", "internal-links")))

    # -- ENG-004: mobile viewport ---------------------------------------------
    no_viewport = [p["url"] for p in ok if not p.get("viewport")]
    if no_viewport:
        out.append(finding(
            "ENG-004", "Pages do not declare a mobile viewport", "high", "high", no_viewport,
            f"{len(no_viewport)}/{n} pages have no viewport meta tag, so mobile browsers render a desktop-width "
            "layout and zoom out. Most assistant traffic is mobile.",
            "Add the standard viewport meta tag and verify each template on a phone-width screen.",
            "Pages become usable at the screen size most visitors actually arrive on.",
            steps=["Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> to the "
                   "base template.",
                   "Test each template at 375px wide and fix horizontal overflow.",
                   "Do not disable user scaling - it breaks accessibility."],
            tags=("mobile",)))

    # -- ENG-005: layout stability and weight ---------------------------------
    cls_risk = [p for p in ok if p.get("images_total", 0) >= 5
                and p.get("images_no_dims", 0) >= 0.6 * p.get("images_total", 1)]
    if cls_risk:
        total = sum(p.get("images_total", 0) for p in cls_risk)
        missing = sum(p.get("images_no_dims", 0) for p in cls_risk)
        out.append(finding(
            "ENG-005", "Images have no dimensions, so the layout shifts while loading", "low", "high",
            [p["url"] for p in cls_risk],
            f"{missing}/{total} images across {len(cls_risk)} page(s) declare neither width nor height. Content "
            "jumps as images load, which is a common cause of mis-taps and early exits on mobile.",
            "Set explicit width and height (or aspect-ratio) on every image so space is reserved before load.",
            "The page stops moving under the visitor's finger.",
            steps=["Add width and height attributes matching the intrinsic image size.",
                   "Reserve space for late-loading embeds and ad slots as well.",
                   "Use loading=\"lazy\" below the fold but never on the main above-fold image."],
            scales=True, tags=("stability", "mobile")))

    # A fixed byte threshold produces two very different findings that need different fixes,
    # and conflating them waters down the signal:
    #   - a MINORITY of pages are unusually heavy -> a per-page defect on specific templates
    #   - NEARLY ALL pages are heavy -> a property of the JS framework/bundle, fixed once at
    #     the architecture level, not by editing 20 pages one at a time
    # Splitting on whether the bloat is an outlier or the norm for this site is what keeps
    # the finding actionable instead of just restating "this site uses a JS framework."
    oversized = sorted([p for p in ok if p.get("html_bytes", 0) > 400_000],
                       key=lambda p: -p.get("html_bytes", 0))
    slow = [p for p in ok if p.get("elapsed_ms", 0) > 4000]
    # Half the sample or more is a property of the platform, not a set of outlier pages.
    # At 0.7 a site with 53% of pages over the threshold was still described as "a minority",
    # which contradicted its own evidence line.
    site_wide = len(oversized) >= max(3, int(0.5 * n))

    if oversized and not site_wide:
        out.append(finding(
            "ENG-006", "A minority of pages are unusually heavy", "medium", "high",
            [p["url"] for p in oversized],
            "; ".join(f"{p['url']}: {p.get('html_bytes', 0) // 1024} KB" for p in oversized[:5]) +
            f" ({len(oversized)}/{n} pages, well above the rest of the sample). Weight on these specific "
            "templates costs visitors before anything is read.",
            "Reduce document weight on these specific templates.",
            "Faster first paint on the pages that are actually outliers.",
            steps=["Compare these templates against the rest of the site to find what they load that others "
                   "do not - usually an embed, a large dataset inlined into the page, or a tracking script.",
                   "Move large inline payloads to external, deferred files.",
                   "Re-measure after the change and confirm these pages now match the rest of the sample."],
            tags=("performance",)))
    elif oversized:
        out.append(finding(
            "ENG-006", "Document weight is high across the whole site", "low", "high",
            [p["url"] for p in oversized[:5]],
            f"{len(oversized)}/{n} sampled pages exceed 400 KB of HTML, which reflects the JS framework's "
            "bundle and hydration payload across the site rather than a defect on these specific pages. "
            "Listing all of them would restate one architectural fact as if it were 20 separate problems.",
            "Address bundle size at the framework/build level rather than per page.",
            "One fix improves every page instead of 20 separate edits.",
            steps=["Audit what the framework inlines into every page (hydration data, route manifests, "
                   "duplicated chunks) and see what can be deferred or code-split.",
                   "Check for third-party scripts and tag managers loaded on every route.",
                   "Re-measure the sample median after the change rather than chasing individual pages."],
            tags=("performance", "architecture")))

    if slow:
        out.append(finding(
            "ENG-006b", "Some pages are slow to arrive", "medium", "high", [p["url"] for p in slow],
            "; ".join(f"{p['url']}: {p.get('elapsed_ms')} ms" for p in slow[:5]) +
            f" ({len(slow)}/{n} pages exceeded 4000 ms). Latency costs visitors before anything is read, and "
            "the same slowness risks fetcher timeouts on the discoverability side.",
            "Cut time-to-first-byte on the slow templates.",
            "Faster first paint for visitors and fewer dropped fetches by machines.",
            steps=["Profile server-side rendering and any database calls on the slow templates.",
                   "Serve HTML from a CDN edge cache with a sensible TTL."],
            tags=("performance",)))

    # -- ENG-007: unhelpful error handling ------------------------------------
    nf = probes.get("not_found", {})
    if nf.get("status") == 404 and nf.get("body_words", 0) < 40 and nf.get("has_links", 0) < 3:
        out.append(finding(
            "ENG-007", "The 404 page is a dead end", "low", "high", [meta.get("probes", {}).get("not_found", {}).get("url")
             or urllib.parse.urljoin(meta.get("origin", ""), "/brand-ai-readiness-audit-probe-404")],
            f"A missing URL returned a 404 page with roughly {nf.get('body_words', 0)} words and "
            f"{nf.get('has_links', 0)} link(s). Assistants sometimes cite URLs that have since moved; a bare "
            "404 turns that near-miss into a lost visitor.",
            "Make the 404 page a recovery point: search, popular destinations, and a route to the likely intent.",
            "Visitors arriving on a stale citation are recovered instead of lost.",
            steps=["Add on-site search and links to the main sections on the 404 template.",
                   "Log 404s with referrers and 301 the recurring ones to the right page.",
                   "Keep the 404 status code - only the content should improve."],
            tags=("recovery", "errors")))

    # -- ENG-008: vague link text ---------------------------------------------
    vague = [p for p in ok if p.get("generic_link_text", 0) >= 5]
    if vague:
        out.append(finding(
            "ENG-008", "Links are labelled 'click here' or 'learn more'", "low", "high",
            [p["url"] for p in vague],
            "; ".join(f"{p['url']}: {p.get('generic_link_text')} non-descriptive link labels"
                      for p in sorted(vague, key=lambda x: -x.get("generic_link_text", 0))[:5]) +
            ". The label is the only preview of the destination for a scanning visitor, a screen-reader user, "
            "and a machine deciding what the link is about.",
            "Label every link with its destination or outcome.",
            "Visitors can choose the next step without clicking to find out what it is.",
            steps=["Rewrite 'learn more' as the destination ('Read the deployment guide').",
                   "Keep labels consistent with the destination page's H1 where possible."],
            scales=True, tags=("navigation", "accessibility")))

    # -- ENG-009: content gated behind a form ---------------------------------
    # A newsletter signup box is not a content gate, and a listing/index page (many links
    # to other content, little content of its own) is not "thin content hidden behind a
    # form" - it is a directory page doing its job. Both patterns previously produced a
    # false positive on any blog index with an email subscribe box. Password fields are a
    # strong, unambiguous gating signal; a bare email field on a genuinely thin single page
    # is a weaker one and is still flagged, but only once listing pages are excluded.
    def looks_like_listing(p):
        return p.get("links_internal_main", 0) >= 8

    gated = []
    for p in deep:
        if p.get("counts", {}).get("form", 0) < 1 or looks_like_listing(p):
            continue
        fields = p.get("form_field_types", [])
        if "password" in fields and p.get("word_count", 0) < 150:
            gated.append(p)
        elif "email" in fields and p.get("word_count", 0) < 80:
            gated.append(p)
    if gated:
        out.append(finding(
            "ENG-009", "Content pages appear to be gated behind a form", "medium", "low",
            [p["url"] for p in gated],
            f"{len(gated)}/{len(deep)} deep page(s) carry very little content alongside a password or email "
            "field, and are not listing/index pages. If this is a genuine paywall or login gate, a visitor "
            "sent by an assistant to verify one fact will not register to see it, and the fetcher could not "
            "read it either. If it is a newsletter box on an otherwise complete page, no action is needed - "
            "confirm which before treating this as a fix.",
            "Put the substance in front of the gate and ask for the email afterwards.",
            "The fact is verifiable on arrival, and the page becomes citable rather than invisible.",
            steps=["Publish the core content ungated, then offer the deeper artefact (template, dataset, "
                   "full report) in exchange for details.",
                   "Gate on value, not on access to basic facts.",
                   "Where a login is genuinely required, publish an ungated summary page that can be cited."],
            verify="Open the page in a private window and confirm whether content is actually withheld.",
            tags=("gating", "conversion")))

    # -- ENG-010: no on-site search -------------------------------------------
    has_search = any("search" in p.get("form_field_types", []) or
                     any("search" in (f.get("title", "") or "").lower() for f in p.get("iframes", []))
                     for p in ok)
    if n >= 6 and not has_search:
        out.append(finding(
            "ENG-010", "No on-site search in the served HTML", "low", "medium",
            [meta.get("origin", "")],
            f"No search input was found in the served HTML of {n} sampled pages - it may still exist and be "
            "rendered by JavaScript, which is why this is reported at low confidence. Assistant citations are "
            "often approximate; "
            "without search, a visitor who lands one page away from what they wanted has to navigate blind.",
            "Add on-site search, and review its query logs as a source of the questions your content should answer.",
            "Recovers misdirected arrivals and reveals the real questions to write for.",
            steps=["Add a search input to the header on every template.",
                   "Review search logs monthly - they are the cheapest source of question-shaped content ideas.",
                   "Show useful zero-result pages with suggested destinations."],
            verify="Confirm no search exists rather than being rendered client-side after load.",
            tags=("navigation", "recovery")))

    notes.append(f"Engagement checks evaluated {n} parsed pages ({len(deep)} deep-landing candidates). "
                 "Interstitial and gating checks are markup-based inferences and are flagged for visual "
                 "confirmation rather than asserted.")
    return out, notes


def main():
    ap = argparse.ArgumentParser(description="On-site engagement analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages = load_pack(args.pack)
    findings, notes = analyze(meta, pages)
    emit(findings, notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
