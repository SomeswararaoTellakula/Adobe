#!/usr/bin/env python3
"""
collect_site_pack.py - read-only site collector for the brand-ai-readiness-audit marketplace.

This is the ONLY script in the marketplace that touches the network. It fetches a
deterministic, robots-respecting sample of a site and writes a normalized "site pack"
that every analyzer skill reads offline. Collecting once and analyzing many times keeps
the audit fast, polite, reproducible, and easy to reason about.

Guarantees:
  * GET/HEAD only. No forms, no POST, no cookies persisted, no credentials, no JS execution.
  * robots.txt is parsed and obeyed for our own user-agent, with no override of any kind.
  * URLs matching authenticated / transactional / destructive patterns are never fetched.
  * Third-party crawler user-agents are NEVER impersonated. Their access is evaluated
    statically against robots.txt instead (see references/ai-crawler-registry.md).
  * Output ordering is stable, so the same site produces the same pack.

Usage:
  python3 collect_site_pack.py --url https://example.com --out ./pack
  python3 collect_site_pack.py --url https://example.com --out ./pack --max-pages 30 --budget 200

Exit codes: 0 ok, 2 unusable target (DNS/TLS/total failure), 3 blocked by robots.
"""

import argparse
import concurrent.futures
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

VERSION = "1.0.0"
UA = "BrandAIReadinessAudit/1.0 (+read-only site audit; respects robots.txt)"

# Never fetch these - authenticated, transactional, destructive or infinite surfaces.
SKIP_PATTERNS = re.compile(
    r"(?:^|/)(?:wp-admin|wp-login|admin|login|signin|sign-in|signup|sign-up|register|logout|"
    r"signout|account|checkout|cart|basket|order|payment|billing|subscribe|unsubscribe|"
    r"password|reset|oauth|sso|auth|api|graphql|feed|rss|print|amp)(?:/|$|\.)",
    re.I,
)
SKIP_QUERY = re.compile(r"(?:add-to-cart|remove_item|action=|do=|delete|logout|redirect_to)", re.I)
SKIP_EXT = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|avif|svg|ico|css|js|mjs|zip|gz|tar|rar|7z|dmg|exe|pkg|"
    r"mp4|webm|mov|avi|mp3|wav|woff2?|ttf|eot|xml|rss|atom|json|txt)$",
    re.I,
)
TRACKING_PARAMS = re.compile(r"^(?:utm_|gclid|fbclid|mc_cid|mc_eid|ref|source|igshid|_ga|msclkid)", re.I)

CURRENCY_RE = re.compile(
    r"(?:[$\u20b9\u20ac\u00a3\u00a5]|\bUSD\b|\bINR\b|\bEUR\b|\bGBP\b|\bRs\.?)\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|INR|EUR|GBP)\b",
    re.I,
)
# Dates were matched in ISO and English-month forms only. A Japanese daily newspaper was
# therefore reported as "abandoned since 2019": its dates are written 2026年9月12日, which
# matched nothing, so one stray ISO date elsewhere became the newest date on the site.
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2})\b"                                   # ISO
    r"|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"                     # ja / zh
    r"|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"                     # ko
    r"|\b\d{4}[./]\d{1,2}[./]\d{1,2}\b"                             # 2026/09/12, 2026.09.12
    r"|\b\d{1,2}[./]\d{1,2}[./]\d{4}\b"                             # 12/09/2026, 12.09.2026
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"
    # month names in the languages the collector already has lexicons for
    r"|\b\d{1,2}\.?\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
    r"setiembre|octubre|noviembre|diciembre|janeiro|fevereiro|mar\u00e7o|maio|junho|julho|setembro|"
    r"outubro|dezembro|janvier|f\u00e9vrier|mars|avril|mai|juin|juillet|ao\u00fbt|septembre|octobre|"
    r"novembre|d\u00e9cembre|januar|februar|m\u00e4rz|mai|juni|juli|august|september|oktober|november|"
    r"dezember|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|"
    r"dicembre)\s+(?:de\s+)?\d{4}\b",
    re.I,
)
COPYRIGHT_RE = re.compile(r"(?:\u00a9|&copy;|copyright)\s*(?:\d{4}\s*[-\u2013]\s*)?(\d{4})", re.I)
QUESTION_HEAD_RE = re.compile(r"^\s*(?:how|what|why|when|where|who|which|can|do|does|is|are|should|will)\b|\?\s*$", re.I)
SUPERLATIVE_RE = re.compile(
    r"\b(?:world[- ]class|best[- ]in[- ]class|industry[- ]leading|leading|cutting[- ]edge|state[- ]of[- ]the[- ]art|"
    r"revolutionary|seamless|innovative|next[- ]gen(?:eration)?|unparalleled|unmatched|game[- ]chang\w+|"
    r"one[- ]stop|holistic|synerg\w+|empower\w*|unlock\w*|transform\w+|premier|trusted by)\b",
    re.I,
)
SPECIFIC_FACT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|ms|s|hrs?|hours?|days?|weeks?|months?|years?|GB|MB|TB|km|kg|mi|users?|"
    r"customers?|countries|employees|seats?|licen[cs]es?)\b|\b(?:19|20)\d{2}\b",
    re.I,
)

FRAMEWORK_SIGNS = {
    "next": re.compile(r"__NEXT_DATA__|/_next/static", re.I),
    "nuxt": re.compile(r"window\.__NUXT__|/_nuxt/", re.I),
    "react": re.compile(r"data-reactroot|react-dom|__REACT_DEVTOOLS", re.I),
    "angular": re.compile(r"ng-version=|<app-root", re.I),
    "vue": re.compile(r"data-v-[0-9a-f]{6,}|__VUE__", re.I),
    "svelte": re.compile(r"svelte-[0-9a-z]{6,}|__sveltekit", re.I),
    "remix": re.compile(r"__remixContext", re.I),
    "gatsby": re.compile(r"___gatsby|gatsby-chunk", re.I),
    "wordpress": re.compile(r"/wp-content/|/wp-includes/", re.I),
    "shopify": re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I),
    "webflow": re.compile(r"data-wf-page|webflow\.js", re.I),
    "squarespace": re.compile(r"static1\.squarespace|Static\.SQUARESPACE_CONTEXT", re.I),
    "wix": re.compile(r"wix-?(?:code|static)|static\.parastorage", re.I),
}
SPA_SHELL_RE = re.compile(r"<div[^>]+id=[\"'](?:root|app|__next|__nuxt|main-app)[\"'][^>]*>\s*</div>", re.I)

MODAL_HINT_RE = re.compile(
    r"(?:class|id|data-[a-z-]+)=\"[^\"]*(?:modal|popup|overlay|lightbox|interstitial|newsletter|"
    r"subscribe-?(?:box|modal)|cookie|consent|gdpr|cmp-|onetrust|cookiebot|klaviyo|privy|exit-intent)[^\"]*\"",
    re.I,
)
AUTOOPEN_RE = re.compile(r"(?:setTimeout[^;]{0,80}(?:modal|popup|overlay)|exit[- ]?intent|autoOpen|auto_open)", re.I)
CHAT_RE = re.compile(r"(?:intercom|drift\.com|tawk\.to|crisp\.chat|hubspot.*conversations|zendesk.*web_widget|livechat)", re.I)

BOILER_TAGS = {"nav", "header", "footer", "aside"}
MAIN_TAGS = {"main", "article"}
SKIP_TEXT_TAGS = {"script", "style", "template", "svg", "noscript"}


# --------------------------------------------------------------------------- robots


