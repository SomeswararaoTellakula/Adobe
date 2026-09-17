---
name: render-extractability-audit
description: >
  Stage 2 of an AI-readiness audit: checks whether content that is visible to a human is
  present in the bytes a machine receives. Detects client-side-rendered pages whose text
  is absent from the served HTML, facts locked inside images, PDFs, video or canvas,
  content delivered in iframes or loaded only on click, missing alt text, and documents
  with almost no extractable text. Use when a site looks complete in a browser but AI
  assistants describe it as empty, thin, or get its facts wrong; when auditing a React,
  Next.js, Vue, Angular or SPA site for AI visibility; when pricing or specifications live
  in a graphic; or as stage 2 of the brand-ai-readiness-audit marketplace. Reads a site
  pack produced by crawl-access-audit and performs no network access of its own.
license: MIT
allowed-tools: [Bash, Read, Write]
---

# Render and extractability audit (stage 2: read)

A page that looks complete to a person is not always complete to a machine. This stage
measures the gap between what is rendered and what is served.

## When to use

- Stage 2 of the marketplace audit.
- Standalone, when assistants describe a site as having "no information available" while
  the site plainly does, or when a site is built on a JavaScript framework.

## Inputs

A site pack directory. Optionally a `rendered/` subdirectory inside it holding
post-JavaScript captures as `{"url": ..., "word_count": N, "text": "..."}` per file.

## Procedure

```bash
python3 scripts/check_render.py --pack ./pack --out findings-render.json
```

With a rendered capture present, the served-vs-rendered delta is measured directly and
findings are marked high confidence. Without one, JavaScript dependence is inferred and
findings carry a `verify_by` note instead of being asserted. Read
`references/render-signatures.md` for the signature list and the exact gates.

## What separates a real finding from a false one

A framework signature is not evidence. Server-rendered React and Next.js ship complete
HTML and must never be flagged. The finding requires **both** a thin served document
**and** a shell signature (empty root container, noscript notice, or framework bundle with
no text). The same discipline applies elsewhere in this skill:

- A short page with a specification table or a written price has its facts in text. It is
  dense, not broken, and is not flagged as facts-locked-in-images.
- Iframes carrying analytics, maps or video players are not content losses; only iframes
  on pages with little text of their own are flagged, for confirmation.
- Missing alt text is only reported at scale (five or more images, half or more without
  alt), because a decorative image with `alt=""` is correct.

## Without a shell

`references/render-signatures.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Fetch each page's raw HTML and read it as a fetcher would: is the body text present, or is there an empty root container and a framework bundle? Note in-content images, media without `<track>`, and iframes. If a browser tool exists, compare rendered text to served text.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `REN-*`, each naming the affected URLs, the served word count, and what
signature was observed.

## Guardrails

Read-only analysis of an existing pack. No network access, no JavaScript execution.
