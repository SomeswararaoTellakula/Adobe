---
name: audit-orchestrator
description: >
  Entrypoint for the brand-ai-readiness-audit marketplace. Audits any website for the
  problems that stop AI assistants finding, reading, trusting and citing it, and the
  problems hurting on-site engagement that stop assistant-referred visitors staying once
  they land, then emits a single structured audit report of evidence-backed findings plus
  suggested actions, each with severity and priority. Use this whenever a request asks for
  findings and suggested actions for a website, or asks why a brand is invisible, stale or
  wrong in ChatGPT / Claude / Perplexity / AI Overviews, why their site does not get
  cited or recommended by AI assistants, how to improve AI discoverability, GEO, AEO or
  LLM SEO, why AI-referred traffic bounces, or asks for an AI-readiness, AI-visibility
  or AI-SEO audit of a URL. Use it even when the request names only a symptom
  ("assistants describe us wrong", "we never show up in AI answers") rather than an audit.
license: MIT
allowed-tools: [Bash, Read, Write, WebSearch, WebFetch]
---

# Brand AI-Readiness Audit (entrypoint)

Composes six analyzer skills into one audit and emits one report. Everything is
recommend-only: this marketplace reads a site and reports. It never modifies one.

## The model this audit is built on

A brand gets cited when six things succeed in order. Each stage is a separate skill,
because each fails for different reasons and is fixed by different people:

| Stage | Question | Skill |
| --- | --- | --- |
| 1. Reach | Is the machine allowed in, and does it get a response? | `crawl-access-audit` |
| 2. Read | Is the content in the bytes that came back? | `render-extractability-audit` |
| 3. Parse | Are the entity and its facts explicitly typed? | `structured-data-audit` |
| 4. Quote | Is there a self-contained fact worth lifting? | `answerability-audit` |
| 5. Trust | Is it current, attributable and corroborated elsewhere? | `freshness-corroboration-audit` |
| 6. Stay | Does the visitor who arrives get what they came for? | `engagement-audit` |

Order matters when reporting. A stage-1 failure makes stages 2-5 unmeasurable, so fix
recommendations are prioritized in pipeline order, not by count.

## Inputs

- **Required:** a URL or domain.
- Optional: `--max-pages` (default 25), `--budget` seconds (default 240),
  `--pack` to re-analyze an existing collection.

## Procedure

1. **Scope.** Confirm the domain and note anything the user already suspects. If they
   report a specific symptom ("Perplexity says we're in the wrong city"), keep it: it
   usually names the stage to look at first.

2. **Collect once.** Run the collector from `crawl-access-audit`. One polite, read-only,
   robots-respecting pass produces a *site pack* that every analyzer then reads offline.
   Collecting once is what keeps the audit under five minutes and reproducible.

   ```bash
   # Run from the marketplace root - the folder containing marketplace.json:
   python3 skills/audit-orchestrator/scripts/run_audit.py \
     --url https://example.com --out-dir ./audit-output

   # Or from anywhere, using this skill's own absolute path:
   python3 /abs/path/to/skills/audit-orchestrator/scripts/run_audit.py \
     --url https://example.com --out-dir ./audit-output
   ```

   The script locates the marketplace by walking up from its own file, so the absolute
   form works from any working directory - only the first form depends on where you are.
   `run_audit.py` then resolves sibling skills through `marketplace.json`, so the manifest
   is the composition contract rather than hard-coded paths.

   Requires Python 3.8+ and nothing else: no install step, no third-party packages, no
   network services. If `python3` is not on PATH, `python` works where it is version 3.

3. **Optional: add a rendered capture.** If a browser tool is available, load the top few
   URLs, extract the post-JavaScript text, and write one JSON file per page into
   `<pack>/rendered/` as `{"url": ..., "word_count": N, "text": "..."}`. This converts
   inferred JavaScript findings into directly measured ones. Skip it if no browser tool
   exists; the audit degrades to lower confidence rather than failing.

4. **Analyze.** `run_audit.py` runs all six analyzers over the pack. Each writes
   `findings-<name>.json`. They are independent: one failing never blocks the rest.

