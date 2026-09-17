#!/usr/bin/env python3
"""
check_render.py - stage 2 of the audit: having reached the page, can a machine read it?

Offline analyzer over a site pack. Detects content that exists for a human eye but not for
a text fetcher: client-side-rendered shells, facts locked inside images or media, content
behind interaction, and markup that carries almost no extractable text.

  python3 check_render.py --pack ./pack [--out findings.json]

If the pack contains a rendered/ directory (post-JavaScript text captured by a browser
tool), the raw-vs-rendered delta is measured directly and confidence is raised to high.
Without it, JS dependence is inferred from framework shells and text volume, and findings
are marked for verification rather than asserted.
"""

import argparse
import json
import os
import re
import sys

SKILL = "render-extractability-audit"
DIMENSION = "discoverability.extractability"

# Content-bearing page classes. Legal and contact pages are excluded from "thin content"
# style checks because being short is normal for them.
CONTENT_CLASSES = {"home", "product", "pricing", "article", "docs", "about", "other"}

# Interactive applications - games, puzzles, calculators, editors - legitimately have no
# body text to serve: the app IS the page. Telling their owner to "server-render the
# primary content" is wrong advice, because there is no prose to render. They still have a
# real discoverability problem (nothing describes them to a machine), but the fix is a text
# description beside the app, not SSR of the app itself.
INTERACTIVE_APP_RE = re.compile(
    r"(?:^|/)(?:games?|puzzles?|crosswords?|wordle|sudoku|play|arcade|quiz|"
    r"calculator|simulator|editor|playground|sandbox|demo|tool|tools|configurator|"
    r"dashboard|viewer|player|map|maps)(?:/|$|\?)", re.I)


def is_interactive_app(page):
    import urllib.parse as _u
    path = _u.urlsplit(page.get("url", "")).path.lower()
    return bool(INTERACTIVE_APP_RE.search(path)) or page.get("counts", {}).get("canvas", 0) > 0
FACT_CLASSES = {"product", "pricing", "docs"}


def load_pack(pack):
    with open(os.path.join(pack, "meta.json")) as f:
        meta = json.load(f)
    pages, path = [], os.path.join(pack, "pages.jsonl")
    if os.path.exists(path):
        with open(path) as f:
            pages = [json.loads(line) for line in f if line.strip()]
    rendered = {}
    rdir = os.path.join(pack, "rendered")
    if os.path.isdir(rdir):
        for name in sorted(os.listdir(rdir)):
            if name.endswith(".json"):
                with open(os.path.join(rdir, name)) as f:
                    try:
                        blob = json.load(f)
                        if blob.get("url"):
                            rendered[blob["url"]] = blob
                    except json.JSONDecodeError:
                        continue
    return meta, pages, rendered


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


