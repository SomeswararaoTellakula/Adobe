#!/usr/bin/env python3
"""
check_access.py - stage 1 of the audit: can a machine reach the page at all?

Offline analyzer. Reads a site pack produced by collect_site_pack.py and emits findings
about the access layer: robots.txt gating of AI agents, snippet-suppressing directives,
bot walls, sitemap health, canonical/host consistency, and response latency.

  python3 check_access.py --pack ./pack [--out findings.json]

Design notes that keep false positives down:
  * Blocking a TRAINING crawler is treated as a policy observation, not a defect. Blocking
    a RETRIEVAL or USER-TRIGGERED fetcher is treated as a real discoverability failure,
    because that is what removes a brand from live answers.
  * Every finding carries counts and sample size, so a reader can judge it independently.
"""

import argparse
import json
import os
import statistics
import sys
import urllib.parse

SKILL = "crawl-access-audit"
DIMENSION = "discoverability.access"


def base_host(host):
    """Strip a leading 'www.' label so host variants of one site compare equal."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def registrable(host):
    """Best-effort registrable domain: the last two labels of the host.

    Good enough to tell 'www.example.com' and 'docs.example.com' (same site) apart from
    'someone-else.com' (a different site), without shipping a public-suffix list.
    """
    labels = base_host(host).split(":")[0].split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else base_host(host)


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
    robots = meta.get("robots", {})
    probes = meta.get("probes", {})
    origin = meta.get("origin", "")
    # A total bot wall makes most access checks unanswerable. If the WAF returns 403 to our
    # client for everything, we cannot know whether a sitemap exists, whether robots.txt is
    # well-formed, or whether any link is broken - we only know we were refused. Emitting
    # "no sitemap" and "linked pages return errors" in that state blames the site for
    # consequences of the block, and the "broken link" is usually the homepage itself.
    _home = meta.get("probes", {}).get("home", {})
    wall_blocked = bool(_home.get("status") in (401, 403, 429)
                        or _home.get("challenge_markers") or _home.get("cf_mitigated"))
    nothing_parsed = not [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    unmeasurable = wall_blocked and nothing_parsed
    verdicts = robots.get("agent_verdicts", {})
    ok_pages = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    n = len(ok_pages)

    if not origin:
        # A pack with no origin was never collected. Reporting checks against absent data
        # would fabricate findings, so report nothing and say why.
        notes.append("Pack contains no collection metadata; no access checks could be evaluated.")
        return out, notes

    # -- ACC-001/002/003: robots.txt gating by agent role ----------------------
    blocked = {"retrieval": [], "user_fetch": [], "training": []}
    for token, v in sorted(verdicts.items()):
        if not v.get("root_allowed"):
            blocked[v["role"]].append(v["label"])

    if blocked["user_fetch"]:
        out.append(finding(
            "ACC-001", "robots.txt blocks user-triggered AI fetchers", "critical", "high",
            [urllib.parse.urljoin(origin, "/robots.txt")],
            "robots.txt disallows / for: " + "; ".join(blocked["user_fetch"]) +
            ". These agents fetch a page only when a user has already asked about you, so the block "
            "removes the brand from answers it would otherwise have won.",
            "Allow user-triggered AI fetchers in robots.txt while keeping any training opt-out you want.",
            "The brand becomes eligible to be fetched and cited at answer time.",
            steps=["Open robots.txt.",
                   "Remove the blanket 'Disallow: /' from the groups named above (ChatGPT-User, Claude-User, "
                   "Perplexity-User and equivalents).",
                   "If the intent was to opt out of model training only, keep those blocks on the training "
                   "crawlers (GPTBot, ClaudeBot, CCBot, Google-Extended) and leave the fetchers allowed.",
                   "Re-fetch /robots.txt and confirm the retrieval groups no longer disallow /."],
            tags=("robots", "ai-agents")))

    if blocked["retrieval"]:
        # Severity tracks WHICH retrieval surfaces are lost, not how many. Blocking the
        # general-purpose search indexes is categorically worse than blocking a niche
        # answer engine, and rating "Amazonbot only" the same as "OAI-SearchBot +
        # PerplexityBot + Claude-SearchBot" misleads whoever triages the report.
        GENERAL_INDEX = ("Googlebot", "Bingbot")
        MAJOR_AI = ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot", "Applebot", "DuckAssistBot")
        if any(x.startswith(GENERAL_INDEX) for x in blocked["retrieval"]):
            severe = "critical"
        elif any(x.startswith(MAJOR_AI) for x in blocked["retrieval"]):
            severe = "high"
        else:
            severe = "medium"   # only niche surfaces (Amazonbot, YouBot) are affected
        out.append(finding(
            "ACC-002", "robots.txt blocks AI/search retrieval crawlers", severe, "high",
            [urllib.parse.urljoin(origin, "/robots.txt")],
            "robots.txt disallows / for: " + "; ".join(blocked["retrieval"]) +
            ". These build the indexes assistants search before they answer."
            + (" This includes a general-purpose search index, which most assistants ground on."
               if severe == "critical" else
               " No general-purpose search index is affected." if severe == "high" else
               " Only niche answer surfaces are affected, so the practical loss is small - "
               "recorded for completeness rather than as an urgent fix."),
            "Unblock the retrieval crawlers that feed AI answer surfaces.",
            "Pages become indexable by the surfaces that assistants query, which is a precondition for citation.",
            steps=["Remove 'Disallow: /' for the retrieval groups listed in the evidence.",
                   "Keep narrow disallows for genuinely private paths instead of site-wide blocks.",
                   "Confirm with a robots tester that / is allowed for each group."],
            tags=("robots", "ai-agents")))

    if blocked["training"]:
        out.append(finding(
            "ACC-003", "Training crawlers are blocked (policy observation, not a defect)", "info", "high",
            [urllib.parse.urljoin(origin, "/robots.txt")],
            "robots.txt disallows / for: " + "; ".join(blocked["training"]) +
            ". This is a legitimate choice; it is recorded so the tradeoff is explicit.",
            "Decide this deliberately: keep the block if training opt-out is the policy, but make sure it "
            "is not accidentally applied to retrieval and user-triggered agents.",
            "Clarity about which half of the AI ecosystem the brand has opted out of.",
            steps=["Confirm the block is intentional with whoever owns brand policy.",
                   "Check no retrieval agent shares the same group by mistake.",
                   "Note that blocking training crawlers reduces the odds of being described correctly "
                   "from model memory, while allowing retrieval still permits live citation."],
            tags=("robots", "policy")))

    star = robots.get("groups", {}).get("*", {})
    if "/" in star.get("disallow", []):
        out.append(finding(
            "ACC-004", "robots.txt disallows the whole site for all agents", "critical", "high",
            [urllib.parse.urljoin(origin, "/robots.txt")],
            "The 'User-agent: *' group contains 'Disallow: /', which blocks every compliant crawler.",
            "Remove the site-wide disallow and replace it with narrow rules for private paths.",
            "Restores baseline crawlability for search and AI systems alike.",
            steps=["Replace 'Disallow: /' with specific paths (e.g. /admin/, /cart/).",
                   "Verify staging rules were not copied to production - this is the usual cause."],
            tags=("robots",)))

    _robots_status = robots.get("status")
    if _robots_status not in (200, 404) and unmeasurable and _robots_status in (401, 403, 429):
        # The wall refused robots.txt for the same reason it refused everything else.
        # ACC-007 already names that cause and carries the correct remedy (allowlist the
        # fetcher at the WAF). A second finding here would restate one problem as two and
        # attach advice - "return 200 text/plain", "never return 5xx" - that does not apply.
        notes.append("robots.txt check folded into the bot-wall finding: the 403 was the WAF refusing "
                     "this client, not a robots.txt fault, and the fix is the same one.")
    elif robots.get("status") not in (200, 404):
        # Status 0 is not a server response - it means our own request failed (DNS, TLS,
        # timeout, refused connection). Reporting that as a server misconfiguration blames
        # the site for a condition it may have nothing to do with.
        network_failure = robots.get("status") == 0
        wall_status = robots.get("status") in (401, 403, 429)
        out.append(finding(
            "ACC-005",
            ("robots.txt could not be fetched from this network" if network_failure
             else "robots.txt is refused for non-browser clients" if wall_status
             else "robots.txt is unreachable or misconfigured"),
            "medium", "low" if network_failure else "high",
            [urllib.parse.urljoin(origin, "/robots.txt")],
            ("The request for /robots.txt returned no HTTP response at all (connection, DNS or TLS "
             "failure). That is a fact about this fetch, not proof of a server fault - it may be a "
             "transient network problem or a filter on this client. Confirm from another network "
             "before acting. If it is real, some crawlers treat an unreadable robots.txt as "
             "'crawl nothing', which would suppress the whole site."
             if network_failure else
             f"/robots.txt returned HTTP {robots.get('status')} for this client. That is the bot wall "
             "refusing the request, not a malformed robots.txt - but the effect on a compliant fetcher is "
             "the same, and some crawlers treat an unreadable robots.txt as 'crawl nothing'."
             if wall_status else
             f"/robots.txt returned HTTP {robots.get('status')}. Some crawlers treat a 5xx on robots.txt as "
             "'crawl nothing', so an error here can suppress the whole site."),
            ("Stop challenging robots.txt at the WAF - serve it to every client."
             if wall_status else
             "Serve robots.txt as plain text with HTTP 200 (or 404 if you have no rules)."),
            "Removes an ambiguous signal that can suppress crawling entirely.",
            steps=(["Fetch https://<domain>/robots.txt in a browser and from another network to "
                    "confirm whether the failure is real.",
                    "Never return 5xx for robots.txt.", "Declare the sitemap in it."]
                   if network_failure else
                   ["Exempt /robots.txt from bot-management rules - it exists precisely so that "
                    "non-browser clients can read it, so challenging it is self-defeating.",
                    "Confirm with `curl -sI https://<domain>/robots.txt` that a plain client gets 200.",
                    "Declare the sitemap in it."]
                   if wall_status else
                   ["Return 200 with content-type text/plain, or a clean 404.",
                    "Never return 5xx for robots.txt.", "Declare the sitemap in it."]),
            tags=("robots",)))

    # When collection was aborted, every later check is unevaluated. Reporting "no sitemap
    # found" after never looking would be a fabricated finding, so stop here instead.
    if meta.get("aborted"):
        notes.append("Crawl aborted before collection: robots.txt disallows this auditor. Only "
                     "robots-level checks were evaluated; sitemap, canonical, latency, error and "
                     "content checks were not run and are not reported either way.")
        return out, notes

    # -- ACC-006: snippet suppression -----------------------------------------
    bad_directives = ("noindex", "nosnippet", "max-snippet:0", "noarchive", "none", "noai", "noimageai")
    suppressed = {}
    for p in ok_pages:
        text = " ".join([p.get("meta_robots", ""), p.get("headers", {}).get("x-robots-tag", "")]).lower()
        hits = [d for d in bad_directives if d in text]
        if hits:
            suppressed[p["url"]] = hits
    if suppressed:
        flat = sorted({h for hits in suppressed.values() for h in hits})
        sev = "critical" if any(h in ("noindex", "none") for h in flat) else "high"
        out.append(finding(
            "ACC-006", "Pages carry directives that suppress indexing or quoting", sev, "high",
            suppressed.keys(),
            f"{len(suppressed)}/{n} sampled pages carry {', '.join(flat)} via meta robots or X-Robots-Tag. "
            "nosnippet / max-snippet:0 specifically forbid reproducing text, which is exactly what an "
            "assistant needs to quote you.",
            "Remove indexing- and snippet-suppressing directives from pages you want cited.",
            "Allows the content to be indexed and quoted in answers.",
            steps=["Audit the meta robots tag and X-Robots-Tag header on the affected pages.",
                   "Drop noindex/none from public pages; drop nosnippet and max-snippet:0 everywhere you "
                   "want to be quotable.",
                   "If snippet length was being limited for a legal reason, prefer max-snippet:<n> with a "
                   "generous n over 0."],
            scales=True, tags=("directives", "snippet")))

    # -- ACC-007: bot wall ----------------------------------------------------
    home = probes.get("home", {})
    # A refused status is a wall on its own. A 200 is only a wall with strong evidence:
    # the CDN's own mitigation header, or challenge text on a page that carries almost no
    # content. A 200 full of article text is not an interstitial, whatever words it
    # contains - matching markers alone reported an encyclopedia as running a bot wall.
    _refused = home.get("status") in (401, 403, 429)
    _interstitial = (home.get("challenge_markers") and home.get("body_words", 0) < 150)
    if _refused or _interstitial or home.get("cf_mitigated"):
        out.append(finding(
            "ACC-007", "Plain HTTP clients are challenged or refused (bot wall)",
            "critical" if nothing_parsed else "high", "medium",
            [origin],
            f"Homepage fetched with a declared audit user-agent returned HTTP {home.get('status')}"
            + (", with an interstitial/challenge page in the body" if home.get("challenge_markers") else "")
            + (f", cf-mitigated={home.get('cf_mitigated')}" if home.get("cf_mitigated") else "")
            + (f" The page carried only {home.get('body_words', 0)} words alongside challenge markup, "
               "which is what an interstitial looks like." if _interstitial and not _refused else "")
            + " Assistant fetchers are plain HTTP clients too - they do not solve challenges."
            + (" No page in the sample could be read, so every AI surface currently receives nothing from "
               "this domain - and the rest of this audit could not be performed." if nothing_parsed else ""),
            "Allowlist compliant AI fetchers at the WAF/CDN layer instead of challenging all non-browser clients.",
            "Lets answer-time fetchers retrieve the page instead of receiving a challenge.",
            steps=["In the CDN/WAF bot-management rules, add a verified-bot allowlist covering search and "
                   "AI fetchers, validated by reverse DNS or published IP ranges rather than user-agent string alone.",
                   "Keep rate limiting, but return 200 with content rather than a JS challenge.",
                   "Re-test with a non-browser client (curl) and confirm HTML comes back."],
            verify="Fetch the same URL from a browser: if a human sees content where a plain client is "
                   "challenged, the bot wall is confirmed.",
            tags=("waf", "bot-management")))

    # -- ACC-008/009: sitemaps ------------------------------------------------
    sitemaps = meta.get("sitemaps", [])
    good = [s for s in sitemaps if s.get("status") == 200 and s.get("entries", 0) > 0]
    if not good and unmeasurable:
        notes.append("Sitemap check skipped: every request was refused by the bot wall, so whether a "
                     "sitemap exists could not be determined. Re-run from an allowlisted client.")
    elif not good:
        out.append(finding(
            "ACC-008", "No usable XML sitemap found", "medium", "high",
            [urllib.parse.urljoin(origin, "/sitemap.xml")],
            "Checked robots.txt Sitemap directives plus /sitemap.xml and /sitemap_index.xml; none returned a "
            "parseable urlset. Crawlers then depend entirely on internal links to find pages.",
            "Publish an XML sitemap and declare it in robots.txt.",
            "Gives crawlers a complete, machine-readable inventory of pages worth fetching.",
            steps=["Generate a sitemap covering canonical URLs only (no redirects, no noindex pages).",
                   "Include <lastmod> with real modification dates.",
                   "Add 'Sitemap: https://<domain>/sitemap.xml' to robots.txt."],
            tags=("sitemap",)))
    elif not any(s.get("declared_in_robots") for s in good):
        out.append(finding(
            "ACC-009", "Sitemap exists but is not declared in robots.txt", "low", "high",
            [s["url"] for s in good],
            f"Found {len(good)} sitemap(s) by convention, but robots.txt declares none.",
            "Add a Sitemap: line to robots.txt.",
            "Crawlers that do not guess conventional paths still find the inventory.",
            steps=["Append 'Sitemap: <absolute URL>' to robots.txt for each sitemap or sitemap index."],
            tags=("sitemap",)))

    # -- ACC-010: host / scheme canonicalization ------------------------------
    variant = probes.get("host_variant", {})
    if variant.get("status") == 200 and not variant.get("canonicalizes"):
        # A rel=canonical across host variants already mitigates most of this. Say so:
        # otherwise the finding reads as urgent when the site has in fact handled the
        # hard part, and the reader cannot tell this apart from a site doing nothing.
        canonical_present = any(p.get("canonical") for p in ok_pages)
        mitigation = (" The pages do declare rel=canonical, which mitigates most of the duplication risk, so "
                      "this is a hardening step rather than an active fault." if canonical_present else
                      " No rel=canonical was found either, so nothing currently tells crawlers which host wins.")
        out.append(finding(
            "ACC-010", "Both www and apex hosts serve content without redirecting",
            "low" if canonical_present else "medium", "high",
            [origin, f"{urllib.parse.urlsplit(origin).scheme}://{variant.get('host')}/"],
            f"{variant.get('host')} returned HTTP 200 and did not redirect to {origin}. Duplicate hosts split "
            "link signals and let assistants cite an inconsistent URL for the same content." + mitigation,
            "Pick one canonical host and 301 the other to it.",
            "Consolidates authority and makes citations point at one stable URL.",
            steps=["Choose the canonical host (www or apex).",
                   "Add a permanent 301 redirect from the other host.",
                   "Ensure rel=canonical on every page points at the chosen host."],
            tags=("canonical", "duplication")))

    scheme = probes.get("http_scheme", {})
    if scheme and scheme.get("status") == 200 and not scheme.get("upgrades_to_https"):
        out.append(finding(
            "ACC-011", "Plain HTTP is served without upgrading to HTTPS", "medium", "high",
            [origin],
            "http:// returned 200 without redirecting to https://. Mixed-scheme duplicates dilute signals and "
            "some fetchers refuse insecure URLs.",
            "Redirect all HTTP traffic to HTTPS with a 301 and enable HSTS.",
            "One secure canonical origin for every page.",
            steps=["Add a site-wide 301 from http:// to https://.", "Serve HSTS.",
                   "Update internal links and the sitemap to https:// URLs."],
            tags=("https", "duplication")))

    # -- ACC-012: canonical tags ----------------------------------------------
    missing_canonical = [p["url"] for p in ok_pages if not p.get("canonical")]
    if n >= 4 and len(missing_canonical) >= max(2, int(0.5 * n)):
        out.append(finding(
            "ACC-012", "Most pages have no rel=canonical", "low", "high", missing_canonical,
            f"{len(missing_canonical)}/{n} sampled pages declare no canonical URL.",
            "Add a self-referencing rel=canonical to every indexable page.",
            "Stops parameterized and duplicated URLs from competing with the page you want cited.",
            steps=["Emit <link rel=\"canonical\" href=\"<absolute self URL>\"> in <head> on every page.",
                   "Use absolute URLs on the canonical host."],
            scales=True, tags=("canonical",)))

    # Only a canonical pointing at a DIFFERENT SITE is a defect. Pointing apex -> www (or
    # any host variant / sibling subdomain of the same registrable domain) is the standard,
    # recommended way to consolidate duplicate hosts. Comparing raw netloc treated
    # "example.com -> www.example.com" as handing the page to a stranger, which inverted
    # the advice: it told sites doing this correctly to undo it.
    offhost, samesite_variant = [], []
    for p in ok_pages:
        c = p.get("canonical", "")
        if not (c and c.startswith("http")):
            continue
        c_host = urllib.parse.urlsplit(c).netloc
        p_host = urllib.parse.urlsplit(p["url"]).netloc
        if registrable(c_host) != registrable(p_host):
            offhost.append(p["url"])
        elif base_host(c_host) != base_host(p_host):
            samesite_variant.append(p["url"])
    if samesite_variant:
        notes.append(f"{len(samesite_variant)}/{n} page(s) canonicalize to a different host of the same site "
                     "(e.g. apex to www). That is normal consolidation and is not reported as a defect.")
    if offhost:
        out.append(finding(
            "ACC-013", "Pages canonicalize to a different site", "high", "high", offhost,
            f"{len(offhost)}/{n} sampled pages point rel=canonical at a different registrable domain, telling "
            "crawlers to index someone else's URL instead of this one.",
            "Correct the canonical to the page's own absolute URL unless syndication is intentional.",
            "The brand's own URL becomes the citable one.",
            steps=["Check the templating layer for a hard-coded canonical host (a frequent staging leak).",
                   "Point each canonical at the page's own https URL on the canonical host."],
            tags=("canonical",)))

    # -- ACC-014: broken pages / soft 404 -------------------------------------
    broken = [p["url"] for p in pages if p.get("status", 0) >= 400 or p.get("status") == 0]
    if broken and unmeasurable:
        notes.append("Broken-link check skipped: the error responses were the bot wall refusing this "
                     "client, not pages that are genuinely missing.")
    elif broken:
        out.append(finding(
            "ACC-014", "Linked pages return errors", "medium", "high", broken,
            f"{len(broken)}/{len(pages)} sampled URLs (discovered from internal links or the sitemap) returned "
            "an error or failed to load.",
            "Fix or remove the broken URLs and update whatever links to them.",
            "Crawl budget stops being spent on dead ends and link equity stops leaking.",
            steps=["Fix the underlying pages, or 301 them to the closest live equivalent.",
                   "Remove dead URLs from the sitemap and from internal navigation."],
            tags=("errors",)))

    nf = probes.get("not_found", {})
    if nf.get("soft_404"):
        out.append(finding(
            "ACC-015", "Missing pages return HTTP 200 (soft 404)", "medium", "high",
            [urllib.parse.urljoin(origin, "/brand-ai-readiness-audit-probe-404")],
            "A URL that should not exist returned HTTP 200. Crawlers then index unlimited phantom pages and "
            "dilute the real ones.",
            "Return a real 404 (or 410) status for missing URLs.",
            "Crawlers stop indexing non-existent pages.",
            steps=["Configure the router/CMS to send a 404 status with the not-found page.",
                   "Keep the friendly design - only the status code needs to change."],
            tags=("errors",)))

    # -- ACC-018: navigation wired to nothing ---------------------------------
    # ACC-014 reports links that return an error. This reports links that were never
    # requests at all - javascript:void(0) or a bare "#" in place of an href. The effect is
    # worse than a broken link: a broken link is at least discovered and reported as dead,
    # whereas these destinations are invisible to every crawler and never enter the index.
    dead_total = sum(p.get("dead_links", 0) for p in ok_pages)
    dead_pages = [p for p in ok_pages if p.get("dead_links", 0) >= 3]
    if dead_pages and dead_total >= 5:
        labels = []
        for p in dead_pages:
            labels.extend(p.get("dead_link_labels", []))
        shown = ", ".join(f"'{l}'" for l in sorted(set(labels))[:8])
        out.append(finding(
            "ACC-018", "Navigation links point at nothing a crawler can follow", "high", "high",
            [p["url"] for p in dead_pages],
            f"{dead_total} link(s) across {len(dead_pages)}/{n} sampled page(s) use javascript:void(0), a bare "
            f"'#', or an empty href instead of a URL" + (f" - including {shown}" if shown else "") +
            ". A fetcher cannot follow these, so whatever they lead to is not discoverable at all. This is "
            "why the crawlable sample may be far smaller than the site.",
            "Give every navigation item a real href, and handle any JavaScript behaviour on top of it.",
            "The pages behind the menu become reachable, indexable and citable.",
            steps=["Replace each javascript:void(0) or href=\"#\" with the destination URL.",
                   "Where a click runs script, keep the real href and call preventDefault() in the handler - "
                   "the link still works without JavaScript and a crawler can still follow it.",
                   "For a menu that genuinely has no destination yet, either build the page or remove the item; "
                   "a menu entry that goes nowhere costs a visitor a click and a crawler a dead end.",
                   "Re-run this audit afterwards and confirm the discovered-URL count rises."],
            verify="View source and search for javascript:void or href=\"#\" in the navigation.",
            tags=("discovery", "crawl-path", "navigation")))

    # -- ACC-017: thin crawlable link graph -----------------------------------
    # If a crawler starting at the homepage can barely reach anything, that is a
    # discoverability failure in its own right - not merely a small sample. It usually
    # means navigation is rendered client-side, so the served HTML contains no <a href>
    # for a fetcher to follow, and no sitemap exists to compensate.
    good_sitemaps = [sm for sm in meta.get("sitemaps", []) if sm.get("entries", 0) > 0]
    discovered = meta.get("discovered", 0)
    home_links = 0
    for p in ok_pages:
        if p.get("page_class") == "home":
            home_links = p.get("links_internal", 0)
    if discovered <= 3 and not good_sitemaps and probes.get("home", {}).get("status") == 200:
        out.append(finding(
            "ACC-017", "Almost no internal pages are reachable by following links", "high", "medium",
            [origin],
            f"Starting from the homepage and following links in the served HTML, only {discovered} URL(s) "
            f"were discoverable on this domain (the homepage itself declared {home_links} internal link(s)), "
            "and no usable XML sitemap exists. A crawler has no route to the rest of the site, so however "
            "much content exists, only what was reachable can be indexed or cited.",
            "Publish an XML sitemap and make sure navigation links are real <a href> elements in the served HTML.",
            "Gives crawlers a route to the whole site instead of a dead end at the homepage.",
            steps=["Generate an XML sitemap listing every canonical URL and declare it in robots.txt.",
                   "Check the homepage with `curl -s <url> | grep -o '<a [^>]*href=\"[^\"]*\"' | head -50` - if "
                   "navigation links are missing, they are being rendered by JavaScript and no fetcher can follow them.",
                   "Render primary navigation and category/index links server-side as ordinary anchor elements.",
                   "Add HTML index or category pages that link to deeper content, so there is a crawlable path "
                   "to every page."],
            verify="Open the homepage's view-source and confirm whether the navigation links are present as "
                   "<a href> elements or injected by script.",
            tags=("discovery", "crawl-path")))

    # -- ACC-016: latency -----------------------------------------------------
    times = [p["elapsed_ms"] for p in ok_pages if p.get("elapsed_ms")]
    if times:
        median = statistics.median(times)
        slow = [p["url"] for p in ok_pages if p.get("elapsed_ms", 0) > 5000]
        if median > 2500 or slow:
            out.append(finding(
                "ACC-016", "Server responses are slow enough to risk fetcher timeouts", "medium", "high",
                slow or [p["url"] for p in ok_pages],
                f"Median response {int(median)} ms across {len(times)} sampled pages; "
                f"{len(slow)} page(s) exceeded 5000 ms. Answer-time fetchers use short timeouts and simply "
                "drop slow sources.",
                "Cut time-to-first-byte for HTML documents, prioritising uncached first views.",
                "The page returns inside the window a fetcher is willing to wait.",
                steps=["Serve HTML from a CDN edge cache with a sensible TTL.",
                       "Profile and fix slow server-side rendering or database calls on the slowest templates.",
                       "Target under 800 ms TTFB for HTML."],
                tags=("performance",)))

    if unmeasurable:
        # robots.txt is frequently exempted from WAF rules, so it often IS readable while
        # every page is refused. Claiming "only the block was observable" in that case
        # understates the evidence and undercuts the robots findings, which are sound.
        robots_readable = robots.get("status") in (200, 404)
        notes.insert(0,
            ("THE SITE REFUSED THIS CLIENT for every page. robots.txt was readable, so the robots-based "
             "findings above are evidence-backed and stand on their own; everything that needed a "
             "successful page fetch was skipped rather than guessed."
             if robots_readable else
             "THE SITE REFUSED THIS CLIENT. Only the block itself was observable; checks that need a "
             "successful fetch were skipped rather than guessed. Nothing below should be read as "
             "evidence about the site's own configuration."))
    notes.append(f"Access checks evaluated against {n} successfully parsed pages "
                 f"({len(pages)} URLs fetched, {meta.get('discovered', 0)} discovered).")
    return out, notes


def main():
    ap = argparse.ArgumentParser(description="Access-layer analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages = load_pack(args.pack)
    findings, notes = analyze(meta, pages)
    emit(findings, notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
