---
name: audit-orchestrator
description: >
  Entrypoint for the Brand AI-Readiness Audit marketplace. Given a website URL
  (domain or homepage), composes the five specialized audit skills —
  crawl-render-audit, structured-data-audit, content-accessibility-audit,
  freshness-entity-audit, and engagement-audit — into a single audit report.
  Produces the required fixed-schema JSON report (site, audited_at, summary,
  findings) ready for a non-expert to act on. Use as the top-level skill for
  diagnosing why a brand is not found, trusted, or repeated by AI assistants,
  and why visitors who arrive don't engage.
license: MIT
tags: ["audit", "ai-discoverability", "engagement", "entrypoint", "reporting"]
declared_tools:
  - shell:execute-read-only
  - file:read
  - python:execute-script
inputs:
  - name: url
    type: string
    required: true
    description: >
      Target website. Accepts bare domain ("example.com") or a full URL
      ("https://example.com/"). HTTPS is assumed when no scheme is given.
outputs:
  - name: report
    type: application/json
    description: >
      Audit report against the fixed schema defined in marketplace rules.
      Contains site, audited_at, summary (counts by severity), and findings.
---

# Audit Orchestrator (Entrypoint)

## When to use

Invoke this skill **once per website** whenever you need to produce a full
Brand AI-Readiness audit. It is the only skill the caller needs to invoke
directly; it is responsible for composing the five specialized skills and
their outputs into the single final report.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url` | yes      | Domain or homepage URL, e.g. `example.com` or `https://example.com/` |

## Procedure

1. **Normalize the input URL.** If no scheme is present, prefix with
   `https://`. Treat any trailing path as the homepage; the canonical origin
   is the scheme + host.

2. **Fetch and parse the homepage** using the shared auditor session
   (polite User-Agent, connection pooling, robots.txt-compliant, reads only).
   - Follow up to 5 redirects.
   - On final response, record HTTP status, headers, and the parsed DOM.

3. **Collect links and build a page sample.**
   - Extract all `<a href>` values from the homepage.
   - Resolve each to an absolute URL; filter to same-domain, robots-allowed,
     http(s)-scheme links; dedupe.
   - Pick up to `MAX_PAGES_CRAWLED` representative pages (homepage + a
     balanced mix of depth-1 sections and deepest content pages) for the
     cross-page sample.

4. **Invoke each specialized audit skill** against the homepage + sample.
   Each skill returns zero or more findings:
   - `crawl-render-audit` — crawler reachability, robots, sitemaps, HTTP
     health, meta robots, render-path (SSR vs CSR vs noscript), <head>
     integrity.
   - `structured-data-audit` — JSON-LD / microdata / RDFa coverage, required
     schema.org types (Organization, WebSite, BreadcrumbList, SearchAction,
     Product/Article), Open Graph, Twitter cards, rel=canonical.
   - `content-accessibility-audit` — image alt text, SVG titles, media
     captions, `<title>` and meta description quality, heading hierarchy
     (H1, skipped levels), text/HTML ratio, facts locked in non-text.
   - `freshness-entity-audit` — Last-Modified / ETag, timestamps in
     `<time datetime>` / OG article tags, copyright-year staleness,
     `sameAs` / Wikipedia / Wikidata cross-reference, `<html lang>`,
     hreflang self-reference.
   - `engagement-audit` — internal link counts & distribution, `<nav>`
     landmark, on-site search form + JSON-LD SearchAction, CTA density and
     placement, related/recent content blocks, pagination signals, footer
     richness, HTML payload size, resource hints.

5. **Run cross-page aggregations** across the internal-page sample:
   - Flag duplicate `<title>`, duplicate `meta description`, duplicate H1.
   - Flag % of sampled pages that lack meta description, lack rel=canonical,
     or carry no structured data at all.
   - Count internal links that reach HTTP 4xx/5xx (broken links).

6. **Deduplicate, sort, and renumber findings.**
   - Primary sort key: severity descending (critical → high → medium → low).
   - Secondary sort key: original skill order for stable, deterministic
     output.
   - Assign contiguous IDs `F-001, F-002, …`.

7. **Build severity summary.** Count findings per severity (critical, high,
   medium, low, info) and compute `total_findings`.

8. **Emit the report** as JSON matching exactly the required minimum schema
   (fields below) plus any extra fields.

## Output

The output is a single JSON document with this shape:

```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": {
    "total_findings": 6,
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 0,
    "info": 0
  },
  "findings": [
    {
      "id": "F-001",
      "title": "Short human-readable title of the problem",
      "severity": "critical | high | medium | low | info",
      "evidence": "Concrete, reproducible evidence — counts, URLs, exact attribute values.",
      "suggested_action": {
        "summary": "Specific, mechanism-sound, actionable fix. May include code snippets.",
        "priority": "critical | high | medium | low"
      }
    }
  ]
}
```

## Declared tools & safety

- **Read-only.** This skill never mutates a live site, never sends
  authenticated requests, never submits forms with data.
- **robots.txt honored** for every fetch via `urllib.robotparser`.
- **Rate-limiting.** No concurrent requests; sequential with mild retry
  backoff only on transient failures.
- **No third-party paid APIs**; the entire audit is performed with open
  HTTP fetches from the caller's environment.

## How to invoke (executable)

The bundled `scripts/run_audit.py` script is the single executable entry
point. Python 3.10+ is required. Dependencies: `requests`, `beautifulsoup4`,
`lxml`.

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python  skills/audit-orchestrator/scripts/run_audit.py example.com --pretty
```

Exit code `0` on success (report is always produced; even an unreachable
site yields a severity-appropriate finding). Exit `2` on usage errors.
