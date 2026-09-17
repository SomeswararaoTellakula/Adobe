---
name: structured-data-audit
description: >
  Stage 3 of an AI-readiness audit: checks whether a page's entity and facts are
  explicitly typed rather than left to be guessed from prose. Validates JSON-LD and
  microdata, checks type coverage against the inferred site type, checks high-value
  properties (Offer price and availability, Article datePublished and author,
  LocalBusiness address and telephone), checks entity identity fields (sameAs, @id,
  legalName), verifies that markup agrees with the visible page, and checks titles,
  descriptions, Open Graph tags, language declarations and heading structure. Use when
  auditing schema.org markup for AI visibility, when assistants misattribute or confuse a
  brand with a similarly-named one, when rich results are missing, or as stage 3 of the
  brand-ai-readiness-audit marketplace. Reads a site pack and performs no network access.
license: MIT
allowed-tools: [Bash, Read, Write]
---

# Structured data audit (stage 3: parse)

Prose has to be interpreted. Structured data is read. This stage checks whether the facts
a brand most wants repeated are stated in a form that cannot be misread — and whether the
brand has a machine-readable identity for those facts to attach to.

## When to use

- Stage 3 of the marketplace audit.
- Standalone, when a brand is found but described wrongly, blended with a namesake, or
  when product and article rich results are missing.

## Inputs

A site pack directory.

## Procedure

```bash
python3 scripts/check_structured_data.py --pack ./pack --out findings-schema.json
```

Read `references/schema-requirements.md` for what each page class should declare and the
minimum property set per type.

## Expectations are gated by site type

The collector infers a site type (ecommerce, saas, publisher, local business, docs,
brand/marketing). A documentation site is never told it is missing `Product` markup, and a
type is only reported missing when **every** sampled page of that class lacks it — one
page without markup is a gap in coverage, reported separately and less severely, not a
missing capability.

Two checks matter more than the rest and are worth understanding:

- **`sameAs` on the Organization node** is the explicit statement "this brand is that
  profile". It is the mechanism by which a site connects to the independent sources that
  corroborate it, and the main defence against being merged with a namesake.
- **Markup that disagrees with the visible page** is worse than no markup. A stale
  hard-coded price in JSON-LD produces confidently wrong answers, and a detected mismatch
  discounts both signals. Structured data and the rendered page must come from one source.

## Without a shell

`references/schema-requirements.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Read each page's `<script type="application/ld+json">` blocks, parse them, and compare the declared types against the expectations table for that page class. Check `sameAs`, `@id`, and whether markup values match the visible page.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `SD-*`. Type-coverage findings are suffixed with the page class
(`SD-004.product`) so a reader can see which template to fix.

## Guardrails

Read-only analysis of an existing pack. No network access. Structured data is parsed
defensively: a malformed block is reported, never executed or trusted.
