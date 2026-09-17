---
name: engagement-audit
description: >
  Stage 6 of an AI-readiness audit: checks whether a visitor referred by an AI assistant
  stays once they land. Checks deep-landing orientation (breadcrumbs, self-describing
  headings, whether the page says whose site this is), overlays and consent walls covering
  the content on arrival, dead-end pages with no contextual next step, missing mobile
  viewport, layout shift from images with no dimensions, page weight and latency,
  unhelpful 404 pages, vague link text, content gated behind forms, and missing on-site
  search. Use when AI-referred or search traffic arrives and bounces, when auditing
  landing-page experience for assistant referrals, when a site converts badly on deep
  entries, or as stage 6 of the brand-ai-readiness-audit marketplace. Reads a site pack
  and performs no network access.
license: MIT
allowed-tools: [Bash, Read, Write]
---

# Engagement audit (stage 6: stay)

Winning the citation is half the job. The other half starts when someone clicks it.

Assistant referrals are not search referrals, and auditing them as if they were misses the
real failures:

- They **land deep**, not on the homepage. The page has to establish whose site this is,
  because the visitor never passed through the front door.
- They arrive **holding an answer already** — the assistant told them something, and they
  came to verify it or act on it. A page that makes them hunt for that fact has nothing to
  hold them with, because they did not come to browse.
- They arrive **without the context that got them here**. The conversation that produced
  the click is invisible to the site, so the page has to be self-sufficient.
- They skew **mobile**, where an overlay covers proportionally far more of the screen.

## When to use

- Stage 6 of the marketplace audit.
- Standalone, when AI or search referrals bounce, or when analytics shows deep entries
  converting far worse than homepage entries.

## Inputs

A site pack directory.

## Procedure

```bash
python3 scripts/check_engagement.py --pack ./pack --out findings-engagement.json
```

Read `references/landing-experience-checks.md` for thresholds and the confirmation steps
for the checks that are markup inferences.

## Which findings are inferences

Overlay, gating and search checks are inferred from markup: a modal class is not proof a
modal opens on arrival, and a client-side search widget may not appear in served HTML.
These are emitted at low confidence with a `verify_by` note, and the orchestrator demotes
them one severity level automatically. Confirming them takes one minute in a private
window on a phone-width screen, and the report says so rather than asserting them.

Checks that are directly observable — no viewport tag, no in-content internal links,
images without dimensions, a bare 404 — are reported at high confidence.

## Without a shell

`references/landing-experience-checks.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Read each deep page as a first-time visitor arriving from an answer: is there a breadcrumb, does the H1 name the subject, is there a viewport tag, are there in-content internal links, and is there modal or consent markup? Weight and timing checks are skipped.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `ENG-*`.

## Guardrails

Read-only analysis of an existing pack. No network access, no interaction with the live
site, no form submission.
