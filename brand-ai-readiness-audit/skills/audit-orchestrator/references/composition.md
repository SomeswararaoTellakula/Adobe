# Check ownership and de-duplication

Every check id belongs to exactly one skill. When two skills could plausibly report the
same symptom, the check is assigned by **root cause**, not by where the symptom shows up.
This is what keeps one underlying problem from being reported six times, which is the
fastest way to make an audit useless.

## Ownership table

| Skill | Owns | Explicitly does not own |
| --- | --- | --- |
| `crawl-access-audit` | robots.txt and agent gating, meta robots / X-Robots-Tag directives, WAF challenges, sitemap health, canonical and host duplication, HTTP errors, soft 404s, server latency | Anything about the content of a page that was successfully fetched |
| `render-extractability-audit` | JavaScript-dependent content, raw-vs-rendered delta, facts in images or media, alt text, iframe content, interaction-gated content, text-poor documents | Whether the extractable text is any good (stage 4), whether hidden content annoys the visitor (stage 6) |
| `structured-data-audit` | JSON-LD and microdata validity, type coverage, required properties, `sameAs` and `@id` presence, markup-vs-page agreement, title/description/OG/lang, heading hierarchy | Whether the external profiles named in `sameAs` actually exist and agree (stage 5) |
| `answerability-audit` | Definitional sentences, brand naming inside chunks, facts stated as text, spec tables, superlative density, paragraph and heading structure, anchor ids | Whether facts are current or corroborated (stage 5), whether facts render at all (stage 2) |
| `freshness-corroboration-audit` | Dates, staleness, fabricated freshness, attribution, off-site corroboration, entity ambiguity, unsourced statistics | Presence of the `sameAs` property itself (stage 3) — this stage judges whether the surfaces it points at corroborate the brand |
| `engagement-audit` | Deep-landing orientation, interstitials, dead ends, viewport, layout stability, weight, 404 usefulness, link labels, form gating, on-site search | Anything a machine cannot read (stage 2) |

## Deliberate near-misses

These pairs look like duplicates and are not:

- **Hidden tab content.** Stage 2 owns it: a machine cannot read it. Stage 6 does not
  re-report it, even though it also costs the visitor a click.
- **`sameAs`.** Stage 3 checks the property exists and is well-formed. Stage 5 checks
  whether independent surfaces actually describe the brand, and consistently. Different
  fixes, different owners: one is a developer ticket, the other is months of work.
- **Missing alt text.** Stage 2 owns it, framed as extractability. Stage 6 mentions
  accessibility only as a consequence, without a second finding.
- **Slow responses.** Stage 1 owns it as a fetcher-timeout risk (`ACC-016`). Stage 6 owns
  page weight as a human-experience problem (`ENG-006`). They fire on different thresholds
  and have different fixes; when both fire, they are genuinely two problems.
- **Thin pages.** If the text was withheld by JavaScript, stage 2 owns it and stage 4
  excludes the page. If the text was served and is simply thin, stage 4 owns it.

## Merge rules applied by the entrypoint

1. Collect findings from every analyzer; each carries its `check_id`, `dimension` and
   `source_skill`.
2. Apply severity normalisation (see `severity-model.md`).
3. Sort by severity, then by pipeline stage, then by check id. Pipeline order is causal
   order: fixing stage 1 can change what stages 2-5 would even report.
4. Assign sequential `F-NNN` ids and map severity to `P0`-`P4` priority.
5. Validate. A finding without evidence, or a count that disagrees with the findings list,
   fails the report rather than shipping quietly.
