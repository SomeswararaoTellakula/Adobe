# Brand AI-Readiness Audit — Agent Skill Marketplace

> **Adobe University Hackathon 2026 · Round 3 submission.**
> Audits any website for both (1) **AI discoverability** — why the brand isn't
> found, trusted, or repeated by AI assistants — and (2) **on-site engagement**
> — why visitors who arrive don't stay. Emits a single, actionable JSON report
> with evidence-backed findings and prioritized fixes.

## Quick start

```bash
# 1. Install
pip install -r skills/audit-orchestrator/scripts/requirements.txt

# 2. Run (entrypoint skill)
python skills/audit-orchestrator/scripts/run_audit.py example.com --pretty -o report.json
```

Output always matches the required minimum schema:

```jsonc
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": { "total_findings": 6, "critical": 1, "high": 2, "medium": 3 },
  "findings": [
    {
      "id": "F-001",
      "title": "…",
      "severity": "critical|high|medium|low|info",
      "evidence": "concrete reproducible evidence",
      "suggested_action": { "summary": "specific fix", "priority": "critical|high|medium|low" }
    }
  ]
}
```

## Marketplace composition

The marketplace is composed of 6 skills. **`audit-orchestrator` is the single
entrypoint** (`marketplace.json` → `entrypoint: true`). It composes the other
five focused skills into the final audit report. Each skill is individually
`agentskills.io`-compliant (YAML frontmatter + deterministic procedure).

| Skill folder path                  | id                      | Role                                                                 |
|------------------------------------|-------------------------|----------------------------------------------------------------------|
| `skills/audit-orchestrator/`       | `audit-orchestrator`    | **Entrypoint.** Normalizes URL, samples internal pages, runs all 5 sub-skills, deduplicates + renumbers findings, emits the final JSON report. |
| `skills/crawl-render-audit/`       | `crawl-render-audit`    | **Crawler reachability.** robots.txt rules, sitemap presence/coverage, meta robots + `X-Robots-Tag`, HTTP status & redirects, HSTS, charset, viewport, SPA SSR vs CSR render-path, `<head>` integrity. |
| `skills/structured-data-audit/`    | `structured-data-audit` | **Machine-readable facts.** JSON-LD parseability + type coverage (Organization, WebSite, BreadcrumbList, SearchAction, Product/Article, `sameAs`), microdata, Open Graph completeness, Twitter Cards, `rel=canonical`. |
| `skills/content-accessibility-audit/` | `content-accessibility-audit` | **Text lock-in-free.** Image alt coverage, SVG `<title>`/`<desc>`, `<video>`/`<audio>` captions, iframe titles, `<title>` length & uniqueness, meta description quality, heading hierarchy (H1 count + no skipped levels), text/HTML ratio, content in `data-*` only. |
| `skills/freshness-entity-audit/`   | `freshness-entity-audit`| **Freshness & disambiguation.** `Last-Modified`/`ETag`, `<time datetime>` + OG article timestamps, copyright-year staleness, `sameAs`/Wikipedia/Wikidata cross-reference, `<html lang>`, hreflang self-reference. |
| `skills/engagement-audit/`         | `engagement-audit`      | **On-site keepers.** Internal link count & distribution, `<nav>` and `<footer>` landmarks, on-site search (form + `SearchAction`), CTA verb density & placement, related/recent content blocks, pagination signals, HTML payload weight, third-party resource hints. |

### Cross-page aggregations performed by the orchestrator

In addition to per-page checks, the orchestrator crawls up to
`MAX_PAGES_CRAWLED = 20` internal pages sampled from the homepage and flags:

- Duplicate `<title>` across pages
- Duplicate `meta description` across pages
- Duplicate H1 headings across pages
- % of sampled pages without a meta description
- % of sampled pages without `rel=canonical`
- % of sampled pages with zero structured data at all
- Internal links that resolve to HTTP ≥ 400

## File layout

```
brand-ai-readiness-audit/           ← this is what you zip for submission
├── marketplace.json                ← manifest, exactly one entrypoint (audit-orchestrator)
├── README.md
└── skills/
    ├── audit-orchestrator/
    │   ├── SKILL.md
    │   ├── references/
    │   │   └── finding-checklist.md
    │   └── scripts/
    │       ├── audit_engine.py     ← shared production-grade engine (all checks)
    │       ├── run_audit.py        ← CLI entrypoint (this skill)
    │       └── requirements.txt
    ├── crawl-render-audit/
    │   ├── SKILL.md
    │   └── scripts/run.py          ← standalone runner (delegates to engine)
    ├── structured-data-audit/
    │   ├── SKILL.md
    │   └── scripts/run.py
    ├── content-accessibility-audit/
    │   ├── SKILL.md
    │   └── scripts/run.py
    ├── freshness-entity-audit/
    │   ├── SKILL.md
    │   └── scripts/run.py
    └── engagement-audit/
        ├── SKILL.md
        └── scripts/run.py
```

## Design principles & safety

- **Recommend-only.** No skill ever writes to, authenticates against, or
  mutates a live site. Every check is read-only HTTP(S) `GET`.
- **robots.txt honored.** The shared `AuditorSession` consults
  `urllib.robotparser.RobotFileParser` before every fetch.
- **Polite UA + retries.** The `BrandAIReadinessAudit/1.0` User-Agent
  identifies the auditor. Transient failures retry with mild backoff (0s,
  1s, 2s). Connection pooling via `requests.Session`.
- **Deterministic.** Same input URL ⇒ same ordered findings, same IDs, same
  severity, same counts (no randomized sampling paths).
- **<5 min typical runtime.** Max 20 internal pages fetched; all work is
  sequential, respectful, and CPU-bound only during parse.
- **Portable.** Pure Python 3.10+; runtime deps: `requests`,
  `beautifulsoup4`, `lxml` (all pure wheels or trivial C extensions). No
  external paid APIs, no auth tokens, no model weights.

## Runtime dependencies

| Package           | Min version | Role                                      |
|-------------------|-------------|-------------------------------------------|
| `requests`        | 2.31        | HTTP pooling, retries, polite headers     |
| `beautifulsoup4`  | 4.12        | DOM parse + selection                     |
| `lxml`            | 4.9         | Fast, deterministic BS4 backend           |

## Running individual sub-skills

Each focused skill can also be invoked standalone for debugging or targeted
audits, against the shared engine:

```bash
# Any of these will emit a JSON array of findings for that dimension.
python skills/crawl-render-audit/scripts/run.py          https://example.com --pretty
python skills/structured-data-audit/scripts/run.py       https://example.com --pretty
python skills/content-accessibility-audit/scripts/run.py https://example.com --pretty
python skills/freshness-entity-audit/scripts/run.py      https://example.com --pretty
python skills/engagement-audit/scripts/run.py            https://example.com --pretty
```