def analyze(meta, pages, rendered):
    out, notes = [], []
    ok = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    content = [p for p in ok if p.get("page_class") in CONTENT_CLASSES]
    n = len(ok)
    if not ok:
        notes.append("No successfully parsed pages in the pack; extractability checks skipped.")
        return out, notes

    # -- REN-001: client-side rendering ---------------------------------------
    # Requires BOTH a thin document AND a framework/SPA signature. A framework alone is not
    # evidence: server-rendered React and Next.js sites ship full HTML and must not be flagged.
    csr, thin_only = [], []
    site_has_framework = any(set(p.get("framework", [])) & {
        "next", "nuxt", "react", "angular", "vue", "svelte", "remix", "gatsby"} for p in ok)
    # Count BOTH shell signatures. The gate exists to tell an isolated thin page apart from
    # a systematic pattern, so it must see every page carrying either signal.
    shell_pages = [p for p in ok
                   if p.get("spa_shell")
                   or (p.get("noscript_words", 0) > 0 and p.get("word_count", 0) < 60)]
    for p in ok:
        thin = p.get("word_count", 0) < 120
        shell = p.get("spa_shell") or (p.get("noscript_words", 0) > 0 and p.get("word_count", 0) < 60)
        framework = bool(set(p.get("framework", [])) & {
            "next", "nuxt", "react", "angular", "vue", "svelte", "remix", "gatsby"})
        if not thin or not (shell or framework):
            continue
        # A bare container div is weak evidence on its own. On a server-rendered site with
        # no framework bundle anywhere in the sample, a single thin page carrying one is far
        # more likely a stub or retired page than a client-rendered route - and calling it
        # "content assembled by JavaScript" at HIGH is a claim the markup does not support.
        corroborated = framework or site_has_framework or len(shell_pages) >= 2
        (csr if corroborated else thin_only).append(p)

    if thin_only:
        out.append(finding(
            "REN-001c", "Pages carry almost no text in the served HTML", "low", "low",
            [p["url"] for p in thin_only],
            f"{len(thin_only)}/{n} sampled page(s) served under 120 words inside a bare container element. "
            "No framework bundle was detected anywhere in the sample, so this is more likely a stub, "
            "retired or placeholder page than a client-rendered route - reported so it can be checked, "
            "not asserted as a rendering defect.",
            "Confirm whether these pages should still exist, and either give them real content or retire them.",
            "Removes near-empty pages that dilute the site's indexable surface.",
            steps=["Open each page and decide whether it is still meant to be published.",
                   "Retire or 301 pages that are no longer current.",
                   "For pages that should stay, add the content a visitor arriving from a search "
                   "would need - at minimum a sentence saying what the page covers."],
            verify="Open the page and confirm whether it is a stub or has content that only appears "
                   "after JavaScript runs.",
            tags=("rendering", "thin-content")))
    # Split interactive apps out before wording the finding, so each group gets advice that
    # actually applies to it.
    apps = [p for p in csr if is_interactive_app(p)]
    csr = [p for p in csr if p not in apps]

    if apps:
        out.append(finding(
            "REN-001b", "Interactive pages have no text description a machine can read",
            "medium", "high", [p["url"] for p in apps],
            f"{len(apps)}/{n} sampled pages are interactive applications (games, puzzles, tools) that serve "
            "almost no body text. That is expected for an app - the page is the application - but it means "
            "nothing on the page tells a machine what the app is, so it cannot be described or recommended "
            "even when people are actively searching for it by name.",
            "Add a served-HTML description beside the app: what it is, how it works, and how to play or use it.",
            "The application becomes describable and citable without changing how the app itself is built.",
            steps=["Add a short text section in the initial HTML above or below the app: what it is, the rules "
                   "or purpose, and who it is for.",
                   "Give the page a descriptive title, meta description and H1 naming the app.",
                   "Add the matching structured data (VideoGame, Game, SoftwareApplication, or WebApplication) "
                   "with name, description and url.",
                   "Do NOT server-render the application itself - that is not the problem here, and the app "
                   "should stay client-side."],
            tags=("rendering", "interactive-app")))

    if csr:
        delta_evidence = ""
        confidence = "medium"
        if rendered:
            deltas = []
            for p in csr:
                r = rendered.get(p["url"])
                if r and r.get("word_count"):
                    deltas.append((p["url"], p.get("word_count", 0), r["word_count"]))
            if deltas:
                confidence = "high"
                worst = max(deltas, key=lambda d: d[2] - d[1])
                delta_evidence = (f" Rendered capture confirms it: {worst[0]} holds {worst[1]} words before "
                                  f"JavaScript and {worst[2]} after.")
        out.append(finding(
            "REN-001", "Page content is assembled by JavaScript and absent from the served HTML",
            "critical" if len(csr) >= max(2, 0.5 * n) else "high", confidence,
            [p["url"] for p in csr],
            f"{len(csr)}/{n} sampled pages served an average of "
            f"{sum(p.get('word_count', 0) for p in csr) // max(1, len(csr))} words of body text, while "
            f"carrying a single-page-app shell or framework bundle "
            f"({', '.join(sorted({f for p in csr for f in p.get('framework', [])})) or 'empty root container'})."
            + delta_evidence +
            " Most AI fetchers read the served HTML and do not execute JavaScript, so this content does not exist for them.",
            "Server-render or pre-render the primary content of these pages so it is present in the initial HTML response.",
            "The text becomes visible to fetchers that do not run JavaScript, which is the precondition for being quoted.",
            steps=["Move the main content of these routes to server-side rendering or static generation "
                   "(SSR/SSG/ISR in the framework already in use).",
                   "Where a full migration is not possible, pre-render the top templates and serve the "
                   "HTML snapshot to all clients (not just to detected bots - cloaking by user-agent is fragile "
                   "and can be penalised).",
                   "Confirm the fix with `curl -s <url> | wc -w` - the served HTML should contain the same "
                   "facts the visitor sees.",
                   "Keep hydration for interactivity; only the content needs to be in the initial payload."],
            verify="" if confidence == "high" else
                   "Capture the post-JavaScript DOM with a browser tool and compare word counts against the "
                   "served HTML to confirm the gap.",
            scales=True, tags=("rendering", "javascript")))

    # -- REN-002: raw/rendered delta on otherwise healthy pages ---------------
    if rendered:
        gaps = []
        for p in ok:
            r = rendered.get(p["url"])
            if not r or not r.get("word_count"):
                continue
            raw_w, ren_w = p.get("word_count", 0), r["word_count"]
            if ren_w > 150 and raw_w < 0.5 * ren_w and p not in csr:
                gaps.append((p["url"], raw_w, ren_w))
        if gaps:
            out.append(finding(
                "REN-002", "A large share of visible text is added after page load", "high", "high",
                [g[0] for g in gaps],
                "; ".join(f"{u}: {a} words served vs {b} words rendered" for u, a, b in gaps[:5]) +
                f" ({len(gaps)} page(s) affected).",
                "Move the post-load content into the server response, especially anything a visitor would quote.",
                "Fetchers see the same substance a browser does.",
                steps=["Identify which components fetch content client-side (tabs, reviews, specs, FAQs).",
                       "Render their content server-side or inline it as static HTML.",
                       "Re-measure served vs rendered word count after the change."],
                scales=True, tags=("rendering",)))

    # -- REN-003: facts locked in images --------------------------------------
    # Only flag when the page has images AND no textual carrier for its facts. A spec table,
    # a definition list or a written price means the facts are already extractable, and a
    # low word count just reflects a dense page.
    image_facts = []
    for p in ok:
        if p.get("page_class") not in FACT_CLASSES or p.get("images_in_main", 0) < 1:
            continue
        counts = p.get("counts", {})
        has_textual_facts = (counts.get("table", 0) >= 1 or counts.get("dl", 0) >= 1
                             or bool(p.get("price_numbers")))
        if p.get("word_count", 0) < 120 and not has_textual_facts:
            image_facts.append(p)
    # product/pricing pages are the transactional class this check exists for: images there
    # plausibly ARE pricing tables or spec sheets. A "docs" page is different - its images
    # are as likely to be screenshots or diagrams, so the same wording overclaims what was
    # actually observed. Without tracking each image's DOM neighborhood (not collected),
    # the honest move is to make the claim proportional to what the class supports: assert
    # specific fact-types for product/pricing, describe the pattern more generally for docs.
    transactional = [p for p in image_facts if p.get("page_class") in ("product", "pricing")]
    illustrative = [p for p in image_facts if p.get("page_class") not in ("product", "pricing")]
    if transactional:
        out.append(finding(
            "REN-003", "Key facts appear to live in images rather than text", "high", "medium",
            [p["url"] for p in transactional],
            "; ".join(f"{p['url']}: {p.get('word_count', 0)} words of body text alongside "
                      f"{p.get('images_in_main', 0)} in-content image(s)" for p in transactional[:5]) +
            ". Pricing tables, spec sheets and comparison charts published as images cannot be read, "
            "quoted or compared by a text-based system.",
            "Publish the facts as real HTML text (tables, definition lists) and keep the graphic as an illustration.",
            "The specific numbers become extractable and quotable rather than invisible.",
            steps=["For each page, list the facts a buyer would ask about (price, plan limits, dimensions, "
                   "compatibility, hours).",
                   "Re-publish those as an HTML <table> or <dl> next to (or instead of) the image.",
                   "Give the image descriptive alt text that repeats the key fact.",
                   "Mirror the same values in structured data so they are machine-checkable."],
            verify="Open the page and confirm whether the numbers a customer needs are selectable text or pixels.",
            tags=("non-text", "images")))
    if illustrative:
        out.append(finding(
            "REN-003b", "Content pages carry little text alongside several images", "medium", "low",
            [p["url"] for p in illustrative],
            "; ".join(f"{p['url']}: {p.get('word_count', 0)} words of body text alongside "
                      f"{p.get('images_in_main', 0)} in-content image(s)" for p in illustrative[:5]) +
            ". This class of page is more likely to use images as illustration (screenshots, diagrams) than "
            "as the sole carrier of a fact, so this is reported as a pattern to check rather than an assertion "
            "that specific facts are hidden.",
            "Confirm whether any of these images carry a fact with no text equivalent, and add one if so.",
            "Any genuinely image-only fact becomes extractable; purely illustrative images are left alone.",
            steps=["Open each page and check whether any image is the only place a number, name or step "
                   "appears.",
                   "For images that are purely illustrative, this is not a defect - improving alt text is "
                   "still worthwhile but no fact needs to move to text.",
                   "For images that do carry a fact, add the equivalent as real HTML text nearby."],
            verify="Open the page and check whether any image is the sole source of a specific fact.",
            tags=("non-text", "images")))

    alt_gaps = [p for p in ok if p.get("images_total", 0) >= 5
                and p.get("images_missing_alt", 0) >= 0.5 * p.get("images_total", 1)]
    if alt_gaps:
        total_imgs = sum(p.get("images_total", 0) for p in alt_gaps)
        total_missing = sum(p.get("images_missing_alt", 0) for p in alt_gaps)
        out.append(finding(
            "REN-004", "Most images carry no alt text", "medium", "high", [p["url"] for p in alt_gaps],
            f"{total_missing}/{total_imgs} images across {len(alt_gaps)} page(s) have missing or empty alt "
            "attributes. Alt text is the only textual description of an image a fetcher receives - and the "
            "only description a screen-reader user receives.",
            "Write descriptive alt text for content images; keep alt=\"\" only for purely decorative ones.",
            "Image content becomes part of the page's extractable text and the page becomes accessible.",
            steps=["Distinguish content images from decorative ones.",
                   "For content images, describe what the image asserts, not what it depicts generically "
                   "('Pricing: Pro plan $49/user/month' beats 'pricing table').",
                   "Leave alt empty for decorative images so assistive tech skips them."],
            scales=True, tags=("non-text", "accessibility")))

    # -- REN-005: media without transcripts -----------------------------------
    media = [p for p in ok if p.get("video_without_track", 0) > 0
             or (p.get("counts", {}).get("audio", 0) > 0 and p.get("counts", {}).get("track", 0) == 0)]
    if media:
        out.append(finding(
            "REN-005", "Video or audio content has no text track or transcript", "medium", "medium",
            [p["url"] for p in media],
            f"{len(media)}/{n} sampled pages embed media without a <track> element. Anything explained only "
            "in the media is invisible to text-based retrieval.",
            "Publish a transcript on the page and attach caption tracks to the player.",
            "The substance of the media becomes indexable, quotable and accessible.",
            steps=["Generate transcripts (automatic transcription plus a human pass for names and numbers).",
                   "Publish the transcript as collapsible on-page HTML text, not a downloadable file.",
                   "Attach <track kind=\"captions\"> to the player and add VideoObject structured data with "
                   "a transcript property."],
            tags=("non-text", "media")))

    # -- REN-006: content in third-party iframes -------------------------------
    iframed = [p for p in content if p.get("iframes") and p.get("word_count", 0) < 200
               and any(f.get("src", "").startswith("http") for f in p.get("iframes", []))]
    if iframed:
        out.append(finding(
            "REN-006", "Primary content may be delivered inside an iframe", "medium", "medium",
            [p["url"] for p in iframed],
            "; ".join(f"{p['url']}: {p.get('word_count', 0)} words of own text plus "
                      f"{len(p.get('iframes', []))} iframe(s)" for p in iframed[:5]) +
            ". Fetchers read the parent document; iframe content is a separate resource that is usually not "
            "merged into the page's text.",
            "Move content out of iframes into the host document, or provide an HTML summary alongside.",
            "The content is attributed to the brand's own URL instead of being invisible or credited elsewhere.",
            steps=["Identify which iframes carry substantive content (booking widgets, embedded docs, catalogues).",
                   "Render the same information server-side in the parent page, even in summary form.",
                   "Keep the iframe for interactivity if needed - the text just has to exist outside it too."],
            verify="Confirm which iframes hold content rather than analytics, maps or video players.",
            tags=("non-text", "iframe")))

    # -- REN-007: interaction-gated content -----------------------------------
    gated = [p for p in content if p.get("aria_controls", 0) >= 4 and p.get("word_count", 0) < 250]
    if gated:
        out.append(finding(
            "REN-007", "Content is likely hidden behind tabs or accordions that load on interaction",
            "medium", "low", [p["url"] for p in gated],
            "; ".join(f"{p['url']}: {p.get('aria_controls', 0)} interactive disclosure controls but only "
                      f"{p.get('word_count', 0)} words in the served HTML" for p in gated[:5]) +
            ". If panels fetch their content on click, that content never reaches a fetcher.",
            "Ship the panel content in the initial HTML and hide it with CSS rather than fetching it on click.",
            "All panels become part of the document a fetcher retrieves in one request.",
            steps=["Check whether each tab/accordion panel is present in view-source or loaded by XHR.",
                   "For XHR-loaded panels, render the content server-side and toggle visibility with CSS.",
                   "This also removes a click before the visitor sees the answer they arrived for."],
            verify="View source (not devtools) and search for text that appears inside a closed panel.",
            tags=("rendering", "interaction")))

    # -- REN-008: markup-heavy, text-poor documents ---------------------------
    # A page already flagged by REN-001 (thin content behind a JS shell) will almost always
    # also have a low text-to-markup ratio - that is the same root cause showing up as a
    # second symptom, not a second problem. Excluding those pages here is what keeps one
    # framework-shell defect from being reported three times (REN-001 here, and previously
    # again via ENG-006-style page weight) instead of once with complete evidence.
    csr_urls = {p["url"] for p in csr}
    bloat = [p for p in content if p["url"] not in csr_urls
             and p.get("html_bytes", 0) > 120_000 and p.get("text_ratio", 1) < 0.03]
    if bloat:
        out.append(finding(
            "REN-008", "Documents are large but carry very little text", "low", "high",
            [p["url"] for p in bloat],
            "; ".join(f"{p['url']}: {p.get('html_bytes', 0) // 1024} KB of HTML, text ratio "
                      f"{p.get('text_ratio')}" for p in bloat[:5]) +
            ". Extractors weight text density; a heavy document with sparse text is cheap to skip.",
            "Reduce inline scripts and markup bloat so the text-to-code ratio rises.",
            "Faster fetches and a higher signal-to-noise ratio for extraction.",
            steps=["Move large inline JSON/JS payloads to external, deferred files.",
                   "Remove unused markup and duplicated template blocks.",
                   "Aim for the main content to be a visible share of the document, not a rounding error."],
            tags=("rendering", "performance")))

    if not rendered:
        notes.append("No rendered/ capture in the pack: JavaScript-dependence findings are inferred from "
                     "served HTML and framework signatures, and are marked for browser verification.")
    notes.append(f"Extractability checks evaluated {n} parsed pages ({len(content)} content pages).")
    return out, notes


def main():
    ap = argparse.ArgumentParser(description="Render/extractability analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages, rendered = load_pack(args.pack)
    findings, notes = analyze(meta, pages, rendered)
    emit(findings, notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
