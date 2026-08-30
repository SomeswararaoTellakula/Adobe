#!/usr/bin/env python3
"""
Brand AI-Readiness Audit Engine
================================
Production-grade, optimized Python engine for auditing website
AI-discoverability and on-site-engagement.

Design goals:
  - Read-only, sandbox-safe (no writes, no auth, no mutation).
  - Efficient: connection pooling, HTTP caching headers, parallel-safe.
  - Deterministic: same input => same output (no randomness, no external API keys).
  - Composable: each audit dimension returns a list of Finding dicts.
  - Portable: only standard library + requests + beautifulsoup4 (lxml parser).
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants & types
# ---------------------------------------------------------------------------

USER_AGENT = (
    "BrandAIReadinessAudit/1.0 (+https://agentskills.io; "
    "respectful read-only auditor; contact: audit@agentskills.io)"
)

SEVERITIES = ("critical", "high", "medium", "low", "info")
PRIORITIES = ("critical", "high", "medium", "low")

SCHEMA_ORG_TYPES_EXPECTED = {
    "Organization",
    "WebSite",
    "BreadcrumbList",
    "Product",
    "Article",
    "BlogPosting",
    "FAQPage",
    "SearchAction",
}

OG_REQUIRED = {"og:title", "og:type", "og:image", "og:url"}
TWITTER_REQUIRED = {"twitter:card", "twitter:title", "twitter:description", "twitter:image"}

HTTP_TIMEOUT = 15
MAX_PAGES_CRAWLED = 20
MAX_INTERNAL_LINKS = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _absolute(base: str, href: str | None) -> str | None:
    if not href:
        return None
    joined = urljoin(base, href.strip())
    clean, _ = urldefrag(joined)
    return clean


def _same_domain(url_a: str, url_b: str) -> bool:
    try:
        return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()
    except Exception:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_ratio(soup: BeautifulSoup) -> float:
    text = soup.get_text(" ", strip=True)
    html_len = max(1, len(str(soup)))
    return round(min(1.0, len(text) / html_len), 4)


def _make_finding(
    finding_id: str,
    title: str,
    severity: str,
    evidence: str,
    action_summary: str,
    action_priority: str,
) -> dict[str, Any]:
    assert severity in SEVERITIES, f"invalid severity: {severity}"
    assert action_priority in PRIORITIES, f"invalid priority: {action_priority}"
    return {
        "id": finding_id,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "suggested_action": {
            "summary": action_summary,
            "priority": action_priority,
        },
    }


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


class AuditorSession:
    """Thin wrapper over requests.Session with pooling, retries, and polite defaults."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: int = HTTP_TIMEOUT) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        self.timeout = timeout
        self.robot_parsers: dict[str, RobotFileParser] = {}
        self._cache: dict[str, requests.Response | None] = {}

    # --- robots.txt --------------------------------------------------------

    def _rp(self, url: str) -> RobotFileParser:
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if base not in self.robot_parsers:
            rp = RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                rp.parse([""])  # empty => allow all
            self.robot_parsers[base] = rp
        return self.robot_parsers[base]

    def can_fetch(self, url: str) -> bool:
        try:
            return self._rp(url).can_fetch(USER_AGENT.split("/")[0], url)
        except Exception:
            return True

    # --- fetch -------------------------------------------------------------

    def fetch(self, url: str, method: str = "GET", allow_redirects: bool = True) -> requests.Response | None:
        """Fetch URL once; cache by (method, url). Returns None on unrecoverable failure."""
        key = (method, url)
        if key in self._cache:
            return self._cache[key]
        last_exc: Exception | None = None
        for delay in (0, 1, 2):  # retry with mild backoff
            if delay:
                time.sleep(delay)
            try:
                resp = self.session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    allow_redirects=allow_redirects,
                    stream=False,
                )
                self._cache[key] = resp
                return resp
            except (requests.RequestException, OSError) as e:
                last_exc = e
                continue
        # Store failure so we don't try again
        self._cache[key] = None
        return None

    def fetch_html(self, url: str) -> tuple[requests.Response | None, BeautifulSoup | None]:
        resp = self.fetch(url)
        if resp is None:
            return None, None
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "xml" not in ctype.lower():
            # Be permissive: still try to parse; many servers mislabel.
            pass
        try:
            text = resp.text
        except Exception:
            try:
                text = resp.content.decode("utf-8", errors="replace")
            except Exception:
                return resp, None
        try:
            soup = BeautifulSoup(text, "lxml")
        except Exception:
            soup = BeautifulSoup(text, "html.parser")
        return resp, soup

    def close(self) -> None:
        self.session.close()


# ---------------------------------------------------------------------------
# Individual audit modules (each returns list[Finding])
# ---------------------------------------------------------------------------


