---
name: crawl-access-audit
description: >
  Stage 1 of an AI-readiness audit: checks whether a machine can reach a site at all.
  Collects a read-only, robots-respecting sample of a website into a reusable site pack,
  then reports on robots.txt gating of AI crawlers (distinguishing training crawlers from
  retrieval and user-triggered fetchers), noindex/nosnippet directives, WAF bot walls,
  sitemap health, canonical and host duplication, broken pages and response latency. Use
  when auditing why AI assistants or search engines cannot fetch or index a site, when a
  brand is entirely absent from AI answers, when checking whether a site blocks GPTBot,
  ClaudeBot, PerplexityBot or Googlebot, or as the first stage of the
  brand-ai-readiness-audit marketplace. This skill owns all network access in the
  marketplace; every other skill reads the pack it produces.
license: MIT
allowed-tools: [Bash, Read, Write, WebFetch]
---

# Crawl and access audit (stage 1: reach)

If a fetcher is refused, redirected in circles, challenged, or told not to quote the page,
nothing later in the pipeline matters. This stage is checked first and fixed first.

## When to use

- As the first stage of the marketplace audit (the entrypoint calls it automatically).
- Standalone, when a brand is completely absent from AI answers rather than merely
  under-represented — that pattern usually resolves to an access problem.

## Inputs

A URL or domain. Optional: `--max-pages` (default 25), `--budget` seconds, `--timeout`,
`--keep-raw` to store HTML bodies.

## Procedure

1. **Collect.** Produces the site pack every other skill reads.

   ```bash
   python3 scripts/collect_site_pack.py --url https://example.com --out ./pack
   ```

   Fetches robots.txt, resolves sitemaps, discovers candidate URLs from the sitemap and
   homepage links, selects a class-balanced deterministic sample (home, product, pricing,
   article, docs, about, contact), fetches each politely, and normalises every page into
   `pages.jsonl`. Also runs probes: HTTP/HTTPS and www/apex behaviour, a soft-404 test,
   and `/llms.txt`. Exit code 3 means robots.txt disallows the audit — that is a finding,
   not an error.

2. **Analyze.**

   ```bash
   python3 scripts/check_access.py --pack ./pack --out findings-access.json
   ```

3. Read `references/access-checks.md` when you need a check's threshold or its
   false-positive gate, and `references/ai-crawler-registry.md` for what each agent does.

## The distinction this stage exists to make

Not every blocked crawler is a problem. Agents fall into three roles:

- **User-triggered fetchers** (ChatGPT-User, Claude-User, Perplexity-User) fetch a page
  *because a user just asked about you*. Blocking these is the most expensive mistake on
  the internet for a brand: the demand already exists and the answer arrives without you.
- **Retrieval crawlers** (OAI-SearchBot, Claude-SearchBot, PerplexityBot, Googlebot,
  Bingbot, Applebot) build the indexes assistants search before answering. Blocking these
  removes the brand from the candidate pool.
- **Training crawlers** (GPTBot, ClaudeBot, CCBot, Google-Extended, Applebot-Extended)
  feed model training. Blocking these is a legitimate policy choice with a real cost —
  the model is less likely to know the brand unprompted — but it is not a defect.

Report the first two as failures and the third as an observation with the tradeoff stated.
Treating an intentional training opt-out as a critical bug is the most common false
positive in this whole domain, and it destroys the credibility of the rest of the report.

## Without a shell

`references/access-checks.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Fetch `/robots.txt`, the sitemap(s), the homepage and a not-found URL. Evaluate robots per agent role, then read response headers and `<meta name="robots">` for indexing and snippet directives. Latency and byte-size checks need instrumentation and are skipped.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `ACC-*`, each carrying counts and sample size. See
`references/access-checks.md` for the catalogue.

## Guardrails

- GET and HEAD only. No forms, no POST, no cookies, no credentials, no JavaScript.
- robots.txt is parsed and obeyed for this auditor's own user-agent. There is no
  override flag: a disallow stops collection, full stop.
- URLs matching authenticated, transactional or destructive patterns (`/cart`, `/checkout`,
  `/login`, `/admin`, `/api`, `?add-to-cart=`) are never fetched.
- Third-party crawler user-agents are never impersonated. Impersonating ChatGPT-User to
  test access is dishonest and produces a worse signal than reading the rules directly.
- Politeness delay per request, `Crawl-delay` honoured up to 2s, bounded page count and
  wall-clock budget.