class Robots:
    """Minimal, spec-faithful robots.txt evaluator (longest-match wins, Allow breaks ties)."""

    def __init__(self, text=""):
        self.groups = {}          # agent(lower) -> {"allow": [...], "disallow": [...], "crawl_delay": float|None}
        self.sitemaps = []
        self.raw = text or ""
        self.parse(self.raw)

    def parse(self, text):
        current = []
        last_was_agent = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                if not last_was_agent:
                    current = []
                agent = value.lower()
                current.append(agent)
                self.groups.setdefault(agent, {"allow": [], "disallow": [], "crawl_delay": None})
                last_was_agent = True
                continue
            last_was_agent = False
            if field == "sitemap":
                self.sitemaps.append(value)
            elif field in ("allow", "disallow") and current:
                for agent in current:
                    self.groups[agent][field].append(value)
            elif field == "crawl-delay" and current:
                try:
                    for agent in current:
                        self.groups[agent]["crawl_delay"] = float(value)
                except ValueError:
                    pass

    def group_for(self, agent_token):
        """Return (matched_agent_name, group). Longest matching agent token wins, then '*'."""
        token = agent_token.lower()
        best, best_len = None, -1
        for agent in self.groups:
            if agent == "*":
                continue
            if agent in token and len(agent) > best_len:
                best, best_len = agent, len(agent)
        if best:
            return best, self.groups[best]
        if "*" in self.groups:
            return "*", self.groups["*"]
        return None, None

    @staticmethod
    def _match(pattern, path):
        if pattern == "":
            return None
        regex = re.escape(pattern).replace(r"\*", ".*")
        if regex.endswith(r"\$"):
            regex = regex[:-2] + "$"
        return re.match(regex, path)

    def allowed(self, agent_token, url):
        path = urllib.parse.urlsplit(url).path or "/"
        query = urllib.parse.urlsplit(url).query
        if query:
            path += "?" + query
        _, group = self.group_for(agent_token)
        if not group:
            return True, ""
        best_rule, best_len, best_kind = "", -1, "allow"
        for kind in ("allow", "disallow"):
            for rule in group[kind]:
                if self._match(rule, path):
                    weight = len(rule)
                    if weight > best_len or (weight == best_len and kind == "allow"):
                        best_rule, best_len, best_kind = rule, weight, kind
        if best_len < 0:
            return True, ""
        return (best_kind == "allow"), f"{best_kind.capitalize()}: {best_rule}"


# --------------------------------------------------------------------------- HTML parsing


# Known AI / answer-engine agents, classified by the ROLE they play in whether a brand
# gets cited. This distinction matters: blocking a training crawler is a legitimate
# policy choice, while blocking a retrieval or user-triggered fetcher silently removes
# the brand from live answers. See references/ai-crawler-registry.md.
AI_AGENTS = {
    "oai-searchbot":      ("OAI-SearchBot (ChatGPT search index)", "retrieval"),
    "chatgpt-user":       ("ChatGPT-User (user-triggered fetch)", "user_fetch"),
    "gptbot":             ("GPTBot (OpenAI training crawl)", "training"),
    "claude-searchbot":   ("Claude-SearchBot (Anthropic search index)", "retrieval"),
    "claude-user":        ("Claude-User (user-triggered fetch)", "user_fetch"),
    "claudebot":          ("ClaudeBot (Anthropic training crawl)", "training"),
    "anthropic-ai":       ("anthropic-ai (legacy Anthropic token)", "training"),
    "perplexitybot":      ("PerplexityBot (Perplexity index)", "retrieval"),
    "perplexity-user":    ("Perplexity-User (user-triggered fetch)", "user_fetch"),
    "googlebot":          ("Googlebot (Search index; grounds AI Overviews)", "retrieval"),
    "google-extended":    ("Google-Extended (Gemini training/grounding)", "training"),
    "bingbot":            ("Bingbot (Bing index; grounds Copilot)", "retrieval"),
    "applebot":           ("Applebot (Siri / Spotlight)", "retrieval"),
    "applebot-extended":  ("Applebot-Extended (Apple AI training)", "training"),
    "duckassistbot":      ("DuckAssistBot (DuckDuckGo AI answers)", "retrieval"),
    "amazonbot":          ("Amazonbot (Alexa answers)", "retrieval"),
    "youbot":             ("YouBot (You.com)", "retrieval"),
    "ccbot":              ("CCBot (Common Crawl corpus)", "training"),
    "bytespider":         ("Bytespider (ByteDance)", "training"),
    "meta-externalagent": ("meta-externalagent (Meta AI)", "training"),
    "cohere-ai":          ("cohere-ai (Cohere)", "training"),
    "diffbot":            ("Diffbot", "training"),
    "timpibot":           ("Timpibot", "training"),
}


def agent_verdicts(robots, origin, sample_paths):
    """Statically evaluate robots.txt for each known agent.

    We evaluate the rules rather than sending requests as these agents: impersonating a
    third party's user-agent is both dishonest and a bad audit signal.
    """
    out = {}
    paths = ["/"] + sorted({urllib.parse.urlsplit(p).path or "/" for p in sample_paths})[:8]
    for token, (label, role) in sorted(AI_AGENTS.items()):
        matched, group = robots.group_for(token)
        blocked = []
        for path in paths:
            ok, rule = robots.allowed(token, urllib.parse.urljoin(origin, path))
            if not ok:
                blocked.append({"path": path, "rule": rule})
        out[token] = {
            "label": label, "role": role,
            "matched_group": matched,
            "explicit_group": matched is not None and matched != "*",
            "root_allowed": not any(b["path"] == "/" for b in blocked),
            "blocked_paths": blocked[:8],
            "fully_blocked": len(blocked) == len(paths),
        }
    return out


