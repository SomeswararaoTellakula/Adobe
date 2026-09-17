---
name: freshness-corroboration-audit
description: >
  Stage 5 of an AI-readiness audit: checks whether an extracted fact will be trusted
  enough to repeat. Checks visible and machine-readable dates, staleness signals across
  the site, fabricated freshness from build-time timestamps, author attribution, whether
  the brand links itself to independent corroborating sources through sameAs and outbound
  links, whether anything distinguishes the brand from entities with the same name, and
  whether statistics are sourced. Emits search tasks for the off-site checks that cannot
  be answered from the site alone. Use when AI assistants describe a brand with outdated
  facts, confuse it with a namesake, refuse to make claims about it, or cite competitors
  as the authority on the brand's own category; when auditing entity disambiguation or
  E-E-A-T style trust signals for AI answers; or as stage 5 of the brand-ai-readiness-audit
  marketplace. Reads a site pack; network work is delegated to the calling agent.
license: MIT
allowed-tools: [Bash, Read, Write, WebSearch]
---

# Freshness and corroboration audit (stage 5: trust)

Extraction is not citation. A machine that can read a claim still has to decide whether to
repeat it, and it prefers claims that are current, attributable, and stated the same way in
several places it did not have to take the brand's word for.

Two failure modes dominate:

- **Single-source fragility.** A fact that appears on exactly one domain — the brand's own
  — has nothing to corroborate it. The fix is not on the website at all: it is getting the
  same facts, in the same words, onto independent surfaces.
- **Mistaken identity.** When several entities share a name, a system with nothing to tell
  them apart either picks the better-described one or blends them. This is why a brand can
  be described accurately in ways that belong to somebody else.

## When to use

- Stage 5 of the marketplace audit.
- Standalone, when assistants get facts about a brand wrong or stale rather than missing.

## Inputs

A site pack directory. The off-site half needs web search, run by the calling agent.

## Procedure

1. Run the on-site checks:

   ```bash
   python3 scripts/check_freshness.py --pack ./pack --out findings-freshness.json
   ```

2. Run the `agent_verification_tasks` the script emits. They are deliberately not
   simulated from on-site data, because they are not knowable from it:
   - does the brand own its own name in search, or does a namesake?
   - which independent domains describe it, and do they agree with the site?
   - what does an assistant actually say when asked about the brand, and what does it cite?
3. Fold the answers into the matching findings' evidence before the report is assembled.
   Read `references/corroboration-surfaces.md` for which surfaces matter by sector and
   what "agreement" has to mean to count.

## The check most likely to be misread

Uniform, current `dateModified` on every page is reported — not as a fix for staleness but
as the opposite problem. A build timestamp stamped site-wide tells a system nothing about
what actually changed, and manufactured freshness costs trust when detected. Real dates on
genuinely revised pages beat fresh dates everywhere.

## Without a shell

`references/corroboration-surfaces.md` holds every check, threshold and gate, so this stage can be run by hand
with fetch and read tools alone. Read visible dates, bylines and outbound identity links, plus `datePublished`/`author` in markup. Then run the five verification searches — this stage's off-site half is agent work in every mode, so nothing is lost here without a shell.

Record the same evidence — counts and sample size — and mark anything you could not measure
as unchecked rather than passing.

## Output

Findings with ids `FRC-*`, plus `agent_verification_tasks` for the calling agent.

## Guardrails

Read-only analysis of an existing pack. The script performs no network access itself; it
emits queries for the agent to run, so search behaviour stays visible and auditable.
