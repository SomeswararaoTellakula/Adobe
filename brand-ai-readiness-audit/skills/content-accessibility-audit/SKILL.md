---
name: content-accessibility-audit
description: >
  Audit a website for the machine-readability of its factual content —
  whether facts are stated in plain semantic text nodes or locked in
  non-textual artifacts AI cannot quote. Inspects image alt text, SVG
  titles, media captions/tracks, iframe titles, title tag length and
  distinctiveness, meta description quality, heading hierarchy integrity
  (H1 count, skipped levels, zero headings), text-to-HTML ratio, visible
  body text volume, and content locked in data-* attributes only.
license: MIT
tags: ["accessibility", "content", "a11y", "headings", "alt-text", "semantic-html"]
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

# Content Accessibility Audit

## When to use

Invoke this skill after fetching a page's DOM. It addresses the Round-2
failure mode **"machines cannot read what is on the page"** because content
lives in non-text or is structurally indistinct.

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `url`  | yes | Absolute homepage URL |

## Procedure

1. **Image alt text**
   - Count `<img>` with `alt` missing entirely vs. `alt=""` (decorative).
   - If ≥25% lack alt, file `Many informative <img> elements lack alt text`
     (high).
   - If 5+ images all have empty/no alt → possible missed informative
     content (medium).

2. **Non-text element labels**
   - SVGs: ≥2 and none have `<title>` → low.
   - `<video>` / `<audio>` without a captions/subtitles `<track>` → medium
     per tag (de-duplicate per tag name).
   - `<iframe>` without `title` → low.

3. **Title & meta description**
   - `<title>` missing → high.
   - `<title>` <15 chars → medium; >70 chars → low.
   - No `meta name=description` → medium.
   - Description <80 → low; >170 → low.

4. **Heading hierarchy**
   - Zero H1 → high.
   - Multiple H1 → medium.
   - Jump from Hn to Hn+2 without the intermediate level → low.
   - Zero H1–H6 at all on a parseable page → high.

5. **Text density**
   - Visible body text < 300 chars on homepage → high.
   - Text/HTML ratio < 6% → medium.
   - 50+ data-* attributes and <1000 visible chars → content-locked-in-
     data-attrs finding (medium).

6. **Cross-page aggregations**
   - Duplicate titles across pages → high.
   - Duplicate meta descriptions → medium.
   - Duplicate H1s → medium.
   - N pages missing meta description → medium.

## Output

JSON array of findings with the standard shape.

## Executable entry point

```
pip install -r skills/audit-orchestrator/scripts/requirements.txt
python skills/content-accessibility-audit/scripts/run.py https://example.com --pretty
```
