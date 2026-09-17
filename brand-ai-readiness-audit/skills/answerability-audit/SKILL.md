---
name: answerability-audit
description: >
  Stage 4 of an AI-readiness audit: checks whether a page states a self-contained,
  checkable fact that an assistant could actually lift and repeat. Detects the absence of
  a plain sentence defining what the brand is, body copy that never names the brand,
  pricing pages with no price, specifications written as prose instead of tables, copy
  dense with marketing superlatives but empty of verifiable figures, long paragraphs with
  no chunk boundaries, missing question-shaped content, and headings with no anchors to
  cite. Use when a site is crawlable, rendered and marked up correctly but assistants
  still never quote it, when a brand loses comparison and pricing questions to
  competitors, when auditing content for AEO/GEO or LLM citability, or as stage 4 of the
  brand-ai-readiness-audit marketplace. Reads a site pack and performs no network access.
license: MIT
allowed-tools: [Bash, Read, Write]
---

# Answerability audit (stage 4: quote)

The stage most audits skip. A page can be perfectly crawlable, fully rendered and richly
marked up and still never be cited, because nothing on it is a statement that survives
being lifted out of its page.

Retrieval works on passages, not pages. The unit that gets quoted is a chunk of a few
hundred words, separated from its header, its navigation and its context. A sentence like
"We reduce downtime by up to 30%" is unusable at that point: no subject, no comparison
baseline, no date. "Acme Robotics customers reported 30% less line downtime in a 2026
survey of 412 plants" survives the trip.

## When to use

- Stage 4 of the marketplace audit.
- Standalone, when the technical stages come back clean and the brand still is not cited,
  or when a competitor with an objectively worse site keeps winning the answer.

## Inputs

A site pack directory.

## Procedure

```bash
python3 scripts/check_answerability.py --pack ./pack --out findings-answerability.json
```

Read `references/quotability-rules.md` for the thresholds and the rewrite patterns to hand
to whoever owns the copy.

## What this skill will not do

It excludes pages whose text was withheld by JavaScript, because those are a rendering
failure already reported by stage 2 and reporting them again as "badly written" would be
wrong and would inflate the count. It does assess genuinely short pages that *were*
served — a pricing page reading "contact us for pricing" is exactly the defect this stage
exists to catch, and its brevity is the finding, not a reason to skip it.

It also does not treat marketing language as bad writing in general. The check fires only
when superlatives outnumber verifiable figures by more than two to one, which is the point
at which there is genuinely nothing on the page to extract.

## Without a shell

`references/quotability-rules.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Read each page as prose. Apply the extract test: cut a claim out of the page and ask whether it still says something specific and attributable. Check the opening for a definitional sentence, pricing pages for real numbers, and headings for question shape.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `ANS-*`. Severity reflects how central the missing fact is: a pricing
page without a price is high, long paragraphs are low.

## Guardrails

Read-only analysis of an existing pack. No network access. Findings describe patterns in
the sample with counts; they never rewrite the site's copy.