class PageParser(HTMLParser):
    """Tolerant single-pass extractor. Produces the structured facts analyzers need."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.skip_depth = 0
        self.boiler_depth = 0
        self.main_depth = 0
        self.main_seen = False
        self.text_main, self.text_boiler, self.text_other = [], [], []
        self.capture_head, self.head_buf, self.head_meta = None, [], None
        self.capture_link, self.link_buf, self.link_meta = False, [], None
        self.para_stack = []
        self.paragraph_words = []
        self.headings, self.links, self.images, self.iframes = [], [], [], []
        self.jsonld_raw, self.jsonld_errors = [], []
        self.in_jsonld, self.jsonld_buf = False, []
        self.in_noscript, self.noscript_buf = False, []
        self.in_title, self.title = False, ""
        self.metas, self.link_rels = [], []
        self.html_lang = ""
        self.microdata_types = []
        self.counts = {
            "table": 0, "ul": 0, "ol": 0, "dl": 0, "form": 0, "input": 0, "button": 0,
            "video": 0, "audio": 0, "track": 0, "canvas": 0, "details": 0, "picture": 0,
        }
        self.form_field_types = []
        self.aria_controls = 0
        self.ids_in_body = 0
        self.first_text_chars = 0
        self.body_seen = False

    # -- helpers ------------------------------------------------------------
    def _sink(self):
        # The INNERMOST open landmark decides, not a fixed precedence. A <nav> inside
        # <main> is still navigation; a <main> inside an unclosed <header> is still the
        # article. Checking boiler first got the second case wrong on any page whose
        # markup was slightly misnested - which is most real pages.
        for entry in reversed(self.stack):
            if entry[2] == "boiler":
                return self.text_boiler
            if entry[2] == "main":
                return self.text_main
        if self.boiler_depth > 0:
            return self.text_boiler
        if self.main_depth > 0:
            return self.text_main
        return self.text_other

    @staticmethod
    def _attrs(attrs):
        out = {}
        for k, v in attrs:
            if k is not None:
                out[k.lower()] = (v or "")
        return out

    def _is_boiler(self, tag, a):
        if tag in BOILER_TAGS:
            return True
        if a.get("role", "").lower() in ("navigation", "banner", "contentinfo"):
            return True
        blob = (a.get("class", "") + " " + a.get("id", "")).lower()
        return bool(re.search(r"\b(?:site-?(?:nav|header|footer)|navbar|menu|breadcrumb|cookie|skip-link)\b", blob))

    # -- handlers -----------------------------------------------------------
    # Elements that never have an end tag. Pushing them would leave permanent stack
    # residue and make every later unwind imprecise.
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                 "meta", "param", "source", "track", "wbr"}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag not in self.VOID_TAGS:
            self.stack.append([tag, a, None])   # third slot: which depth this bumped

        if tag == "html":
            self.html_lang = a.get("lang", "")
        elif tag == "body":
            self.body_seen = True
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            self.metas.append({
                "name": a.get("name", "").lower(),
                "property": a.get("property", "").lower(),
                "http_equiv": a.get("http-equiv", "").lower(),
                "content": a.get("content", "")[:400],
                "charset": a.get("charset", ""),
            })
        elif tag == "link":
            self.link_rels.append({
                "rel": a.get("rel", "").lower(),
                "href": a.get("href", "")[:500],
                "hreflang": a.get("hreflang", ""),
                "type": a.get("type", ""),
            })
        elif tag == "script":
            if a.get("type", "").lower() in ("application/ld+json", "application/json+ld"):
                self.in_jsonld, self.jsonld_buf = True, []
            self.skip_depth += 1
            return
        elif tag in SKIP_TEXT_TAGS:
            if tag == "noscript":
                self.in_noscript = True
            self.skip_depth += 1
            if tag == "svg":
                return
            return

        if a.get("itemtype"):
            self.microdata_types.append(a["itemtype"][:200])
        if a.get("aria-controls"):
            self.aria_controls += 1
        if a.get("id") and self.body_seen:
            self.ids_in_body += 1

        # Record which counter this element bumped, so the matching unwind is exact even
        # when the document is misnested. Previously the end-tag handler recomputed this
        # from the end tag's (empty) attributes and discarded nested entries wholesale, so
        # an unclosed <div class="menu"> left boiler_depth permanently above zero - and
        # because boiler wins in _sink(), every later word on the page counted as
        # navigation. A real encyclopedia article reported 0 words of content.
        _bumped = None
        if self._is_boiler(tag, a):
            self.boiler_depth += 1
            _bumped = "boiler"
        elif tag in MAIN_TAGS or a.get("role", "").lower() == "main":
            self.main_depth += 1
            self.main_seen = True
            _bumped = "main"
        if _bumped and self.stack and self.stack[-1][0] == tag:
            self.stack[-1][2] = _bumped

        if tag in self.counts:
            self.counts[tag] += 1
        if tag == "input":
            self.form_field_types.append(a.get("type", "text").lower())

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.capture_head, self.head_buf = int(tag[1]), []
            self.head_meta = {"id": a.get("id", ""), "in_main": self.main_depth > 0 or not self.main_seen}
        elif tag == "a":
            self.capture_link, self.link_buf = True, []
            self.link_meta = {
                "href": a.get("href", "")[:800],
                "rel": a.get("rel", "").lower(),
                "target": a.get("target", ""),
                "in_boiler": self.boiler_depth > 0,
                "has_aria_label": bool(a.get("aria-label")),
            }
        elif tag == "img":
            self.images.append({
                "src": (a.get("src") or a.get("data-src") or "")[:500],
                "alt": a.get("alt"),
                "width": a.get("width", ""),
                "height": a.get("height", ""),
                "loading": a.get("loading", ""),
                "in_main": self.main_depth > 0 or not self.main_seen,
            })
        elif tag == "iframe":
            self.iframes.append({"src": a.get("src", "")[:500], "title": a.get("title", "")})
        elif tag == "p":
            self.para_stack.append(len(self._sink()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def _unwind(self, index):
        """Pop stack[index:], reversing each entry's depth bump."""
        for entry in self.stack[index:]:
            if entry[2] == "boiler":
                self.boiler_depth = max(0, self.boiler_depth - 1)
            elif entry[2] == "main":
                self.main_depth = max(0, self.main_depth - 1)
        del self.stack[index:]

    def handle_endtag(self, tag):
        tag = tag.lower()
        a = {}
        matched = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                a = self.stack[i][1]
                self._unwind(i)
                matched = True
                break

        if tag == "script":
            if self.in_jsonld:
                self.jsonld_raw.append("".join(self.jsonld_buf))
                self.in_jsonld = False
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in SKIP_TEXT_TAGS:
            if tag == "noscript":
                self.in_noscript = False
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self.in_title = False
            return

        # Depth is now unwound in _unwind() from the stack entry's recorded bump, so no
        # decrement happens here. A stray end tag with no matching start (matched=False)
        # correctly changes nothing.

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.capture_head:
            text = re.sub(r"\s+", " ", "".join(self.head_buf)).strip()
            if text:
                self.headings.append({"level": self.capture_head, "text": text[:300], **(self.head_meta or {})})
            self.capture_head, self.head_buf, self.head_meta = None, [], None
        elif tag == "a" and self.capture_link:
            text = re.sub(r"\s+", " ", "".join(self.link_buf)).strip()
            if self.link_meta is not None:
                self.link_meta["text"] = text[:200]
                self.links.append(self.link_meta)
            self.capture_link, self.link_buf, self.link_meta = False, [], None
        elif tag == "p" and self.para_stack:
            start = self.para_stack.pop()
            sink = self._sink()
            words = len(" ".join(sink[start:]).split())
            if words:
                self.paragraph_words.append(words)

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld_buf.append(data)
            return
        if self.in_noscript:
            self.noscript_buf.append(data)
            return
        if self.skip_depth > 0:
            return
        if self.in_title:
            self.title += data
            return
        if self.capture_head is not None:
            self.head_buf.append(data)
        if self.capture_link:
            self.link_buf.append(data)
        self._sink().append(data)


# --------------------------------------------------------------------------- fetching


def base_host(host):
    """Strip a leading 'www.' label. (str.lstrip would eat characters, not the prefix.)"""
    return host[4:] if host.lower().startswith("www.") else host.lower()


def same_site(host, root_host):
    a, b = base_host(host), base_host(root_host)
    return a == b or a.endswith("." + b)


META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*['"]?refresh['"]?[^>]*content\s*=\s*['"]?\s*\d+\s*;\s*url=([^'"\s>]+)""",
    re.I)


def meta_refresh_target(html, base):
    """Return the URL a meta-refresh gateway page points at, if any.

    urllib follows HTTP 3xx but not meta refresh, and large brands often serve a near-empty
    gateway at the apex that refreshes to the real site. Auditing the gateway instead of the
    site produces a report about a page with no content, no markup and no links - technically
    accurate about the shim, useless about the brand.
    """
    m = META_REFRESH_RE.search(html[:4000])
    if not m:
        return None
    target = norm_url(m.group(1).strip("'\""), base)
    return target if target and target != norm_url(base) else None


HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+(:\d+)?$", re.I)


def sanitize_url(raw):
    """Clean a URL that arrived from a shell, a copy-paste or an agent.

    A trailing space survived into the hostname once and produced a report that blamed the
    site for three defects when the request had simply never been sent. URLs cannot contain
    raw whitespace, so all of it is removed rather than only trimmed, and the host is
    validated before any request is made.

    Returns (url, error). error is None when the URL is usable.
    """
    if not raw or not raw.strip():
        return None, "no URL given"
    cleaned = "".join(raw.split())                       # kills spaces, tabs, newlines
    cleaned = cleaned.strip("<>\"'\u201c\u201d\u2018\u2019")   # angle brackets and smart quotes
    cleaned = cleaned.rstrip(".,;")                      # trailing sentence punctuation
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    parts = urllib.parse.urlsplit(cleaned)
    if parts.scheme not in ("http", "https"):
        return None, f"unsupported scheme '{parts.scheme}' (only http and https are audited)"
    host = parts.netloc.split("@")[-1]
    if not host or not HOSTNAME_RE.match(host):
        return None, f"'{host or raw.strip()}' is not a valid hostname"
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                    parts.path or "/", "", "")), None