5. **Verify off-site claims.** The freshness skill emits `agent_verification_tasks` —
   searches that cannot be answered from the site alone (does the brand own its own name
   in search, do independent sources describe it the same way, what does an assistant
   actually say when asked about it). Run them with WebSearch, then fold what you learn
   into the matching findings' evidence. The last one is the ground truth the whole audit
   is trying to move, so run it if you run only one.

6. **Merge.** Findings are normalised, de-duplicated by owner (see
   `references/composition.md`), severity-adjusted (see `references/severity-model.md`),
   sorted by severity then pipeline stage, and given sequential `F-NNN` ids.

7. **Validate and emit.** `validate_report.py` runs automatically and fails the report if
   any finding lacks evidence or an action, or if the counts disagree with the findings.
   Report the summary counts and the top actions in your reply; attach `report.md` for
   the readable version and `report.json` for the machine-readable one.

## If no shell is available

The scripts are the deterministic path, not the only one. Every check, threshold and
false-positive gate is written down in the `references/` files, so an agent with only fetch
and read tools can run the same audit by hand — smaller sample, same reasoning:

1. Fetch `/robots.txt`. Evaluate it for each agent in
   `crawl-access-audit/references/ai-crawler-registry.md`, classifying blocks by agent role.
2. Fetch `/sitemap.xml` (and any sitemap named in robots.txt). Take the homepage plus up to
   8 URLs spread across page classes — product, pricing, article, docs, about, contact.
3. For each page, work through the check catalogue in each analyzer skill's `references/`
   file, in pipeline order. The catalogues give the trigger, the evidence to record and the
   gate that prevents a false positive.
4. Assemble findings with the severity rules in `references/severity-model.md` and emit the
   schema in `references/report-schema.json`.

State the smaller sample in `limitations`. Findings you could not check — anything needing
byte counts, timing or a full crawl — are omitted, never guessed.

## Composition rules

- **One owner per check.** Each check id belongs to exactly one skill. Overlapping
  symptoms are assigned by *root cause*, not by where they show up. Content hidden in a
  closed tab is a `render-extractability-audit` finding (a machine cannot read it) and not
  also an engagement finding. `references/composition.md` holds the full ownership table.
- **Do not re-report a cause as its effects.** When a page is a JavaScript shell, the
  answerability skill excludes it instead of reporting it as badly written. One root cause
  produces one finding.
- **Analyzers never write the report.** They emit findings; only this skill assigns ids,
  final severities and priorities. That keeps severity comparable across dimensions.

## Output

`report.json`, conforming to `references/report-schema.json`. Required by the contract:
`site`, `audited_at`, `summary` with counts by severity, and `findings[]` where every
finding has `id`, `title`, `severity`, `evidence`, `suggested_action`. This marketplace
also emits `scope`, `confidence`, `dimension`, `affected_urls`, `severity_note`,
`suggested_action.steps`, `proactive_recommendations`, `agent_verification_tasks` and
`limitations`.

Two rules for the report body, both enforced by the validator:

- **Every finding carries evidence with numbers.** "0/12 product pages contain schema.org
  markup" is a finding. "Structured data could be improved" is not.
- **Every finding carries an action a non-expert can hand to a developer**: what to change,
  where, and what it should look like afterwards.

Beyond the defects found, always include the proactive recommendations
(`references/beyond-defect-playbook.md`). A site with no detected faults can still be
almost impossible to cite, usually because nothing on it is worth quoting.

## Guardrails

- Recommend-only. No skill writes to the audited site, submits a form, or touches an
  authenticated area. GET and HEAD only.
- robots.txt is obeyed unconditionally. There is no override flag. If it disallows this
  auditor, collection stops and the report contains the robots finding alone — which is
  itself usually the answer.
- Third-party crawler user-agents are never impersonated. Their access is evaluated
  statically against robots.txt.
- Rate-limited and bounded by a single wall-clock deadline covering collection and every
  analyzer (`--budget`, default 240s; hard worst case 4.5 minutes). An audit cannot become
  a load test, and cannot overrun the brief's 5-minute limit.
- Say what was not checked. `limitations` is part of the report, not an appendix.

## References

- `references/composition.md` — check ownership table and de-duplication rules
- `references/severity-model.md` — how severity, confidence and blast radius combine
- `references/report-schema.json` — the output contract
- `references/beyond-defect-playbook.md` — improvements to recommend when nothing is broken
