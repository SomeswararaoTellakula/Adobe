---
name: engagement-audit
description: >
  Audit a website for on-site-engagement factors that keep visitors around
  once they arrive — navigation landmarks, internal link density and
  distribution, on-site search mechanisms (form + JSON-LD SearchAction),
  call-to-action density and coverage, related/recent content blocks,
  pagination vs. infinite-scroll signals, footer richness, HTML payload
  weight, and third-party origin resource hints (preconnect, dns-prefetch).
license: MIT
tags: ["engagement", "navigation", "cta", "internal-links", "search", "performance"]
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

# Engagement Audit

## When to use

Invoke this skill to produce the **on-site engagement** half of the audit
(why visitors who arrive don't stay). Focuses on orientation, pathways,
and page-speed signals that are directly detectable from the static DOM
and headers without running JavaScript profiling.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url`  | yes | Absolute homepage URL. |

## Procedure

1. **Internal link graph**
   - Count same-domain `<a href>` links (resolved to absolute).
   - Fewer than 5 internal links on homepage → high.
   - All internal links collapse to path `/` → medium.

2. **Navigation & footer landmarks**
   - No `<nav>` element → medium.
   - No `<footer>` → medium.
   - Footer has <5 links → low.

3. **On-site search**
   - Presence of `<form role="search">` OR an input with `type=search` /
     `name=search` / `name=s`.
   - Presence of JSON-LD `SearchAction` (depth-first search through all
     blocks).
   - Neither is present → medium.

4. **Call-to-action coverage**
   - Search button/link/input text for CTA verbs: sign up, subscribe,
     register, start, try, buy, book, demo, get started, request, contact,
     download, join.
   - 0 CTAs on page with >800 visible chars → high.
   - Exactly 1 CTA → low.

5. **Content continuity**
   - On pages with ≥1500 visible chars, look for heading strings
     containing "related / recent / popular / featured / trending /
     you might / recommended". Absent → medium.

6. **Pagination / list structure**
   - Many `<article>`/`<li>` nodes but no `<a rel=next>` or
     `<link rel=next>` → low.

7. **Performance heuristics**
   - HTML response > 500 KB → medium.
   - External links exist but no `<link rel=dns-prefetch|preconnect>` →
     low.

## Output

JSON array of standard findings.

## Executable entry point

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python skills/engagement-audit/scripts/run.py https://example.com --pretty
```