class AuditEngine:
    """Collects every audit check. Call `run_all(url)` to produce the full report."""

    def __init__(self, session: AuditorSession | None = None) -> None:
        self.session = session or AuditorSession()
        self._finding_counter = 0

    # ---- finding id generator -------------------------------------------
    def _fid(self, prefix: str) -> str:
        self._finding_counter += 1
        return f"{prefix}-{self._finding_counter:03d}"

    # =====================================================================
    # 1. Crawlability & Renderability
    # =====================================================================

    def audit_crawlability(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []

        # robots.txt reachable and permissive?
        rp = self.session._rp(homepage)
        robots_url = urljoin(homepage, "/robots.txt")
        robots_resp = self.session.fetch(robots_url)
        if robots_resp is None or robots_resp.status_code >= 400:
            out.append(_make_finding(
                self._fid("CR"),
                "robots.txt is unreachable or missing",
                "medium",
                f"GET {robots_url} -> status={robots_resp.status_code if robots_resp is not None else 'connection-failed'}; "
                "crawlers cannot learn which paths are allowed/disallowed.",
                "Publish a valid robots.txt at the site root. Use `User-agent: *` with explicit `Allow: /` to opt in, "
                "plus targeted `Disallow:` rules for private paths.",
                "high",
            ))
        else:
            disallow_all = False
            try:
                entries = rp.entries
                for entry in entries or []:
                    rulelines = entry.rulelines if hasattr(entry, "rulelines") else []
                    for rl in rulelines:
                        if hasattr(rl, "path") and rl.path == "/" and not rl.allowance:
                            disallow_all = True
            except Exception:
                pass
            if disallow_all:
                out.append(_make_finding(
                    self._fid("CR"),
                    "robots.txt blocks all crawlers",
                    "critical",
                    f"robots.txt at {robots_url} contains a rule that disallows `/` for broad crawlers; "
                    "this will suppress indexing and AI discovery entirely.",
                    "Remove the blanket `Disallow: /` from the wildcard `User-agent: *` block. "
                    "Block only genuinely private paths.",
                    "critical",
                ))
            if not self.session.can_fetch(homepage):
                out.append(_make_finding(
                    self._fid("CR"),
                    "Homepage URL is blocked by robots.txt",
                    "critical",
                    f"RobotFileParser.can_fetch('{homepage}') returned False under User-Agent {USER_AGENT.split('/')[0]}.",
                    "Update robots.txt to Allow the homepage and other public content paths.",
                    "critical",
                ))

        # sitemap.xml reachable & parseable?
        sitemap_candidates = [
            urljoin(homepage, "/sitemap.xml"),
            urljoin(homepage, "/sitemap_index.xml"),
        ]
        found_sitemap = False
        for sm_url in sitemap_candidates:
            sm_resp = self.session.fetch(sm_url)
            if sm_resp is not None and sm_resp.status_code < 400 and b"urlset" in sm_resp.content[:16384].lower():
                found_sitemap = True
                # Try to glean size
                urls = re.findall(rb"<loc>([^<]+)</loc>", sm_resp.content)
                if len(urls) < 2:
                    out.append(_make_finding(
                        self._fid("CR"),
                        "Sitemap has very few URLs or is nearly empty",
                        "medium",
                        f"{sm_url} parses but contains only {len(urls)} <loc> entries.",
                        "Ensure sitemap.xml includes all canonical public URLs (products, articles, categories). "
                        "Submit sitemaps via Google Search Console and Bing Webmaster Tools.",
                        "medium",
                    ))
                break
        if not found_sitemap and resp is not None:
            # Try robots.txt Sitemap: directive
            try:
                sm_lines = [l for l in rp.site_maps() or []] if hasattr(rp, "site_maps") else []
                if not sm_lines:
                    robots_text = robots_resp.text if robots_resp and robots_resp.status_code < 400 else ""
                    sm_lines = re.findall(r"(?im)^Sitemap:\s*(\S+)", robots_text)
                for sm in sm_lines:
                    r = self.session.fetch(sm)
                    if r and r.status_code < 400 and b"urlset" in r.content[:16384].lower():
                        found_sitemap = True
                        break
            except Exception:
                pass
        if not found_sitemap:
            out.append(_make_finding(
                self._fid("CR"),
                "No sitemap.xml is exposed",
                "high",
                "Tried /sitemap.xml, /sitemap_index.xml, and robots.txt Sitemap directives — none resolve to a parseable urlset sitemap.",
                "Generate a sitemap.xml containing every canonical public URL; serve it from the site root and "
                "declare its location in robots.txt with a `Sitemap:` directive.",
                "high",
            ))

        # meta robots on homepage
        if soup is not None:
            meta_robots = soup.find("meta", attrs={"name": re.compile(r"(?i)^robots$")})
            if meta_robots and meta_robots.get("content"):
                content = meta_robots["content"].lower()
                blocked = any(tok in content for tok in ("noindex", "none", "noarchive", "nosnippet"))
                if blocked:
                    out.append(_make_finding(
                        self._fid("CR"),
                        "Homepage meta robots blocks indexing/snippets",
                        "critical",
                        f"<meta name='robots' content='{content}'> on homepage. `noindex`/`none` suppress discovery.",
                        "Change meta robots to `index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1`.",
                        "critical",
                    ))
                if "nofollow" in content:
                    out.append(_make_finding(
                        self._fid("CR"),
                        "Homepage meta robots uses nofollow",
                        "high",
                        f"meta robots='{content}' contains nofollow — internal PageRank won't flow.",
                        "Drop `nofollow` from homepage meta robots; use rel=nofollow on specific outbound links only.",
                        "high",
                    ))

        # X-Robots-Tag header
        if resp is not None:
            xrobots = resp.headers.get("X-Robots-Tag", "")
            if xrobots:
                low = xrobots.lower()
                if any(tok in low for tok in ("noindex", "none")):
                    out.append(_make_finding(
                        self._fid("CR"),
                        "X-Robots-Tag blocks indexing",
                        "critical",
                        f"Response header X-Robots-Tag: {xrobots}",
                        "Remove noindex/none from the X-Robots-Tag header on public responses; apply selectively only to private paths.",
                        "critical",
                    ))

        # HTTP status
        if resp is None:
            out.append(_make_finding(
                self._fid("CR"),
                "Homepage is unreachable over HTTP(S)",
                "critical",
                f"GET {homepage} failed after retries (connection/timeout error).",
                "Confirm DNS resolves, TLS is valid, origin is reachable, and CDN/WAF rules allow well-known crawler user agents.",
                "critical",
            ))
        else:
            if resp.status_code >= 400:
                out.append(_make_finding(
                    self._fid("CR"),
                    f"Homepage returns HTTP {resp.status_code}",
                    "critical",
                    f"GET {homepage} -> status {resp.status_code}.",
                    "Return 200 OK for the homepage. If redirecting, ensure final destination returns 200 and "
                    "the redirect chain is <= 2 hops.",
                    "critical",
                ))
            elif 300 <= resp.status_code < 400:
                n_hops = 0
                cur = resp
                while cur.is_redirect or cur.is_permanent_redirect:
                    n_hops += 1
                    break
                if n_hops:
                    out.append(_make_finding(
                        self._fid("CR"),
                        f"Homepage issues HTTP {resp.status_code} redirect",
                        "low",
                        f"GET {homepage} redirects (status {resp.status_code}). Location: {resp.headers.get('Location','')}",
                        "Serve preferred host directly (canonical origin) to avoid redirect latency; "
                        "ensure chains stay under 2 hops.",
                        "low",
                    ))
            # HSTS / HTTPS
            if urlparse(homepage).scheme == "https":
                if "Strict-Transport-Security" not in resp.headers:
                    out.append(_make_finding(
                        self._fid("CR"),
                        "Strict-Transport-Security header is missing",
                        "low",
                        "HTTPS response has no `Strict-Transport-Security` header; downgrade attacks are easier.",
                        "Add `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`.",
                        "low",
                    ))

        # Content-Type charset
        if resp is not None:
            ct = resp.headers.get("Content-Type", "")
            if ct and "charset" not in ct.lower():
                out.append(_make_finding(
                    self._fid("CR"),
                    "Content-Type header omits charset declaration",
                    "low",
                    f"Content-Type: {ct}",
                    "Send charset explicitly, e.g. `Content-Type: text/html; charset=utf-8`, and declare "
                    "`<meta charset='utf-8'>` first in <head>.",
                    "low",
                ))

        return out

    def audit_render(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []
        if soup is None or resp is None:
            return out

        html_raw = resp.text or ""
        body_text_static = soup.get_text(" ", strip=True)

        # Detect JS-heavy SPA patterns that need rendering
        spa_markers = [
            (r"<div[^>]*id=[\"']app[\"']", "Root div #app (classic SPA mount)"),
            (r"data-reactroot|data-react-id|_NEXT|__NEXT_DATA__", "React/Next.js SSR markers"),
            (r"ng-app|ng-version", "Angular markers"),
            (r"data-v-|__VUETEL__|_nuxt|__NUXT__", "Vue/Nuxt markers"),
            (r"svelte-|_svelte", "Svelte markers"),
            (r"window\.__\w+_STATE__|window\.__INITIAL_STATE__", "Client hydration blob"),
        ]
        spa_hits = [desc for (rx, desc) in spa_markers if re.search(rx, html_raw)]

        low_body = len(body_text_static) < 400
        noscript = soup.find("noscript")
        noscript_text = noscript.get_text(" ", strip=True) if noscript else ""

        if (spa_hits or low_body) and len(body_text_static) < 600 and len(noscript_text) < 200:
            out.append(_make_finding(
                self._fid("RD"),
                "Page body appears to be client-rendered without SSR/noscript fallback",
                "high",
                (
                    f"SPA markers detected: {', '.join(spa_hits) if spa_hits else '(none)'}. "
                    f"Static extracted text length={len(body_text_static)} chars; "
                    f"<noscript> text length={len(noscript_text)} chars. "
                    "Non-rendering crawlers may see an almost-empty page."
                ),
                "Enable server-side rendering (SSR) or static-site generation (SSG) for public pages. "
                "If staying CSR-only, ship a populated <noscript> fallback and ensure window.__*_STATE__ "
                "blobs contain the same facts visible to users. Validate with Google's URL Inspection Tool.",
                "high",
            ))

        # <head> integrity: title + meta charset first-ish
        head = soup.head
        if head:
            children = list(head.children)
            if not any(getattr(c, "name", None) == "meta" and c.get("charset") for c in children):
                out.append(_make_finding(
                    self._fid("RD"),
                    "No <meta charset> in <head>",
                    "low",
                    "<head> has no charset declaration; browsers must guess encoding.",
                    "Place `<meta charset='utf-8'>` as the first child of <head> (before <title>).",
                    "low",
                ))
            title_tags = head.find_all("title")
            if not title_tags:
                out.append(_make_finding(
                    self._fid("RD"),
                    "Page has no <title> tag",
                    "high",
                    "<title> missing from <head>. Both search results and AI citations use titles as anchors.",
                    "Add a descriptive, unique <title> (50–60 chars) containing the brand name and core value prop.",
                    "high",
                ))
            elif len(title_tags) > 1:
                out.append(_make_finding(
                    self._fid("RD"),
                    "Multiple <title> tags in <head>",
                    "medium",
                    f"Found {len(title_tags)} <title> tags. Only one is honored.",
                    "Collapse to exactly one <title> per page.",
                    "medium",
                ))
            viewport = head.find("meta", attrs={"name": "viewport"})
            if not viewport or not viewport.get("content"):
                out.append(_make_finding(
                    self._fid("RD"),
                    "No viewport meta tag",
                    "medium",
                    "<head> is missing a responsive viewport declaration.",
                    "Add `<meta name='viewport' content='width=device-width, initial-scale=1'>` to enable mobile-friendly rendering.",
                    "medium",
                ))

        # Base href (affects URL resolution)
        base = soup.find("base")
        if base and not base.get("href"):
            out.append(_make_finding(
                self._fid("RD"),
                "<base> tag without href",
                "low",
                "<base> present but has no href attribute; relative URL resolution is undefined.",
                "Either remove <base> or populate its href to the site's canonical origin.",
                "low",
            ))

        return out

    # =====================================================================
    # 2. Structured Data (JSON-LD, Microdata, RDFa, OG, Twitter)
    # =====================================================================

    def audit_structured_data(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []
        if soup is None:
            return out

        jsonld_blocks: list[dict] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            txt = tag.string or tag.get_text()
            if not txt:
                continue
            try:
                data = json.loads(txt)
            except json.JSONDecodeError as e:
                out.append(_make_finding(
                    self._fid("SD"),
                    "Invalid JSON-LD block (JSON parse error)",
                    "high",
                    f"script[type=application/ld+json] failed to parse: {e.msg} at line {e.lineno} col {e.colno}.",
                    "Fix JSON syntax in the offending block. Validate with schema.org's validator or Google's Rich Results Test.",
                    "high",
                ))
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        jsonld_blocks.append(item)
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    for item in data["@graph"]:
                        if isinstance(item, dict):
                            jsonld_blocks.append(item)
                else:
                    jsonld_blocks.append(data)

        types_present = {b.get("@type") for b in jsonld_blocks if isinstance(b.get("@type"), str)}
        # Microdata itemtype extract
        md_types: set[str] = set()
        for md in soup.find_all(attrs={"itemtype": True}):
            t = md.get("itemtype", "")
            m = re.search(r"schema\.org/([A-Za-z0-9]+)", t)
            if m:
                md_types.add(m.group(1))
        all_types = types_present | md_types

        # --- overall presence
        if not jsonld_blocks and not md_types:
            out.append(_make_finding(
                self._fid("SD"),
                "No structured data (JSON-LD or microdata) found on homepage",
                "critical",
                "0 JSON-LD blocks, 0 schema.org microdata itemtype attributes. AI assistants have no machine-readable fact graph to cite.",
                "Deploy JSON-LD (preferred) at minimum declaring Organization + WebSite + BreadcrumbList + page-specific types "
                "(Product / Article / FAQPage / Service). Use schema.org terms exactly.",
                "critical",
            ))

        # --- Organization
        if "Organization" not in all_types and "LocalBusiness" not in all_types:
            out.append(_make_finding(
                self._fid("SD"),
                "Missing Organization/LocalBusiness schema",
                "high",
                f"Structured types present: {sorted(all_types) or 'none'}. No Organization entity anchors the brand.",
                "Add an Organization (or LocalBusiness) JSON-LD node with `name`, `url`, `logo`, `sameAs`, "
                "`foundingDate`, and `description`. This is the anchor for cross-web entity identity.",
                "high",
            ))
        else:
            org = next(
                (b for b in jsonld_blocks if b.get("@type") in ("Organization", "LocalBusiness")),
                None,
            )
            if org:
                if "sameAs" not in org or not org["sameAs"]:
                    out.append(_make_finding(
                        self._fid("SD"),
                        "Organization schema has no sameAs links (entity disambiguation)",
                        "high",
                        "Organization JSON-LD lacks a `sameAs` array pointing at authoritative profiles.",
                        "Populate `sameAs` with the brand's Wikipedia, Wikidata, LinkedIn company page, "
                        "Crunchbase, and official social profiles (Twitter/X, Facebook, Instagram, YouTube channel).",
                        "high",
                    ))
                for field_ in ("name", "url", "logo"):
                    if field_ not in org:
                        out.append(_make_finding(
                            self._fid("SD"),
                            f"Organization schema missing `{field_}`",
                            "medium",
                            f"Organization JSON-LD has no `{field_}` field.",
                            f"Add `{field_}` to the Organization node. `logo` must be an absolute ImageObject URL.",
                            "medium",
                        ))

        # --- WebSite + SearchAction
        if "WebSite" not in all_types:
            out.append(_make_finding(
                self._fid("SD"),
                "Missing WebSite schema with SearchAction (site-links search box)",
                "medium",
                "No WebSite @type declared. A potentialAction:SearchAction enables SERP site-links search boxes.",
                "Add `{\"@type\":\"WebSite\",\"url\":\"...\",\"potentialAction\":{\"@type\":\"SearchAction\","
                "\"target\":\"...?q={search_term_string}\",\"query-input\":\"required name=search_term_string\"}}`.",
                "medium",
            ))
        else:
            ws = next((b for b in jsonld_blocks if b.get("@type") == "WebSite"), None)
            if ws and ("potentialAction" not in ws or not isinstance(ws["potentialAction"], (dict, list))):
                out.append(_make_finding(
                    self._fid("SD"),
                    "WebSite schema has no potentialAction SearchAction",
                    "medium",
                    "WebSite JSON-LD has no `potentialAction` declaring a site search.",
                    "Add SearchAction with the correct `target` template and `query-input` constraint.",
                    "medium",
                ))

        # --- BreadcrumbList
        if "BreadcrumbList" not in all_types:
            out.append(_make_finding(
                self._fid("SD"),
                "Missing BreadcrumbList schema",
                "medium",
                "No BreadcrumbList @type present. Breadcrumbs power hierarchical SERP presentation and internal linking.",
                "On every non-homepage page add a BreadcrumbList JSON-LD (or ol+microdata) mirroring the visible breadcrumb trail.",
                "medium",
            ))

        # --- OG tags
        og_vals: dict[str, str] = {}
        for m in soup.find_all("meta", property=re.compile(r"(?i)^og:")):
            p = (m.get("property") or "").lower()
            c = (m.get("content") or "").strip()
            if c:
                og_vals[p] = c
        missing_og = OG_REQUIRED - set(og_vals)
        if missing_og:
            out.append(_make_finding(
                self._fid("SD"),
                "Open Graph tags are incomplete",
                "medium",
                f"Missing required og:* properties: {sorted(missing_og)}. Found: {sorted(og_vals)}",
                "Add <meta property=og:title/type/image/url> on every page. og:image must be ≥1200x630px absolute HTTPS URL.",
                "medium",
            ))
        elif og_vals.get("og:image") and not og_vals["og:image"].startswith("http"):
            out.append(_make_finding(
                self._fid("SD"),
                "og:image is a relative URL",
                "low",
                f"og:image='{og_vals['og:image']}' — many crawlers require absolute URLs.",
                "Use absolute HTTPS URLs for og:image and twitter:image.",
                "low",
            ))

        # --- Twitter cards
        tw_vals: dict[str, str] = {}
        for m in soup.find_all("meta", attrs={"name": re.compile(r"(?i)^twitter:")}):
            n = (m.get("name") or "").lower()
            c = (m.get("content") or "").strip()
            if c:
                tw_vals[n] = c
        missing_tw = TWITTER_REQUIRED - set(tw_vals)
        if missing_tw and not tw_vals:
            out.append(_make_finding(
                self._fid("SD"),
                "No Twitter card meta tags",
                "low",
                "0 meta[name=twitter:*] tags found.",
                "Add Twitter summary_large_image cards for rich sharing. They also serve as fallback for many aggregators.",
                "low",
            ))
        elif missing_tw:
            out.append(_make_finding(
                self._fid("SD"),
                "Twitter card tags are incomplete",
                "low",
                f"Missing: {sorted(missing_tw)}. Found: {sorted(tw_vals)}",
                "Fill in twitter:card, twitter:title, twitter:description, twitter:image consistently with OG.",
                "low",
            ))

        # --- canonical
        canonical = soup.find("link", rel="canonical")
        if not canonical or not canonical.get("href"):
            out.append(_make_finding(
                self._fid("SD"),
                "No rel=canonical link",
                "high",
                "<link rel=canonical href=...> is absent; crawlers may pick duplicate URLs.",
                "Add <link rel=canonical> pointing at the single authoritative URL for this page, from every variant "
                "(http/https, trailing slash, print, AMP, query params).",
                "high",
            ))
        else:
            href = canonical["href"]
            if not href.startswith("http"):
                out.append(_make_finding(
                    self._fid("SD"),
                    "rel=canonical is a relative URL",
                    "medium",
                    f"rel=canonical='{href}' should be absolute.",
                    "Use full absolute HTTPS URL for rel=canonical to prevent misinterpretation.",
                    "medium",
                ))

        return out

    # =====================================================================
    # 3. Content Accessibility (machine-readable facts in text)
    # =====================================================================

    def audit_content_accessibility(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []
        if soup is None:
            return out

        # --- images
        imgs = soup.find_all("img")
        imgs_no_alt = [i for i in imgs if i.get("alt") is None]
        imgs_empty_alt = [i for i in imgs if i.get("alt") is not None and not str(i["alt"]).strip()]
        informative_imgs = max(0, len(imgs) - len(imgs_empty_alt))
        if imgs and len(imgs_no_alt) >= max(1, round(len(imgs) * 0.25)):
            out.append(_make_finding(
                self._fid("CA"),
                "Many informative <img> elements lack alt text",
                "high",
                f"{len(imgs_no_alt)}/{len(imgs)} <img> tags have no alt attribute at all; "
                f"{len(imgs_empty_alt)} have empty alt (decorative convention).",
                "Give every informative image a descriptive `alt` attribute. "
                "Mark purely decorative images with `alt=''` (empty string) explicitly. "
                "For diagrams/charts, pair alt with a longer <figure><figcaption>.",
                "high",
            ))
        # data URI / tiny inline images still counted; ensure at least 1 descriptive if many imgs
        if len(imgs) >= 5 and informative_imgs == 0:
            out.append(_make_finding(
                self._fid("CA"),
                "All images declare empty alt — possible missed informative content",
                "medium",
                f"{len(imgs)} <img> tags, all with empty or missing alt. Facts carried in images will be invisible to AI.",
                "For product photos, team headshots, logos, diagrams, and screenshots, write real alt text describing the visual fact.",
                "medium",
            ))

        # --- SVG
        svgs = soup.find_all("svg")
        svgs_no_title = [s for s in svgs if not s.find("title")]
        if len(svgs) >= 2 and len(svgs_no_title) == len(svgs):
            out.append(_make_finding(
                self._fid("CA"),
                "SVG elements lack <title> accessibility labels",
                "low",
                f"{len(svgs_no_title)}/{len(svgs)} <svg> nodes have no child <title>.",
                "Wrap each meaningful SVG with `<title>…</title>` + `<desc>…</desc>` so crawlers and screen readers grasp its meaning.",
                "low",
            ))

        # --- video/audio without tracks
        for media_tag in ("video", "audio"):
            nodes = soup.find_all(media_tag)
            for n in nodes:
                if not n.find("track", attrs={"kind": re.compile(r"(?i)(captions|subtitles|transcript)")}):
                    out.append(_make_finding(
                        self._fid("CA"),
                        f"<{media_tag}> has no captions/subtitles <track>",
                        "medium",
                        f"<{media_tag}> found without a kind=captions|subtitles|descriptions track element.",
                        f"Add a <track kind='captions' srclang='en' label='English' src='…vtt'> for every {media_tag}, "
                        "and publish a human-readable transcript on the page (text the AI can quote).",
                        "medium",
                    ))
                    break

        # --- iframe without title
        for iframe in soup.find_all("iframe"):
            if not iframe.get("title"):
                out.append(_make_finding(
                    self._fid("CA"),
                    "<iframe> missing title attribute",
                    "low",
                    f"<iframe src='{iframe.get('src','')[:80]}'> has no title; its purpose is opaque.",
                    "Give every iframe a descriptive `title=` describing the embedded content.",
                    "low",
                ))
                break

        # --- <title> length & brand
        title_tag = soup.title
        if title_tag and title_tag.string:
            t = title_tag.string.strip()
            if len(t) < 15:
                out.append(_make_finding(
                    self._fid("CA"),
                    "<title> is too short to be distinctive",
                    "medium",
                    f"Title='{t}' ({len(t)} chars). Below typical 50–60 char target window.",
                    "Rewrite title to ~50–60 characters: include brand name, primary keyword, and a differentiator.",
                    "medium",
                ))
            elif len(t) > 70:
                out.append(_make_finding(
                    self._fid("CA"),
                    "<title> exceeds typical visible length",
                    "low",
                    f"Title is {len(t)} chars; SERP truncation at ~60 chars is likely.",
                    "Trim title to ≤60 chars so the full meaning is visible without truncation.",
                    "low",
                ))

        # --- meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if not meta_desc or not (meta_desc.get("content") or "").strip():
            out.append(_make_finding(
                self._fid("CA"),
                "No meta description",
                "medium",
                "<meta name=description> is missing. Used as fallback snippet text by search and citation UI.",
                "Write a 140–160 character meta description summarizing the page with a CTA hint. "
                "Keep it unique per page.",
                "medium",
            ))
        else:
            d = meta_desc["content"].strip()
            if len(d) < 80:
                out.append(_make_finding(
                    self._fid("CA"),
                    "Meta description is very short",
                    "low",
                    f"meta description length={len(d)} chars (target 140–160).",
                    "Expand meta description to 140–160 chars with specific facts and an implicit call to action.",
                    "low",
                ))
            elif len(d) > 170:
                out.append(_make_finding(
                    self._fid("CA"),
                    "Meta description is likely truncated",
                    "low",
                    f"meta description length={len(d)} chars; snippet generation may clip at ~160.",
                    "Shorten to ≤160 chars so your crafted sentence appears in full.",
                    "low",
                ))

        # --- Heading hierarchy
        h1 = soup.find_all("h1")
        if len(h1) == 0:
            out.append(_make_finding(
                self._fid("CA"),
                "Page has no <h1> heading",
                "high",
                "Zero <h1> tags. A top-level semantic heading is the primary structural anchor for extractive reading.",
                "Add exactly one <h1> that states the page's core proposition in 5–15 words.",
                "high",
            ))
        elif len(h1) > 1:
            out.append(_make_finding(
                self._fid("CA"),
                "Multiple <h1> headings on one page",
                "medium",
                f"Found {len(h1)} <h1> tags; page hierarchy is ambiguous.",
                "Keep exactly one <h1>. Demote the rest to <h2>/<h3> under a single top-level topic.",
                "medium",
            ))

        # Skipped heading levels
        level_counts: Counter[int] = Counter()
        for i in range(1, 7):
            level_counts[i] = len(soup.find_all(f"h{i}"))
        active = [lvl for lvl in range(1, 7) if level_counts[lvl] > 0]
        if active:
            for prev, cur in zip(active, active[1:]):
                if cur - prev > 1:
                    out.append(_make_finding(
                        self._fid("CA"),
                        f"Heading levels skip from H{prev} to H{cur}",
                        "low",
                        f"Counts: H1={level_counts[1]} H2={level_counts[2]} H3={level_counts[3]} "
                        f"H4={level_counts[4]} H5={level_counts[5]} H6={level_counts[6]}.",
                        "Respect strict nesting: H1 → H2 → H3. Never jump from H2 to H4 without an H3.",
                        "low",
                    ))
                    break
        if sum(level_counts.values()) == 0 and soup is not None:
            out.append(_make_finding(
                self._fid("CA"),
                "Page has no semantic headings (H1–H6)",
                "high",
                "No H1..H6 tags found. Extractors rely on headings to identify sections.",
                "Introduce a heading hierarchy: one H1 plus H2/H3/H4 subdivisions mirroring page structure.",
                "high",
            ))

        # --- text ratio
        ratio = _text_ratio(soup)
        body = soup.body
        if body:
            visible = body.get_text(" ", strip=True)
            if len(visible) < 300:
                out.append(_make_finding(
                    self._fid("CA"),
                    "Homepage carries very little readable text",
                    "high",
                    f"Visible body text ~ {len(visible)} chars; text/HTML ratio = {ratio:.0%}.",
                    "Add a hero paragraph, feature descriptions, and supporting copy stating the brand's facts "
                    "(what it does, who it serves, why it's trustworthy) in plain semantic <p>/<li> elements.",
                    "high",
                ))
            elif ratio < 0.06:
                out.append(_make_finding(
                    self._fid("CA"),
                    "Text-to-HTML ratio is very low",
                    "medium",
                    f"text/HTML={ratio:.0%} — lots of chrome, little fact-bearing text.",
                    "Reduce wrapper bloat and move inline styles/scripts to external files; "
                    "expand prose sections with concrete, citable sentences.",
                    "medium",
                ))

        # --- facts in data attributes only (sign of unreadable content)
        data_attr_count = sum(
            1 for tag in soup.find_all(attrs=lambda a: any(k.startswith("data-") for k in (a or {})))
        )
        if data_attr_count > 50 and len(visible if 'visible' in locals() else soup.get_text(' ', strip=True)) < 1000:
            out.append(_make_finding(
                self._fid("CA"),
                "Content appears to live mostly in data-* attributes",
                "medium",
                f"{data_attr_count} data-* attributes vs. ~{len(visible)} chars of visible text.",
                "Render any fact that should be cited into real DOM text nodes, not just JS-only data attributes.",
                "medium",
            ))

        return out

    # =====================================================================
    # 4. Freshness & Entity
    # =====================================================================

    def audit_freshness_entity(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []

        # --- last-modified header
        if resp is not None:
            lm = resp.headers.get("Last-Modified", "")
            etag = resp.headers.get("ETag", "")
            if not lm and not etag:
                out.append(_make_finding(
                    self._fid("FE"),
                    "No Last-Modified or ETag cache headers",
                    "low",
                    "Response has neither Last-Modified nor ETag; crawlers can't cheaply detect freshness.",
                    "Emit `Last-Modified` or `ETag` (or both) on every GET response so crawlers revalidate efficiently.",
                    "low",
                ))

        if soup is None:
            return out

        # --- <time datetime>
        time_tags = soup.find_all("time")
        has_date_time = any(t.get("datetime") for t in time_tags)
        og_pub = soup.find("meta", property="article:published_time")
        og_mod = soup.find("meta", property="article:modified_time")

        if not (has_date_time or og_pub or og_mod):
            # For homepages it's debatable, but many sites show "last updated". Flag only if body has article content.
            if soup.find(["article", "main"]) and len(soup.get_text(" ", strip=True)) > 1500:
                out.append(_make_finding(
                    self._fid("FE"),
                    "No machine-readable publish/update timestamps on article content",
                    "medium",
                    "No <time datetime=''>, meta[property=article:published_time], or article:modified_time found.",
                    "Add <time datetime='YYYY-MM-DDTHH:MM:SSZ'> wrapping visible date text, plus "
                    "meta article:published_time and article:modified_time.",
                    "medium",
                ))

        # --- stale copyright year in footer
        footer = soup.find("footer") or soup
        text = footer.get_text(" ", strip=True)
        year = datetime.now().year
        year_hits = re.findall(r"\b(19|20)\d{2}\b", text)
        if year_hits:
            years = {int(yy[0] + yy[1]) for yy in re.findall(r"\b((?:19|20)\d{2})\b", text)}
            if year not in years and (year - 1) not in years and text:
                out.append(_make_finding(
                    self._fid("FE"),
                    "Copyright year in footer appears stale",
                    "low",
                    f"Copyright text mentions years {sorted(years)}; current year {year} is missing.",
                    "Update the footer copyright range to end in the current year; prefer a dynamic template variable.",
                    "low",
                ))

        # --- entity disambiguation via sameAs / Wikipedia links in body
        body_links = soup.find_all("a", href=True)
        external = [a["href"] for a in body_links if not _same_domain(homepage, a["href"]) and a["href"].startswith("http")]
        has_wikipedia = any("wikipedia.org" in l for l in external)
        has_wikidata = any("wikidata.org" in l for l in external)

        jsonld_org = None
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
                if isinstance(data, dict) and data.get("@type") in ("Organization", "LocalBusiness"):
                    jsonld_org = data
                    break
            except Exception:
                pass
        jsonld_has_sameAs = bool(jsonld_org and jsonld_org.get("sameAs"))

        if not (has_wikipedia or has_wikidata or jsonld_has_sameAs):
            out.append(_make_finding(
                self._fid("FE"),
                "No cross-web entity corroboration (sameAs / Wikipedia / Wikidata)",
                "high",
                "Page links to neither Wikipedia nor Wikidata, and JSON-LD Organization lacks `sameAs` array. "
                "AI has no anchor to disambiguate this brand from namesakes.",
                (
                    "Create/maintain a Wikipedia article and Wikidata entry for the brand; "
                    "link them from the site's about/press page; "
                    "and include those URLs in Organization schema's `sameAs` array along with LinkedIn, Crunchbase, and social profiles."
                ),
                "high",
            ))

        # --- hreflang absent on multi-language hints
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            pass
        else:
            out.append(_make_finding(
                self._fid("FE"),
                "<html> has no `lang` attribute",
                "low",
                "<html lang=''> is unset. Language identification is a prerequisite for correct tokenization and citation.",
                "Set `<html lang='en'>` (or appropriate BCP-47 code) on the root element.",
                "low",
            ))
        hreflangs = soup.find_all("link", rel="alternate", hreflang=True)
        if hreflangs:
            # Verify self-reference present
            self_href = None
            for hl in hreflangs:
                if hl.get("hreflang") in ("x-default", html_tag.get("lang") if html_tag else None):
                    self_href = hl.get("href")
            if not self_href:
                out.append(_make_finding(
                    self._fid("FE"),
                    "hreflang set missing a self-referencing tag",
                    "low",
                    "Alternate hreflang tags exist but don't include a self-reference; Google treats the set as invalid.",
                    "Add a `<link rel=alternate hreflang=… href=this-page>` pointing at itself, plus an `hreflang=x-default` fallback.",
                    "low",
                ))

        return out

    # =====================================================================
    # 5. On-Site Engagement
    # =====================================================================

    def audit_engagement(self, homepage: str, resp: requests.Response | None, soup: BeautifulSoup | None) -> list[dict]:
        out: list[dict] = []
        if soup is None:
            return out

        # Internal/external link counts
        anchors = soup.find_all("a", href=True)
        internal: list[str] = []
        external: list[str] = []
        for a in anchors:
            href = _absolute(homepage, a.get("href"))
            if not href:
                continue
            if _same_domain(homepage, href):
                internal.append(href)
            elif href.startswith("http"):
                external.append(href)

        if len(internal) < 5:
            out.append(_make_finding(
                self._fid("EG"),
                "Homepage has very few internal links",
                "high",
                f"Found {len(internal)} internal links (<5). Visitors and crawlers have few paths to explore deeper.",
                "Add a site-wide navigation header linking to key sections (product, pricing, about, docs, blog), "
                "an HTML sitemap / featured categories block, and contextual inline links within body copy.",
                "high",
            ))

        # Distinct paths (don't double count)
        uniq_paths = {urlparse(u).path.rstrip("/") or "/" for u in internal}
        if len(uniq_paths) == 1 and "/" in uniq_paths and len(internal) > 0:
            out.append(_make_finding(
                self._fid("EG"),
                "All internal links on homepage point only at the homepage",
                "medium",
                f"{len(internal)} internal links all resolve to path '/'.",
                "Link out from homepage to sub-pages so the site forms a proper graph, not a single-node star.",
                "medium",
            ))

        # Nav landmark
        nav = soup.find(["nav", "[role=navigation]"], attrs={"role": "navigation"})
        if nav is None and soup.find("nav") is None:
            # Many sites use <nav> without role, so soup.find("nav") already captured.
            if soup.find("nav") is None:
                out.append(_make_finding(
                    self._fid("EG"),
                    "No <nav> landmark found",
                    "medium",
                    "No <nav> element on homepage; orientation is weak.",
                    "Wrap the primary site navigation in a <nav> (or <header><nav>…</nav></header>) landmark with a clear label.",
                    "medium",
                ))

        # Search mechanism
        search_action = False
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(tag.string or "{}")
                items = d if isinstance(d, list) else ([d] if isinstance(d, dict) else [])
                for item in items:
                    if isinstance(item, dict):
                        stack = [item]
                        while stack:
                            cur = stack.pop()
                            if cur.get("@type") == "SearchAction":
                                search_action = True
                            for v in cur.values():
                                if isinstance(v, dict):
                                    stack.append(v)
                                elif isinstance(v, list):
                                    stack.extend(x for x in v if isinstance(x, dict))
            except Exception:
                pass
        form_search = soup.find("form", attrs={"role": "search"}) or soup.find("form") and any(
            inp.get("type") == "search" or "search" in (inp.get("name") or "").lower() or "s" == (inp.get("name") or "").lower()
            for inp in (soup.find("form").find_all("input") if soup.find("form") else [])
        )
        if not (search_action or form_search):
            out.append(_make_finding(
                self._fid("EG"),
                "No on-site search mechanism detected",
                "medium",
                "Neither <form role=search> nor JSON-LD SearchAction present. Visitors cannot self-serve answers.",
                "Add a site search (e.g., Algolia, Typesense, or full-text search on the origin). Expose it via a visible search box "
                "and declare it in WebSite schema as SearchAction so AI assistants can deep-link into results.",
                "medium",
            ))

        # CTA density
        cta_words = re.compile(
            r"(?i)\b(sign\s*up|subscribe|register|start|try|buy|book|demo|get\s*started|request|contact|download|join)\b"
        )
        buttons = soup.find_all(["button", "a", "input"])
        cta_count = 0
        for b in buttons:
            txt = (b.get_text(" ", strip=True) or b.get("value") or b.get("aria-label") or "").strip()
            if cta_words.search(txt):
                cta_count += 1
        if cta_count == 0 and len(soup.get_text(" ", strip=True)) > 800:
            out.append(_make_finding(
                self._fid("EG"),
                "No prominent call-to-action buttons or links",
                "high",
                "Scanned buttons/links/inputs; zero contain CTA verbs (sign up, try, demo, buy, download, contact, get started).",
                "Place at least one primary CTA (e.g. 'Get Started', 'Book Demo') 'above the fold' with a contrasting style, "
                "and secondary CTAs inline with feature sections.",
                "high",
            ))
        elif 0 < cta_count < 2:
            out.append(_make_finding(
                self._fid("EG"),
                "Only one CTA on the homepage",
                "low",
                f"Found {cta_count} CTA element(s). Single CTA forces scrolling for users ready to convert.",
                "Repeat the primary CTA near the hero, mid-page after social proof, and in the footer. Tailor secondary CTAs per section.",
                "low",
            ))

        # Breadcrumb trail (visual)
        has_crumbs = False
        for cls in ("breadcrumb", "breadcrumbs", "crumbs"):
            if soup.find(class_=re.compile(cls)):
                has_crumbs = True
                break
        if soup.find("ol") and not has_crumbs:
            # ok, nothing wrong
            pass

        # Related / recent sections
        keywords = ["related", "recent", "popular", "featured", "trending", "you might", "recommended"]
        headings = soup.find_all(["h1", "h2", "h3", "h4"])
        related_heading = any(
            any(k in (h.get_text(" ", strip=True).lower()) for k in keywords) for h in headings
        )
        if not related_heading and len(soup.get_text(" ", strip=True)) > 1500:
            out.append(_make_finding(
                self._fid("EG"),
                "No 'related/recent/featured' content block found",
                "medium",
                "Longer page but no section heading suggests 'recent', 'popular', 'featured', or 'you may also like' context block.",
                "Add a 'Related articles', 'Featured products', or 'You might also like' block with 3–5 internal links "
                "to keep visitors moving and expose more URLs to crawlers.",
                "medium",
            ))

        # Pagination vs inf-scroll signal (heuristic)
        has_next = bool(soup.find("a", rel="next") or soup.find("link", rel="next"))
        big_list = len(soup.find_all("article")) > 20 or len(soup.find_all("li")) > 200
        if big_list and not has_next:
            out.append(_make_finding(
                self._fid("EG"),
                "Long list without pagination or rel=next signals",
                "low",
                f"List-like page (many <article>/<li>) but no <a rel=next> or <link rel=next>.",
                "Add real ?page=N paginated URLs with rel=prev/next link tags; avoid infinite-scroll-only collections.",
                "low",
            ))

        # Footer link richness
        footer = soup.find("footer")
        if footer:
            footer_links = footer.find_all("a", href=True)
            if len(footer_links) < 5:
                out.append(_make_finding(
                    self._fid("EG"),
                    "Footer contains very few links",
                    "low",
                    f"Footer has {len(footer_links)} links; typical site footers carry ~10–30 utility links.",
                    "Expand the footer with category links, contact, about, careers, legal/privacy, and social profile links.",
                    "low",
                ))
        else:
            out.append(_make_finding(
                self._fid("EG"),
                "No <footer> landmark present",
                "medium",
                "Page has no <footer> element; standard orientation and legal links are expected there.",
                "Wrap bottom utility links and copyright in a <footer> landmark element.",
                "medium",
            ))

        # --- Page weight / performance hints (heuristic)
        html_bytes = len(resp.content) if resp is not None else 0
        if html_bytes > 500_000:
            out.append(_make_finding(
                self._fid("EG"),
                "HTML payload is very large (>500 KB)",
                "medium",
                f"HTML document is {html_bytes/1024:.0f} KB; TTFB + parse will be slow on mobile.",
                "Defer/de-duplicate inline JS, inline CSS only above-the-fold rules, and strip unused markup; "
                "serve compressed (gzip/brotli) responses.",
                "medium",
            ))

        # --- resource hints (preconnect, dns-prefetch)
        has_hints = bool(
            soup.find("link", rel=re.compile(r"(?i)^(dns-prefetch|preconnect|preload|prefetch)$"))
        )
        if external and not has_hints:
            out.append(_make_finding(
                self._fid("EG"),
                "No preconnect/dns-prefetch resource hints for third-party origins",
                "low",
                f"{len(external)} distinct external hosts but no <link rel=dns-prefetch|preconnect>.",
                "Add `<link rel=preconnect href='https://…'>` for critical third parties (fonts, analytics, CDN) "
                "and `<link rel=dns-prefetch>` for the rest to cut connection latency.",
                "low",
            ))

        return out

    # =====================================================================
    # 6. Multi-page crawl (homepage + top internal pages)
    # =====================================================================

    def _collect_internal_links(self, homepage: str, soup: BeautifulSoup) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for a in soup.find_all("a", href=True):
            href = _absolute(homepage, a.get("href"))
            if not href:
                continue
            if not _same_domain(homepage, href):
                continue
            if not self.session.can_fetch(href):
                continue
            p = urlparse(href)
            if p.scheme not in ("http", "https"):
                continue
            key = p.netloc + p.path.rstrip("/") + (("?" + p.query) if p.query else "")
            if key in seen:
                continue
            seen.add(key)
            out.append(href)
            if len(out) >= MAX_INTERNAL_LINKS:
                break
        return out

    def _pick_sample_pages(self, homepage: str, links: list[str]) -> list[str]:
        """Pick a small, representative sample of pages to deep-audit."""
        # Score by "distinctiveness" of path.
        def score(u: str) -> tuple[int, int, str]:
            p = urlparse(u)
            segs = [s for s in p.path.split("/") if s]
            is_media = any(ext in p.path.lower() for ext in (".jpg", ".jpeg", ".png", ".gif", ".pdf", ".mp4", ".zip"))
            return (0 if not is_media else 1, -len(segs), u)

        ordered = sorted(set(links), key=score)
        # homepage first, then a mix of depth-1 pages + deepest pages
        chosen: list[str] = [homepage]
        depth1 = [u for u in ordered if len([s for s in urlparse(u).path.split("/") if s]) == 1]
        deep = [u for u in ordered if len([s for s in urlparse(u).path.split("/") if s]) >= 2]
        for src in (depth1, deep):
            for u in src:
                if u not in chosen and len(chosen) < MAX_PAGES_CRAWLED:
                    chosen.append(u)
        return chosen[:MAX_PAGES_CRAWLED]

    # =====================================================================
    # Run orchestration
    # =====================================================================

    def run_all(self, homepage_input: str) -> dict[str, Any]:
        url = homepage_input.strip()
        if not re.match(r"(?i)^https?://", url):
            url = "https://" + url
        netloc = urlparse(url).netloc
        site = netloc.lower()

        # Fetch homepage
        resp, soup = self.session.fetch_html(url)

        findings: list[dict] = []

        findings += self.audit_crawlability(url, resp, soup)
        findings += self.audit_render(url, resp, soup)
        findings += self.audit_structured_data(url, resp, soup)
        findings += self.audit_content_accessibility(url, resp, soup)
        findings += self.audit_freshness_entity(url, resp, soup)
        findings += self.audit_engagement(url, resp, soup)

        # If homepage is usable, crawl a sample of internal pages for cross-page checks
        if resp is not None and soup is not None and resp.status_code < 400:
            links = self._collect_internal_links(url, soup)
            samples = self._pick_sample_pages(url, links)[1:]  # skip homepage, already audited

            # Cross-page aggregations (structural patterns across samples)
            title_counts: Counter[str] = Counter()
            desc_counts: Counter[str] = Counter()
            h1_counts: Counter[str] = Counter()
            jsonld_pages_with_org = 0
            pages_with_structured_data = 0
            pages_without_meta_desc = 0
            pages_without_canonical = 0
            pages_http_err = 0
            n_samples = 0

            for sub in samples:
                sresp, ssoup = self.session.fetch_html(sub)
                if sresp is None or sresp.status_code >= 400:
                    pages_http_err += 1
                    continue
                if ssoup is None:
                    continue
                n_samples += 1
                t = (ssoup.title.string or "").strip() if ssoup.title else ""
                if t:
                    title_counts[t] += 1
                md = ssoup.find("meta", attrs={"name": "description"})
                md_c = (md.get("content") or "").strip() if md else ""
                if md_c:
                    desc_counts[md_c] += 1
                else:
                    pages_without_meta_desc += 1
                h1_el = ssoup.find("h1")
                if h1_el and h1_el.get_text(" ", strip=True):
                    h1_counts[h1_el.get_text(" ", strip=True)] += 1
                if not ssoup.find("link", rel="canonical"):
                    pages_without_canonical += 1
                # Structured data
                has_sd = False
                for tag in ssoup.find_all("script", type="application/ld+json"):
                    try:
                        d = json.loads(tag.string or "{}")
                        has_sd = True
                        stack = [d] if isinstance(d, dict) else list(d) if isinstance(d, list) else []
                        while stack:
                            cur = stack.pop()
                            if not isinstance(cur, dict):
                                continue
                            if cur.get("@type") in ("Organization", "LocalBusiness"):
                                jsonld_pages_with_org += 1
                                break
                            for v in cur.values():
                                if isinstance(v, dict):
                                    stack.append(v)
                                elif isinstance(v, list):
                                    stack.extend(x for x in v if isinstance(x, dict))
                    except Exception:
                        pass
                if has_sd or ssoup.find(attrs={"itemtype": True}):
                    pages_with_structured_data += 1

            if n_samples >= 3:
                dup_titles = [(t, c) for t, c in title_counts.items() if c > 1]
                if dup_titles:
                    t, c = dup_titles[0]
                    findings.append(_make_finding(
                        self._fid("CR"),
                        f"Duplicate page titles across {c} sampled pages",
                        "high",
                        f"Across {n_samples} sampled internal pages, title '{t[:80]}…' repeats {c} times. "
                        f"Total duplicates: {len(dup_titles)}.",
                        "Write unique page-specific <title> for every URL; brand+suffix at the end is fine.",
                        "high",
                    ))
                dup_descs = [(d, c) for d, c in desc_counts.items() if c > 1]
                if dup_descs:
                    findings.append(_make_finding(
                        self._fid("CA"),
                        "Duplicate meta descriptions across sampled pages",
                        "medium",
                        f"Across {n_samples} sampled pages, {len(dup_descs)} duplicate meta descriptions found.",
                        "Make meta descriptions unique per page. Omit entirely rather than repeating a generic one.",
                        "medium",
                    ))
                dup_h1s = [(h, c) for h, c in h1_counts.items() if c > 1]
                if dup_h1s:
                    findings.append(_make_finding(
                        self._fid("CA"),
                        "Duplicate H1 headings across sampled pages",
                        "medium",
                        f"Across {n_samples} sampled pages, {len(dup_h1s)} duplicate H1s found.",
                        "Give every page its own H1 that states the page's unique topic.",
                        "medium",
                    ))
                if pages_without_meta_desc:
                    findings.append(_make_finding(
                        self._fid("CA"),
                        f"{pages_without_meta_desc}/{n_samples} sampled pages lack a meta description",
                        "medium",
                        f"Cross-page scan: {pages_without_meta_desc}/{n_samples} internal pages have no meta description.",
                        "Add a unique meta description to every page.",
                        "medium",
                    ))
                if pages_without_canonical:
                    findings.append(_make_finding(
                        self._fid("SD"),
                        f"{pages_without_canonical}/{n_samples} sampled pages lack rel=canonical",
                        "medium",
                        f"Cross-page scan: {pages_without_canonical}/{n_samples} internal pages have no rel=canonical.",
                        "Add rel=canonical pointing at the authoritative URL on every page variant.",
                        "medium",
                    ))
                if pages_with_structured_data == 0:
                    findings.append(_make_finding(
                        self._fid("SD"),
                        f"0/{n_samples} sampled internal pages carry any structured data",
                        "high",
                        f"Cross-page scan: no JSON-LD or microdata on any of {n_samples} internal pages sampled.",
                        "Add JSON-LD structured data (BreadcrumbList + page-specific Product/Article/Service type) to every page, not just the homepage.",
                        "high",
                    ))
                if pages_http_err:
                    findings.append(_make_finding(
                        self._fid("CR"),
                        f"{pages_http_err} links from homepage lead to HTTP errors",
                        "high",
                        f"Out of {len(samples)} followed links, {pages_http_err} returned >=400 or failed to connect.",
                        "Audit internal links with a link checker; fix broken links and soft-404s to return proper 404/410 statuses.",
                        "high",
                    ))

        # Re-severity summary (rebuild ids to be contiguous after ordering)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        findings.sort(key=lambda f: (severity_order.get(f["severity"], 99), f["id"]))
        for i, f in enumerate(findings, 1):
            prefix = f["id"].split("-", 1)[0]
            f["id"] = f"F-{i:03d}"
        sev_counts = Counter(f["severity"] for f in findings)
        summary = {
            "total_findings": len(findings),
            "critical": sev_counts.get("critical", 0),
            "high": sev_counts.get("high", 0),
            "medium": sev_counts.get("medium", 0),
            "low": sev_counts.get("low", 0),
            "info": sev_counts.get("info", 0),
        }

        return {
            "site": site,
            "audited_at": _now_iso(),
            "summary": summary,
            "findings": findings,
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            json.dumps(
                {"error": "usage: audit_engine.py <url> [--pretty]"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    url = argv[1]
    pretty = "--pretty" in argv
    sess = AuditorSession()
    engine = AuditEngine(sess)
    try:
        report = engine.run_all(url)
    finally:
        sess.close()
    if pretty:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
