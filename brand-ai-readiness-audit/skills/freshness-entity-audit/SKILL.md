---
name: freshness-entity-audit
description: >
  Audit a website for content-freshness signals and entity disambiguation —
  the factors that let an AI assistant trust that content is current and
  that it refers to the intended brand, not a namesake. Checks Last-Modified
  / ETag headers, <time datetime> markup, OG article:published_time /
  article:modified_time, copyright-year staleness in footer, presence of
  Wikipedia / Wikidata cross-links or JSON-LD Organization sameAs array,
  <html lang> declaration, and hreflang self-reference integrity.
license: MIT
tags: ["freshness", "entity", "sameAs", "disambiguation", "copyright", "hreflang"]
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
    description: JSON array of findings.
---

# Freshness & Entity Audit

## When to use

Invoke this skill to detect the Round-2 failure modes where content is
treated as stale or the brand is confused with another entity of the same
name. Corresponds to Appendix D (agreement across the web) and content
currency.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url`  | yes | Absolute homepage URL. |

## Procedure

1. **Caching / freshness headers**
   - Response has neither `Last-Modified` nor `ETag` → low.

2. **Timestamped content**
   - On article/main-bearing pages, require `<time datetime>` or
     `article:published_time` / `article:modified_time` OG meta.
   - Absent on long-form content (≥1500 chars) → medium.

3. **Copyright currency**
   - Extract all 4-digit years ≥1900 from the footer.
   - If neither the current year nor the previous year is present →
     `Copyright year in footer appears stale` (low).

4. **Entity corroboration**
   - Check body `<a>` links for Wikipedia or Wikidata URLs.
   - Check JSON-LD Organization/LocalBusiness for non-empty `sameAs` array.
   - If none of the three exist → high finding.

5. **Language & internationalization**
   - No `<html lang="">` → low.
   - If hreflang alternate tags are present, require a self-referencing
     tag; otherwise → low.

## Output

JSON array of standard-shaped findings.

## Executable entry point

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python skills/freshness-entity-audit/scripts/run.py https://example.com --pretty
```
