---
name: structured-data-audit
description: >
  Audit a website for machine-readable structured data coverage, schema
  correctness, and citation-surface metadata. Validates JSON-LD blocks
  (parseability, @type coverage for Organization, WebSite, BreadcrumbList,
  SearchAction, Product/Article, sameAs disambiguation), microdata
  itemtype attributes, Open Graph required tags, Twitter card tags, and
  rel=canonical presence and absoluteness.
license: MIT
tags: ["structured-data", "json-ld", "schema.org", "open-graph", "twitter-cards", "seo"]
declared_tools:
  - shell:execute-read-only
  - python:execute-script
inputs:
  - name: url
    type: string
    required: true
    description: Absolute homepage URL.
outputs:
  - name: findings
    type: application/json
    description: JSON array of findings for this audit dimension.
---

# Structured Data Audit

## When to use

Invoke this skill once the homepage DOM is available; it covers the third
AI-discovery failure mode: **the crawler cannot pick out the specific fact
someone is looking for** because facts are implicit and untyped.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url`  | yes | Absolute homepage URL (scheme + host + path). |

## Procedure

1. **Parse JSON-LD blocks**
   - For every `<script type="application/ld+json">`, parse its contents as
     JSON. On parse failure, file `Invalid JSON-LD block` (high).
   - Flatten arrays and `@graph` nodes into a flat list of typed entities.

2. **Parse microdata**
   - Extract every `itemtype=` attribute; normalize to schema.org type
     names (`schema.org/TypeName` → `TypeName`).

3. **Overall presence**
   - 0 JSON-LD blocks + 0 microdata → `No structured data found on
     homepage` (critical).

4. **Required schema.org types**
   - No `Organization`/`LocalBusiness` → high.
     - If present and no `sameAs` array → high (entity anchor).
     - If present and missing `name` / `url` / `logo` → medium per field.
   - No `WebSite` → medium.
     - If WebSite has no `potentialAction` of type `SearchAction` → medium.
   - No `BreadcrumbList` → medium.

5. **Open Graph**
   - Missing any of `og:title`, `og:type`, `og:image`, `og:url` → medium.
   - `og:image` is relative → low.

6. **Twitter Cards**
   - Zero `twitter:*` tags → low.
   - Missing any of `twitter:card`, `twitter:title`, `twitter:description`,
     `twitter:image` → low.

7. **Canonical**
   - No `<link rel="canonical" href="...">` → high.
   - Present but href is relative (not absolute) → medium.

8. **Cross-page aggregation (on N sampled internal pages)**
   - X% lack rel=canonical → medium.
   - 0/N carry any structured data at all → high.

## Output

A JSON array of findings with the standard finding shape.

## Executable entry point

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python skills/structured-data-audit/scripts/run.py https://example.com --pretty
```