def wire_encode(url):
    """Percent-encode a URL's path/query and IDNA-encode its host.

    urllib cannot send a URL containing non-ASCII characters - it raises
    UnicodeEncodeError, which was swallowed and recorded as a failed fetch. Every
    accented or non-Latin URL therefore came back as a broken link: an encyclopedia's
    articles on Oma\u00f1a, C\u00e1diz and Wolfenb\u00fcttel were all reported dead. This affects any
    site whose URLs are not pure ASCII, which is most of the non-English web.

    '%' is kept safe so an already-encoded URL is not encoded twice.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    netloc = parts.netloc
    if any(ord(ch) > 127 for ch in netloc):
        host, sep, port = netloc.rpartition(":")
        if not sep or not port.isdigit():
            host, sep, port = netloc, "", ""
        userinfo, at, hostonly = host.rpartition("@")
        try:
            hostonly = hostonly.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            hostonly = urllib.parse.quote(hostonly, safe="")
        netloc = f"{userinfo}{at}{hostonly}{sep}{port}"
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
    query = urllib.parse.quote(parts.query, safe="%=&:@!$'()*+,;/?~-._")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, ""))


def norm_url(url, base=None):
    if base:
        url = urllib.parse.urljoin(base, url)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not TRACKING_PARAMS.match(k)]
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return wire_encode(urllib.parse.urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), path,
        urllib.parse.urlencode(sorted(query)), "",
    )))


def fetchable(url, root_host):
    parts = urllib.parse.urlsplit(url)
    if not same_site(parts.netloc, root_host):
        return False
    if SKIP_EXT.search(parts.path) or SKIP_PATTERNS.search(parts.path) or SKIP_QUERY.search(parts.query):
        return False
    return len(parts.path.strip("/").split("/")) <= 6


def http_get(url, timeout=8.0, ua=UA, method="GET", max_bytes=3_000_000):
    """Single read-only request. Returns a dict; never raises for HTTP status."""
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
        "Accept-Encoding": "gzip",
    })
    started = time.time()
    chain = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes)
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(max_bytes)
                except OSError:
                    pass
            charset = resp.headers.get_content_charset() or "utf-8"
            return {
                "url": url, "final_url": resp.url, "status": resp.status,
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "body": body.decode(charset, errors="replace"),
                "bytes": len(body), "elapsed_ms": int((time.time() - started) * 1000),
                "redirected": resp.url != url, "error": None, "redirect_chain": chain,
            }
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(200_000)
        except Exception:
            pass
        return {
            "url": url, "final_url": e.url if hasattr(e, "url") else url, "status": e.code,
            "headers": {k.lower(): v for k, v in (e.headers or {}).items()},
            "body": body.decode("utf-8", errors="replace"), "bytes": len(body),
            "elapsed_ms": int((time.time() - started) * 1000), "redirected": False,
            "error": None, "redirect_chain": chain,
        }
    except Exception as e:  # DNS, TLS, timeout, malformed response
        return {
            "url": url, "final_url": url, "status": 0, "headers": {}, "body": "",
            "bytes": 0, "elapsed_ms": int((time.time() - started) * 1000),
            "redirected": False, "error": f"{type(e).__name__}: {e}"[:200], "redirect_chain": chain,
        }


# --------------------------------------------------------------------------- page record


# Copula and "does what" verbs per language, for detecting a definitional sentence
# ("<Brand> is a <category> that ..."). Every text heuristic in this marketplace was
# originally English-only, which produced confident false positives on perfectly good
# non-English sites - a Spanish page reading "X es una panaderia artesanal en Madrid"
# was reported as having no definitional sentence at all.
#
# Where a language IS covered, the check runs. Where it is NOT, the result is None
# (unknown) rather than False, and the analyzer reports it as unchecked instead of
# asserting a defect it had no way to measure.
LANG_COPULA = {
    "en": r"is|are|was|provides?|offers?|builds?|makes?|helps?|serves?|specializ\w+|delivers?",
    "es": r"es|son|era|ofrece|ofrecemos|elabora|fabrica|ayuda|sirve|se\s+dedica|somos",
    "pt": r"é|são|era|oferece|fabrica|ajuda|serve|somos",
    "fr": r"est|sont|était|propose|offre|fabrique|aide|sert|sommes",
    "de": r"ist|sind|war|bietet|stellt|hilft|liefert|produziert",
    "it": r"è|sono|era|offre|produce|aiuta|serve|siamo",
    "nl": r"is|zijn|was|biedt|maakt|helpt|levert",
    "id": r"adalah|merupakan|menyediakan|membuat|membantu",
    "ms": r"adalah|merupakan|menyediakan|membuat|membantu",
    "pl": r"jest|są|oferuje|produkuje|pomaga",
    "sv": r"är|var|erbjuder|tillverkar|hjälper",
    "da": r"er|var|tilbyder|laver|hjælper",
    "nb": r"er|var|tilbyr|lager|hjelper",
    "no": r"er|var|tilbyr|lager|hjelper",
    "tr": r"bir|olan|sunar|üretir|yardım",
}


def lang_code(html_lang):
    """Primary subtag of an html lang attribute: 'es-419' -> 'es'. Empty when absent."""
    return (html_lang or "").strip().lower().split("-")[0]


STOPWORD_TOKENS = {"www", "com", "the", "inc", "ltd", "llc", "home", "official", "site", "app", "co"}


def brand_tokens_from(*sources):
    tokens = set()
    for src in sources:
        if not src:
            continue
        for tok in re.split(r"[^a-z0-9]+", str(src).lower()):
            if len(tok) >= 3 and tok not in STOPWORD_TOKENS and not tok.isdigit():
                tokens.add(tok)
    return sorted(tokens)[:6]


# Leading and trailing furniture that page titles wrap around a brand name. Left in
# place, a title like "Welcome to <Site>" became the brand, which then appeared inside the
# definitional-sentence template ("'Welcome to <Site> is a <category> that...'") and in
# every verification search the report emits.
TITLE_PREFIX = re.compile(
    r"^(?:welcome\s+to|welkom\s+bij|bienvenido[sa]?\s+a|bienvenue\s+(?:sur|a|à)|willkommen\s+(?:bei|auf)|"
    r"benvenut[oi]\s+(?:su|a)|bem[-\s]vind[oa]\s+a[o]?|"
    r"the\s+official\s+(?:website|site)\s+of|official\s+(?:website|site)\s+of|"
    r"home\s*(?:page)?\s*[-|:\u2013\u2014]|homepage\s*[-|:\u2013\u2014])\s*",
    re.I)
TITLE_SUFFIX = re.compile(
    r"\s*[-|:\u2013\u2014]?\s*(?:home\s*(?:page)?|homepage|official\s+(?:website|site)|"
    r"welcome|inicio|accueil|startseite)\s*$", re.I)


def clean_brand_segment(seg):
    """Strip title furniture from a candidate brand string."""
    prev = None
    out = (seg or "").strip()
    while out and out != prev:            # furniture can nest: "Welcome to X - Home"
        prev = out
        out = TITLE_PREFIX.sub("", out).strip()
        out = TITLE_SUFFIX.sub("", out).strip()
        out = out.strip(" -|:\u2013\u2014\u00b7")
    return out


def brand_name_from(title, og_site_name, label):
    """Best display name for the brand, used in search queries.

    Prefer og:site_name. Otherwise pick the title segment that actually matches the domain
    label, because a title is as often "Brand - tagline" as "Page | Brand" and taking a
    fixed end of it picks the tagline half the time.
    """
    if og_site_name and og_site_name.strip():
        cleaned = clean_brand_segment(og_site_name)
        if cleaned:
            return cleaned[:80]
    flat = re.sub(r"[^a-z0-9]", "", label.lower())
    segments = [seg.strip() for seg in re.split(r"[|\u2013\u2014\u00b7]|\s-\s", title or "") if seg.strip()]
    for seg in segments:
        cleaned = clean_brand_segment(seg)
        if cleaned and flat and flat in re.sub(r"[^a-z0-9]", "", cleaned.lower()):
            return cleaned[:80]
    for seg in segments:
        cleaned = clean_brand_segment(seg)
        if cleaned:
            return cleaned[:80]
    return label.title()


# Paths under these roots are never commerce pages even if a later segment says
# "product" - a company's internal playbook at /handbook/product/metrics is not a
# product listing. Checked before the product-path test, not after, so a document
# root always wins over a substring match deeper in the same path.
NON_COMMERCE_ROOTS = re.compile(
    r"(?:^|/)(?:handbook|docs?|documentation|help|kb|guide|guides|blog|news|wiki|"
    r"internal|support|community|changelog|tutorial|tutorials)(?:/|$)")


# Common documentation/support/blog subdomains, keyed by their leading label. When the
# PATH gives no signal (e.g. a subdomain root, or a path segment the path-based rules
# below do not recognise), the host itself is often the clearest signal available -
# "docs.example.com/changelog" is a docs page even though "changelog" matches nothing.
# Legal/policy documents are frequently published inside a help centre with a numeric
# ticket prefix ("/hc/en-us/articles/115014893428-Terms-of-Service"), so the document name
# is buried in a slug rather than occupying its own path segment. Matching only whole
# segments classified those as documentation, which then produced advice to add FAQPage
# markup to a Terms of Service page.
# Anchors used as buttons. A modal close, a consent choice or a carousel arrow has no
# destination by design, so counting them as "navigation pointing at nothing" turned a
# cookie banner into a high-severity discoverability finding. Only links whose label reads
# like a place count as a missing destination.
UI_CONTROL_LABEL = re.compile(
    r"^(?:accept(?:\s+all)?|reject(?:\s+all)?|decline|allow(?:\s+all)?|deny|agree|disagree"
    r"|close(?:\s+\w+)?|dismiss|cancel|ok|okay|done|back|next|previous|prev|continue|skip"
    r"|skip\s+to\s+.*|menu|close\s+menu|open\s+menu|toggle.*|expand.*|collapse.*|show\s+\w+"
    r"|hide\s+\w+|more|less|read\s+more|view\s+more|load\s+more|submit|send|save|search"
    r"|clear|reset|apply|filter.*|sort.*|share.*|print|copy|download|play|pause|stop|mute"
    r"|unmute|zoom.*|select.*|choose.*|edit|remove|delete|add|\+|-|x|\u00d7|\u2715|\u2716"
    r"|cookie\s+settings|cookie\s+preferences|manage\s+(?:cookies|preferences|settings)"
    r"|privacy\s+(?:choices|settings|preferences)|your\s+privacy\s+choices"
    r"|consent.*|preferences|settings|opt[\s-]?out|do\s+not\s+sell.*"
    r"|\d+|slide\s*\d*|page\s*\d*)$", re.I)


LEGAL_SLUG = re.compile(
    r"(?:^|[/\-_])(?:"
    r"terms(?:[-_](?:of[-_](?:service|sale|use)|and[-_]conditions))?"
    r"|privacy(?:[-_](?:policy|notice|statement))?"
    r"|cookie[-_]?(?:policy|notice|preferences)"
    r"|copyright(?:[-_]notice)?"
    r"|legal(?:[-_]notice)?"
    r"|disclaimer|accessibility(?:[-_]statement)?"
    r"|cookies|licence|license|licencing|licensing"
    r"|gdpr|ccpa|dmca|eula"
    r")(?:$|[-_/.])", re.I)

HOST_HINTS = {
    "docs": "docs", "documentation": "docs", "developer": "docs", "developers": "docs",
    "help": "docs", "support": "docs", "kb": "docs", "learn": "docs", "guides": "docs",
    "blog": "article", "news": "article", "press": "article",
    "status": "other", "community": "other", "forum": "other",
}


def classify(url, parsed, jsonld_types, text, root_host=None):
    parts = urllib.parse.urlsplit(url)
    path = parts.path.lower().strip("/")
    host = parts.netloc.lower()
    types = {t.lower() for t in jsonld_types}
    is_root_host = root_host is not None and base_host(host) == base_host(root_host)
    host_label = base_host(host).split(".")[0] if not is_root_host else ""
    host_hint = HOST_HINTS.get(host_label)

    # A query string makes a distinct page even when the path is empty: "/?search=Artemis"
    # is a search-results page, not the homepage. Treating those as home put six search
    # pages into the identity checks as if each were a separate homepage.
    if not path and parts.query:
        q = parts.query.lower()
        return "search" if re.search(r"(?:^|&)(?:q|s|query|search|keyword|term)=", q) else "other"

    if not path:
        # A root path is "home" ONLY on the site's own root host. The same empty path on
        # a subdomain (docs.example.com/, blog.example.com/) is that subdomain's landing
        # page, not the brand's homepage - conflating the two let a docs subdomain's root
        # outrank and displace the real homepage from a sample in practice, because both
        # matched the same one-slot "home" bucket and the docs host happened to sort first.
        if is_root_host:
            return "home"
        return host_hint or "other"
    # Checked before host hints and before the docs rule, so a policy document filed under
    # help.example.com is not treated as documentation.
    if LEGAL_SLUG.search("/" + path):
        return "legal"
    segments = path.split("/")
    non_commerce = bool(NON_COMMERCE_ROOTS.search("/" + path))
    if types & {"product", "productgroup", "offer"}:
        return "product"
    # Match the LEADING path segment only ("/product/x", "/shop/x"), never a substring
    # anywhere in the path - that is what let "/handbook/product/metrics" get classified
    # as a product page. A non-commerce root always overrides even a leading match.
    if not non_commerce and segments and segments[0] in ("product", "products", "shop", "item", "p"):
        return "product"
    # Every remaining pattern is anchored at both ends of the path segment ("(?:/|$)"
    # after the alternation), the same fix as the product check above. Without it,
    # "pricing" would match inside "/repricing-policy" and "team" inside "/steamworks".
    if re.search(r"(?:^|/)(?:pricing|plans|price)(?:/|$)", path):
        return "pricing"
    if types & {"article", "blogposting", "newsarticle", "techarticle"} or re.search(
            r"(?:^|/)(?:blog|news|article|posts?|insights|resources|stories)(?:/|$)", path):
        return "article"
    # NON_COMMERCE_ROOTS exists to stop "/handbook/product/x" being read as a product page.
    # It must NOT gate this rule: "docs" is itself in that list, so gating here meant a
    # path-based documentation page ("/docs/getting-started") could never be classified as
    # docs and fell through to "other" - silently disabling the FAQPage/HowTo expectations
    # for every documentation site organised by path rather than by subdomain.
    if (re.search(r"(?:^|/)(?:docs?|documentation|guide|guides|support|help|kb|faq)(?:/|$)", path)
            and not re.search(r"(?:^|/)handbook(?:/|$)", path)) or "faqpage" in types:
        return "docs"
    if re.search(r"(?:^|/)(?:about|company|team|who-we-are|our-story)(?:/|$)", path):
        return "about"
    if re.search(r"(?:^|/)(?:contact|locations?|stores?|find-us)(?:/|$)", path):
        return "contact"
    if re.search(r"(?:^|/)(?:careers?|jobs)(?:/|$)", path):
        return "careers"
    if re.search(r"(?:^|/)(?:privacy|terms|legal|cookie)(?:/|$)", path):
        return "legal"
    if re.search(r"(?:^|/)handbook(?:/|$)", path):
        return "other"
    if host_hint:
        return host_hint
    return "other"


def jsonld_objects(raw_blocks, errors):
    out = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except json.JSONDecodeError as e:
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                out.append(node)
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
    return out


def type_names(objs):
    names = []
    for o in objs:
        t = o.get("@type")
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, list):
            names.extend(str(x) for x in t)
    return names


def definitional_state(first_chunk, brand_tokens, html_lang):
    """True / False / None for "does this text define the brand?".

    None means the page's language has no lexicon here, so the question was not asked.
    Returning False in that case would be an assertion the collector cannot support.
    """
    if not brand_tokens:
        return None
    code = lang_code(html_lang)
    # An undeclared language is treated as English: that is the overwhelming default for
    # pages with no lang attribute, and the check degrades to a missed finding, not a
    # fabricated one, if the guess is wrong.
    verbs = LANG_COPULA.get(code or "en")
    if not verbs:
        return None
    brand = "|".join(re.escape(t) for t in brand_tokens)
    return bool(re.search(r"\b(?:%s)\b[^.]{0,90}?(?:\b|\s)(?:%s)(?:\b|\s)" % (brand, verbs),
                          first_chunk, re.I))


def build_record(res, root_host, brand_tokens):
    html = res["body"]
    parser = PageParser()
    record = {
        "url": res["url"], "final_url": res["final_url"], "status": res["status"],
        "elapsed_ms": res["elapsed_ms"], "bytes": res["bytes"], "error": res["error"],
        "redirected": res["redirected"],
        "content_type": res["headers"].get("content-type", ""),
        "headers": {k: v for k, v in res["headers"].items() if k in (
            "x-robots-tag", "cache-control", "content-language", "vary", "server",
            "content-encoding", "cf-mitigated", "cf-ray", "content-security-policy",
        )},
    }
    if res["status"] == 0 or "html" not in record["content_type"].lower() and html.strip()[:1] != "<":
        record.update({"parse_ok": False, "page_class": "unparsed"})
        return record

    try:
        parser.feed(html)
        parser.close()
    except Exception as e:  # never let one malformed page kill the run
        record.update({"parse_ok": False, "parse_error": f"{type(e).__name__}: {e}"[:200], "page_class": "unparsed"})
        return record

    text_main = re.sub(r"\s+", " ", "".join(parser.text_main if parser.main_seen else parser.text_other)).strip()
    text_boiler = re.sub(r"\s+", " ", "".join(parser.text_boiler)).strip()
    text_all = re.sub(r"\s+", " ", "".join(parser.text_main + parser.text_other + parser.text_boiler)).strip()
    if parser.main_seen and len(text_main.split()) < 40:
        # <main> present but nearly empty - fall back so we do not under-report content.
        text_main = re.sub(r"\s+", " ", "".join(parser.text_main + parser.text_other)).strip()

    errors = []
    objs = jsonld_objects(parser.jsonld_raw, errors)
    types = type_names(objs)

    metas = {}
    for m in parser.metas:
        key = m["name"] or m["property"]
        if key and key not in metas:
            metas[key] = m["content"]
    canonical = ""
    hreflangs = []
    for lr in parser.link_rels:
        rels = lr["rel"].split()
        if "canonical" in rels and not canonical:
            canonical = lr["href"]
        if "alternate" in rels and lr["hreflang"]:
            hreflangs.append(lr["hreflang"])

    internal, external, dead = [], [], []
    for link in parser.links:
        href = link.get("href", "").strip()
        # A nav item wired to javascript:void(0) or a bare "#" is not a link a fetcher can
        # follow, so the page behind it does not exist for any crawler. These were silently
        # discarded, which meant a site whose entire services menu was javascript:void(0)
        # reported no link problem at all - the destinations simply never appeared.
        # In-page fragments (#section) and mailto:/tel: are legitimate and are not counted.
        if not href or href == "#" or href.lower().startswith("javascript:"):
            label = " ".join((link.get("text") or "").split())
            # An unlabelled anchor is almost always an icon button; a control label is a
            # button too. Neither is a destination that went missing.
            if label and not UI_CONTROL_LABEL.match(label):
                dead.append(label[:60])
            continue
        if href.startswith(("#", "mailto:", "tel:")):
            continue
        absolute = norm_url(href, res["final_url"])
        if not absolute:
            continue
        entry = {**link, "abs": absolute}
        (internal if same_site(urllib.parse.urlsplit(absolute).netloc, root_host) else external).append(entry)

    words = text_main.split()
    brand_hits = sum(text_main.lower().count(tok) for tok in brand_tokens) if brand_tokens else 0
    heads = parser.headings
    first_chunk = " ".join(words[:220])

    record.update({
        "parse_ok": True,
        "title": re.sub(r"\s+", " ", parser.title).strip()[:300],
        "html_lang": parser.html_lang,
        "meta_description": metas.get("description", "")[:400],
        "meta_robots": metas.get("robots", ""),
        "og": {k: v for k, v in metas.items() if k.startswith("og:")},
        "twitter": {k: v for k, v in metas.items() if k.startswith("twitter:")},
        "viewport": metas.get("viewport", ""),
        "canonical": canonical,
        "hreflangs": sorted(set(hreflangs)),
        "jsonld_count": len(objs),
        "jsonld_types": types,
        "jsonld_objects": objs[:40],
        "jsonld_errors": errors,
        "microdata_types": parser.microdata_types[:20],
        "headings": heads[:120],
        "h1": [h["text"] for h in heads if h["level"] == 1],
        "heading_ids": sum(1 for h in heads if h.get("id")),
        "question_headings": sum(1 for h in heads if QUESTION_HEAD_RE.search(h["text"])),
        "links_internal": len(internal),
        "dead_links": len(dead),
        "dead_link_labels": sorted(set(dead))[:12],
        "links_external": len(external),
        "links_internal_main": sum(1 for l in internal if not l["in_boiler"]),
        "external_domains": sorted({urllib.parse.urlsplit(l["abs"]).netloc.lower() for l in external})[:60],
        "internal_targets": sorted({l["abs"] for l in internal})[:300],
        "generic_link_text": sum(1 for l in internal + external
                                 if l.get("text", "").strip().lower() in
                                 ("click here", "here", "read more", "learn more", "more", "link", "this")),
        "images_total": len(parser.images),
        "images_missing_alt": sum(1 for i in parser.images if i["alt"] is None or not i["alt"].strip()),
        "images_in_main": sum(1 for i in parser.images if i["in_main"]),
        "images_no_dims": sum(1 for i in parser.images if not i["width"] or not i["height"]),
        "images_lazy": sum(1 for i in parser.images if i["loading"] == "lazy"),
        "iframes": parser.iframes[:20],
        "counts": parser.counts,
        "form_field_types": parser.form_field_types[:30],
        "aria_controls": parser.aria_controls,
        "has_breadcrumb": bool(re.search(r"breadcrumb", html, re.I)) or "breadcrumblist" in " ".join(types).lower(),
        "ids_in_body": parser.ids_in_body,
        "word_count": len(words),
        "boiler_words": len(text_boiler.split()),
        "text_ratio": round(len(text_all) / max(1, len(html)), 4),
        "paragraph_words": parser.paragraph_words[:200],
        "long_paragraphs": sum(1 for w in parser.paragraph_words if w > 120),
        "noscript_words": len(" ".join(parser.noscript_buf).split()),
        "framework": sorted(k for k, rx in FRAMEWORK_SIGNS.items() if rx.search(html)),
        "spa_shell": bool(SPA_SHELL_RE.search(html)),
        "modal_hints": len(MODAL_HINT_RE.findall(html)),
        "auto_open_hints": bool(AUTOOPEN_RE.search(html)),
        "chat_widget": bool(CHAT_RE.search(html)),
        "currency_mentions": len(CURRENCY_RE.findall(text_main)),
        "price_numbers": sorted({re.sub(r"[^\d.]", "", m) for m in CURRENCY_RE.findall(text_main)} - {""})[:20],
        "visible_dates": sorted(set(DATE_RE.findall(text_all)))[:20],
        "copyright_years": sorted({m for m in COPYRIGHT_RE.findall(text_all)})[:5],
        "superlatives": len(SUPERLATIVE_RE.findall(text_main)),
        "specific_facts": len(SPECIFIC_FACT_RE.findall(text_main)),
        "brand_mentions": brand_hits,
        "brand_in_first_chunk": bool(brand_tokens) and any(t in first_chunk.lower() for t in brand_tokens),
        "definitional_sentence": definitional_state(first_chunk, brand_tokens, parser.html_lang),
        "lang_lexicon": lang_code(parser.html_lang) in LANG_COPULA or not parser.html_lang,
        "text_main_sample": text_main[:1500],
        "first_chunk": first_chunk,
        "video_without_track": max(0, parser.counts["video"] - parser.counts["track"]),
        "html_bytes": len(html),
    })
    record["page_class"] = classify(res["url"], record, types, text_main, root_host)
    record["boiler_ratio"] = round(record["boiler_words"] / max(1, record["boiler_words"] + record["word_count"]), 3)
    return record


# --------------------------------------------------------------------------- discovery


def parse_sitemap(text):
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
    lastmods = re.findall(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", text, re.I)
    is_index = "<sitemapindex" in text.lower()
    return locs, lastmods, is_index


# Sitemaps are plain-text XML and can legitimately run to tens of megabytes on a large
# site - a partition file with hundreds of thousands of URLs is normal. The default
# http_get cap (3MB, sized for HTML pages) was silently truncating these mid-element on
# real large sites, which cuts a <url> block in half and drops every entry after the cut
# with no signal that it happened - the discovery count just looked smaller than it was.
SITEMAP_MAX_BYTES = 25_000_000


def gather_sitemap_urls(origin, robots, timeout, log):
    candidates = list(dict.fromkeys(robots.sitemaps + [
        urllib.parse.urljoin(origin, "/sitemap.xml"),
        urllib.parse.urljoin(origin, "/sitemap_index.xml"),
    ]))
    found, entries, seen = [], [], set()
    queue = candidates[:6]
    truncated_any = False
    while queue and len(entries) < 20000:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        res = http_get(sm, timeout=timeout, max_bytes=SITEMAP_MAX_BYTES)
        # A response that lands exactly on the byte cap almost certainly means more bytes
        # existed and were cut off - report it rather than silently parsing a partial file.
        truncated = res["bytes"] >= SITEMAP_MAX_BYTES
        truncated_any = truncated_any or truncated
        record = {"url": sm, "status": res["status"], "bytes": res["bytes"],
                  "declared_in_robots": sm in robots.sitemaps, "error": res["error"],
                  "truncated": truncated}
        if res["status"] == 200 and ("<urlset" in res["body"].lower() or "<sitemapindex" in res["body"].lower()):
            locs, lastmods, is_index = parse_sitemap(res["body"])
            record.update({"entries": len(locs), "is_index": is_index,
                           "lastmods": sorted(set(lastmods))[-5:] if lastmods else []})
            if is_index:
                queue.extend(locs[:5])
            else:
                entries.extend(locs)
        else:
            record["entries"] = 0
        found.append(record)
        log.append(f"sitemap {sm} -> {res['status']} ({record.get('entries', 0)} urls)"
                   + (" [TRUNCATED at byte cap]" if truncated else ""))
    if truncated_any:
        log.append(f"one or more sitemap files were truncated at the {SITEMAP_MAX_BYTES // 1_000_000}MB fetch "
                   "cap; the discovered URL count is a lower bound, not a full count")
    return found, entries


MAX_EXPAND_FETCHES = 6   # hard ceiling on extra fetches spent purely on link discovery


def delay_for(robots):
    """Politeness delay for discovery fetches, honouring Crawl-delay up to 2s."""
    _, group = robots.group_for(UA)
    if group and group.get("crawl_delay"):
        return min(float(group["crawl_delay"]), 2.0)
    return 0.4


def select_urls(candidates, max_pages, root_host):
    """Deterministic, class-balanced sample. Sorted input -> sorted output."""
    quotas = {"home": 1, "pricing": 2, "product": 5, "article": 5, "docs": 4,
              "about": 2, "contact": 1, "careers": 1, "other": 6, "legal": 0}
    buckets = {}
    for url in sorted(set(candidates)):
        cls = classify(url, {}, [], "", root_host)
        buckets.setdefault(cls, []).append(url)
    for cls in buckets:
        buckets[cls].sort(key=lambda u: (len(urllib.parse.urlsplit(u).path.strip("/").split("/")), u))

    root_base = base_host(root_host) if root_host else None

    def host_of(url):
        return base_host(urllib.parse.urlsplit(url).netloc)

    # A single subdomain (docs.*, help.*) can hold many short, category-less paths that
    # all land in the same bucket - "other" in particular, since paths like /changelog or
    # /get-started match no content-type regex. Left uncapped, one subdomain observed to
    # fill 6 of 20 total slots in a real audit, crowding out sampling diversity across the
    # rest of the site. The root host itself is never capped: it is the primary target.
    host_cap = max(3, max_pages // 4)

    def take(urls, limit):
        picked, per_host = [], {}
        for url in urls:
            if len(picked) >= limit:
                break
            h = host_of(url)
            if h != root_base:
                if per_host.get(h, 0) >= host_cap:
                    continue
                per_host[h] = per_host.get(h, 0) + 1
            picked.append(url)
        return picked

    chosen = []
    for cls in ("home", "pricing", "product", "article", "docs", "about", "contact", "other", "careers", "legal"):
        chosen.extend(take(buckets.get(cls, []), quotas.get(cls, 2)))
    if len(chosen) < max_pages:
        per_host_total = {}
        for url in chosen:
            h = host_of(url)
            if h != root_base:
                per_host_total[h] = per_host_total.get(h, 0) + 1
        for cls in sorted(buckets):
            for url in buckets[cls]:
                if url in chosen:
                    continue
                h = host_of(url)
                if h != root_base and per_host_total.get(h, 0) >= host_cap:
                    continue
                chosen.append(url)
                if h != root_base:
                    per_host_total[h] = per_host_total.get(h, 0) + 1
                if len(chosen) >= max_pages:
                    break
            if len(chosen) >= max_pages:
                break
    return chosen[:max_pages]


# --------------------------------------------------------------------------- probes


def probe_word_count(html):
    """Rough word count of an HTML body, for telling an interstitial from a real page."""
    if not html:
        return 0
    try:
        parser = PageParser()
        parser.feed(html[:200_000])
        parser.close()
        return len(" ".join(parser.text_main + parser.text_boiler + parser.text_other).split())
    except Exception:
        return len(re.sub(r"<[^>]+>", " ", html[:200_000]).split())


def run_probes(origin, root_host, robots, timeout, log):
    parts = urllib.parse.urlsplit(origin)
    probes = {}

    home = http_get(origin, timeout=timeout)
    probes["home"] = {"status": home["status"], "elapsed_ms": home["elapsed_ms"],
                      "final_url": home["final_url"], "bytes": home["bytes"], "error": home["error"],
                      "cf_mitigated": home["headers"].get("cf-mitigated", ""),
                      # Word count lets the analyzer tell an interstitial from an article.
                      # A real challenge page is nearly empty; a 200 response full of text is
                      # not a wall no matter what words happen to appear in it.
                      "body_words": probe_word_count(home["body"]),
                      # Markers narrowed to challenge-specific strings. "captcha" and "access
                      # denied" occur in ordinary prose - on an encyclopedia they appear in
                      # article text - and matching them flagged the most crawler-friendly
                      # site on the web as running a bot wall, at HIGH severity.
                      "challenge_markers": bool(re.search(
                          r"just a moment\.\.\.|checking your browser|cf-browser-verification|"
                          r"cf[-_]chl|_cf_chl_opt|please enable (?:js|javascript) (?:and cookies )?to continue|"
                          r"ddos protection by|attention required!|"
                          r"verifying you are human|security check to access",
                          home["body"][:8000], re.I))}

    # HTTP -> HTTPS and www / apex consistency (duplication + reachability signals).
    if parts.scheme == "https":
        insecure = http_get(urllib.parse.urlunsplit(("http", parts.netloc, "/", "", "")), timeout=timeout)
        probes["http_scheme"] = {"status": insecure["status"], "final_url": insecure["final_url"],
                                 "upgrades_to_https": insecure["final_url"].startswith("https://")}
    alt_host = root_host[4:] if root_host.startswith("www.") else "www." + root_host
    alt = http_get(urllib.parse.urlunsplit((parts.scheme, alt_host, "/", "", "")), timeout=timeout)
    probes["host_variant"] = {"host": alt_host, "status": alt["status"], "final_url": alt["final_url"],
                              "canonicalizes": urllib.parse.urlsplit(alt["final_url"]).netloc.lower() == root_host}

    # Soft-404 detection: a URL that should not exist.
    ghost = urllib.parse.urljoin(origin, "/brand-ai-readiness-audit-probe-404")
    g = http_get(ghost, timeout=timeout)
    probes["not_found"] = {"url": ghost, "status": g["status"], "soft_404": g["status"] == 200,
                           "body_words": len(g["body"].split()),
                           "has_links": g["body"].lower().count("<a ") if g["status"] in (200, 404) else 0}

    for name, path in (("llms_txt", "/llms.txt"), ("ai_txt", "/ai.txt"), ("security_txt", "/.well-known/security.txt")):
        allowed, _ = robots.allowed(UA, urllib.parse.urljoin(origin, path))
        if not allowed:
            probes[name] = {"status": None, "skipped": "robots"}
            continue
        r = http_get(urllib.parse.urljoin(origin, path), timeout=timeout)
        probes[name] = {"status": r["status"], "bytes": r["bytes"],
                        "looks_valid": r["status"] == 200 and "<html" not in r["body"][:400].lower()}
    log.append("probes complete")
    return probes


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="Collect a read-only site pack for the audit marketplace.")
    ap.add_argument("--url", required=True, help="Site root or any page on the site")
    ap.add_argument("--out", required=True, help="Output pack directory")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--budget", type=float, default=240.0, help="Wall-clock seconds before graceful stop")
    ap.add_argument("--delay", type=float, default=0.4, help="Politeness delay per request per worker")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-raw", action="store_true", help="Store raw HTML bodies in the pack")
    args = ap.parse_args()

    t0 = time.time()
    log = []
    start_url, url_error = sanitize_url(args.url)
    if url_error:
        print(json.dumps({"status": "bad_url", "error": url_error, "given": args.url}), file=sys.stderr)
        return 4
    parts = urllib.parse.urlsplit(start_url)
    root_host = parts.netloc.lower()
    origin = urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "/", "", ""))
    registrable = base_host(root_host)
    domain_label = registrable.split(".")[0]
    brand_tokens = brand_tokens_from(domain_label)
    brand_name = domain_label.title()

    os.makedirs(args.out, exist_ok=True)
    if args.keep_raw:
        os.makedirs(os.path.join(args.out, "raw"), exist_ok=True)

    # 1. robots.txt
    robots_res = http_get(urllib.parse.urljoin(origin, "/robots.txt"), timeout=args.timeout)
    robots_text = robots_res["body"] if robots_res["status"] == 200 else ""
    if robots_res["status"] == 200 and "<html" in robots_text[:500].lower():
        robots_text = ""  # served an HTML page, not a robots file
    robots = Robots(robots_text)
    log.append(f"robots.txt -> {robots_res['status']} ({len(robots_text)} bytes)")

    self_allowed, self_rule = robots.allowed(UA, origin)
    if not self_allowed:
        meta = {
            "pack_version": VERSION, "site": registrable, "origin": origin,
            "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "aborted": "robots_disallow",
            "robots": {"status": robots_res["status"], "text": robots_text[:20000], "rule": self_rule,
                       "sitemaps": robots.sitemaps, "groups": robots.groups},
            "log": log,
        }
        with open(os.path.join(args.out, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        open(os.path.join(args.out, "pages.jsonl"), "w").close()
        print(json.dumps({"status": "blocked_by_robots", "rule": self_rule, "pack": args.out}))
        return 3

    # 2. discovery
    #
    # entry_url is the page we harvest links from; origin stays the HOST ROOT because
    # robots.txt, sitemaps and the probes are all host-level resources. A gateway can
    # refresh to a path on the same host ("/real/"), so collapsing the two would discard
    # the path and re-detect the same refresh forever.
    entry_url = origin
    home = http_get(entry_url, timeout=args.timeout)

    entry_redirects = []
    seen_entries = {norm_url(entry_url)}
    for _ in range(2):
        if home["status"] != 200 or not home["body"]:
            break
        target = meta_refresh_target(home["body"], home["final_url"])
        if not target or norm_url(target) in seen_entries:
            break
        ok, _rule = robots.allowed(UA, target)
        if not ok:
            log.append(f"meta refresh to {target} not followed (robots disallow)")
            break
        entry_redirects.append({"from": entry_url, "to": target, "kind": "meta-refresh"})
        log.append(f"entry page meta-refreshes to {target}; auditing that instead")
        entry_url = target
        seen_entries.add(norm_url(target))
        parts = urllib.parse.urlsplit(target)
        if base_host(parts.netloc) != base_host(root_host):
            # A cross-host gateway also moves the brand identity and the host-level resources.
            root_host = parts.netloc.lower()
            origin = urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "/", "", ""))
            registrable = base_host(root_host)
            domain_label = registrable.split(".")[0]
            brand_tokens = brand_tokens_from(domain_label)
            brand_name = domain_label.title()
        home = http_get(entry_url, timeout=args.timeout)

    # If the entry page could not be fetched at all (DNS failure, TLS error, connection
    # refused, timeout), we have no evidence about the site - only about our own request.
    # Continuing would emit findings like "robots.txt is misconfigured" and "no sitemap",
    # blaming the site for our inability to reach it. Stop and say so instead.
    if home["status"] == 0:
        print(json.dumps({
            "status": "unreachable", "origin": origin,
            "error": home.get("error") or "no response",
            "hint": "Check the hostname, your network, and whether the site blocks this client. "
                    "No findings are emitted because none could be observed.",
        }), file=sys.stderr)
        return 5

    sitemaps, sitemap_urls = gather_sitemap_urls(origin, robots, args.timeout, log)
    candidates = {norm_url(entry_url)}
    if home["status"] == 200:
        home_record = build_record(home, root_host, brand_tokens)
        # The brand is often named more precisely on the page than in the domain.
        brand_name = brand_name_from(home_record.get("title", ""),
                                     home_record.get("og", {}).get("og:site_name"), domain_label)
        # Domain-derived tokens stay first: they are the most reliable identifier we have.
        extra = [t for t in brand_tokens_from(brand_name) if t not in brand_tokens]
        brand_tokens = (brand_tokens + extra)[:6]
        home_record = build_record(home, root_host, brand_tokens)
        candidates.update(u for u in home_record.get("internal_targets", []) if fetchable(u, root_host))
    for u in sitemap_urls[:800]:
        n = norm_url(u)
        if n and fetchable(n, root_host):
            candidates.add(n)

    # Breadth-first expansion. Harvesting links from the homepage alone is enough for a
    # site with a sitemap, but a site with no sitemap and a shallow homepage yields almost
    # nothing - observed in practice as a 1-page sample against a --max-pages 100 budget,
    # with findings then computed over a sample of one. Expand one level further, bounded
    # by page count, fetch count and the wall-clock budget so this can never become a crawl.
    discovery_rounds = 1
    if len(candidates) < args.max_pages:
        frontier = [u for u in sorted(candidates) if u != norm_url(entry_url)][:MAX_EXPAND_FETCHES]
        expanded = 0
        for url in frontier:
            if len(candidates) >= args.max_pages or time.time() - t0 > args.budget * 0.5:
                break
            ok, _ = robots.allowed(UA, url)
            if not ok:
                continue
            time.sleep(delay_for(robots))
            res = http_get(url, timeout=args.timeout)
            expanded += 1
            if res["status"] != 200:
                continue
            rec = build_record(res, root_host, brand_tokens)
            candidates.update(u for u in rec.get("internal_targets", []) if fetchable(u, root_host))
        if expanded:
            discovery_rounds = 2
            log.append(f"expanded discovery: fetched {expanded} page(s) for links -> "
                       f"{len(candidates)} candidates")

    allowed_candidates = []
    blocked_by_robots = []
    for u in sorted(candidates):
        ok, rule = robots.allowed(UA, u)
        (allowed_candidates if ok else blocked_by_robots).append(u)

    selected = select_urls(allowed_candidates, args.max_pages, root_host)
    log.append(f"discovery: {len(candidates)} candidates, {len(selected)} selected")

    # 3. fetch (bounded, polite, deterministic output order)
    crawl_delay = None
    _, our_group = robots.group_for(UA)
    if our_group and our_group.get("crawl_delay"):
        crawl_delay = min(float(our_group["crawl_delay"]), 2.0)
    delay = max(args.delay, crawl_delay or 0.0)

    results = {}

    def worker(url):
        if time.time() - t0 > args.budget:
            return url, None
        time.sleep(delay)
        return url, http_get(url, timeout=args.timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for url, res in pool.map(worker, selected):
            if res is not None:
                results[url] = res

    records = []
    for url in selected:
        res = results.get(url)
        if res is None:
            continue
        rec = build_record(res, root_host, brand_tokens)
        # After a gateway redirect the effective homepage sits at a path, so the
        # empty-path rule in classify() would miss it and the home-specific checks
        # (Organization markup, definitional sentence) would never run.
        if norm_url(rec["url"]) == norm_url(entry_url) and rec.get("page_class") not in (None, "unparsed"):
            rec["page_class"] = "home"
        if args.keep_raw and res["body"]:
            digest = hashlib.sha1(url.encode()).hexdigest()[:16]
            with open(os.path.join(args.out, "raw", digest + ".html"), "w") as f:
                f.write(res["body"][:800_000])
            rec["raw_file"] = f"raw/{digest}.html"
        records.append(rec)

    # 4. probes + site typing
    probes = run_probes(origin, root_host, robots, args.timeout, log)
    # Type the site only from pages that were actually read. A URL's path alone is not
    # evidence of what a page contains, so a bot-walled site whose every response was a 403
    # challenge previously reported a confident page class and site type inferred from
    # nothing - the same absence-of-evidence error as claiming "no sitemap" when refused.
    parsed_records = [r for r in records if r.get("status") == 200 and r.get("parse_ok")]
    classes = [r.get("page_class", "unparsed") for r in parsed_records]
    all_types = {t.lower() for r in parsed_records for t in r.get("jsonld_types", [])}
    if not parsed_records:
        site_type = "unknown"
    elif "product" in classes or all_types & {"product", "offer"}:
        site_type = "ecommerce"
    elif "pricing" in classes and any(c in classes for c in ("docs", "other")):
        site_type = "saas"
    elif classes.count("article") >= 3:
        site_type = "publisher"
    elif "contact" in classes and all_types & {"localbusiness", "restaurant", "store"}:
        site_type = "local_business"
    elif "docs" in classes:
        site_type = "docs_or_support"
    else:
        site_type = "brand_or_marketing"

    meta = {
        "pack_version": VERSION,
        "collector": "crawl-access-audit/collect_site_pack.py",
        "site": registrable,
        "origin": origin,
        "entry_url": entry_url,
        "root_host": root_host,
        "brand_tokens": brand_tokens,
        "brand_name": brand_name,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aborted": None,
        "config": {"max_pages": args.max_pages, "timeout": args.timeout, "delay": delay,
                   "budget": args.budget,
                   "user_agent": UA, "rendered_capture": False},
        "robots": {
            "status": robots_res["status"], "bytes": len(robots_text),
            "text": robots_text[:20000], "sitemaps": robots.sitemaps,
            "groups": robots.groups, "self_allowed": self_allowed, "self_rule": self_rule,
            "agent_verdicts": agent_verdicts(robots, origin, selected),
        },
        "sitemaps": sitemaps,
        "sitemap_url_count": len(sitemap_urls),
        "sitemap_sample": sorted(sitemap_urls)[:50],
        "discovered": len(candidates),
        "discovery_rounds": discovery_rounds,
        "entry_redirects": entry_redirects,
        "blocked_by_robots_sample": blocked_by_robots[:25],
        "blocked_by_robots_count": len(blocked_by_robots),
        "selected": selected,
        "pages_fetched": len(records),
        "page_classes": sorted(set(classes)),
        "site_type": site_type,
        "probes": probes,
        "elapsed_s": round(time.time() - t0, 1),
        "budget_exhausted": (time.time() - t0) > args.budget,
        "log": log,
    }

    with open(os.path.join(args.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    with open(os.path.join(args.out, "pages.jsonl"), "w") as f:
        for rec in records:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    print(json.dumps({
        "status": "ok", "pack": args.out, "site": registrable, "site_type": site_type,
        "pages_fetched": len(records), "elapsed_s": meta["elapsed_s"],
    }))
    return 0 if records else 2


if __name__ == "__main__":
    sys.exit(main())
