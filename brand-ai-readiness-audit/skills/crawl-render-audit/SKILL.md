---
name: crawl-render-audit
description: >
  Audit a website for crawler reachability and render-path correctness.
  Checks robots.txt reachability & rules, sitemap presence & structure,
  meta robots tags, X-Robots-Tag headers, HTTP status of the homepage and
  internal links, redirect chain length, Content-Type charset, Strict-
  Transport-Security, viewport meta tag, SPA/SSR-vs-CSR patterns, and
  <head> integrity (meta charset, title count, base href). Designed as
  one focused sub-skill composed by the audit-orchestrator entrypoint.
license: MIT
tags: ["crawlability", "rendering", "robots.txt", "sitemap", "http", "seo"]
declared_tools:
  - shell:execute-read-only
  - python:execute-script
inputs:
  - name: url
    type: string
    required: true
    description: Homepage URL (absolute with scheme) to audit.
outputs:
  - name: findings
    type: application/json
    description: >
      JSON array of findings for this dimension only. Each finding follows
      the marketplace schema (id, title, severity, evidence, suggested_action).
---

# Crawl & Render Audit

## When to use

Invoke this skill **after the homepage has been fetched and parsed** as part
of a larger brand-audit flow. It produces a slice of findings covering the
first two failure modes of AI discovery: **the crawler is not let in**, and
**the crawler cannot read what is on the page**.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url` | yes | Absolute homepage URL including scheme, e.g. `https://example.com/` |

## Procedure

1. **robots.txt**
   - Fetch `/robots.txt` from the origin.
   - If it is unreachable or 404+: file `robots.txt is unreachable or missing`.
   - Parse it with a standards-compliant `RobotFileParser`.
   - If the wildcard `User-agent: *` block contains `Disallow: /`: file
     `robots.txt blocks all crawlers` (critical).
   - If `can_fetch(homepage)` is False for the auditor's UA: file
     `Homepage URL is blocked by robots.txt` (critical).
   - Check `Sitemap:` directives inside robots.txt as a secondary sitemap
     source (step 2).

2. **Sitemap(s)**
   - Try `/sitemap.xml`, then `/sitemap_index.xml`, then any `Sitemap:`
     URLs declared in robots.txt.
   - Classify as present when the response is <400 and the body contains a
     recognizable `<urlset>` element.
   - If no sitemap of any form resolves: file `No sitemap.xml is exposed`.
   - If a sitemap parses but contains fewer than 2 `<loc>` entries: file
     `Sitemap has very few URLs or is nearly empty`.

3. **Indexing directives on the homepage**
   - Parse `<meta name="robots" content="...">`.
     - Presence of `noindex` or `none`: file `Homepage meta robots blocks
       indexing/snippets` (critical).
     - Presence of `nofollow`: file `Homepage meta robots uses nofollow`
       (high).
   - Inspect the `X-Robots-Tag` response header.
     - Contains `noindex`/`none`: file `X-Robots-Tag blocks indexing`
       (critical).

4. **HTTP health**
   - On the homepage response:
     - `status >= 400` → `Homepage returns HTTP NNN` (critical).
     - `3xx` redirect → `Homepage issues HTTP NNN redirect` (low).
       Verify chain is ≤ 2 hops.
     - Response body empty / connection failed after retries →
       `Homepage is unreachable over HTTP(S)` (critical).
   - HTTPS but no `Strict-Transport-Security` → HSTS finding (low).
   - `Content-Type` header without `charset=` → charset finding (low).
   - Cross-page: count internal links whose GET returns `>= 400`; file
     broken-links finding (high) when any exist.

5. **Render-path correctness**
   - Inspect raw HTML for SPA-mount markers (`#app`, `data-reactroot`,
     `_NEXT/__NEXT_DATA__`, `ng-app`, `_nuxt`, `__INITIAL_STATE__`, etc.).
   - Compute statically-extracted body text length; compare with
     `<noscript>` fallback length.
   - When SPA markers are present **and** static body is <600 chars **and**
     `<noscript>` is <200 chars: file
     `Page body appears to be client-rendered without SSR/noscript fallback`
     (high).

6. **Head integrity**
   - No `<meta charset>`: file low-severity finding.
   - No `<title>` → high; >1 `<title>` → medium.
   - No `<meta name="viewport">` → medium.
   - `<base>` present without `href` → low.

## Output

A JSON array of findings, each with schema:

```json
{
  "id": "CR-###",
  "title": "...",
  "severity": "critical | high | medium | low",
  "evidence": "...",
  "suggested_action": { "summary": "...", "priority": "critical | high | medium | low" }
}
```

## Executable entry point

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python skills/crawl-render-audit/scripts/run.py https://example.com --pretty
```

The runner delegates to the shared engine's `audit_crawlability` and
`audit_render` modules for deterministic output.
