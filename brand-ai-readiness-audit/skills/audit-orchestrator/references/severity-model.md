# Severity model

Severity is assigned deterministically so two runs over the same pack produce the same
report, and so severities are comparable across dimensions rather than being each
analyzer's private opinion.

## Base severity

Set by the check, from the mechanism it breaks:

| Severity | Meaning |
| --- | --- |
| `critical` | Breaks the pipeline for the whole site. Nothing downstream can succeed. Blocked user-triggered fetchers, site-wide `Disallow: /`, site-wide `noindex`, every page a JavaScript shell. |
| `high` | Removes a whole page class from consideration, or removes the primary citable fact. A pricing page with no price, product markup with no offer, no viewport on any page. |
| `medium` | Degrades extraction, trust or engagement where a workaround exists. Missing descriptions, no breadcrumbs, undated articles. |
| `low` | Marginal, isolated, or an accumulation of small friction. Long paragraphs, vague link text, missing heading anchors. |
| `info` | A deliberate policy choice recorded with its tradeoff, not a defect. Blocking training crawlers is the main one. Never counted as a problem. |

## Two adjustments

**Confidence demotion.** A finding inferred from markup rather than observed directly is
demoted one level and carries a `verify_by` note describing the one-minute check that would
confirm it. This is the main defence against a confident-sounding report built on guesses:
if the auditor could not see it, the report does not claim it saw it.

**Coverage promotion.** A check marked `scales_with_coverage`, observed at high confidence,
affecting at least three pages and at least 80% of the sample, is promoted one level. A
template-wide failure is a different problem from one bad page, and should be read that way.

Promotion never applies to low-confidence findings, so the two rules cannot cancel out into
a confidently-stated guess. Checks that are inherently site-wide facts (a viewport tag in a
base template) are not marked `scales_with_coverage`, because their page count reflects the
sample size rather than blast radius.

Every adjustment writes its reason into `severity_note`, so a reader can see why a finding
sits where it does.

## Priority

`P0` critical, `P1` high, `P2` medium, `P3` low, `P4` proactive. Within the report, actions
are additionally ordered by pipeline stage: a stage-1 access fix precedes a stage-4 content
fix of the same severity, because until the fetcher gets in, the content work cannot pay off.

## Confidence

| Confidence | Basis |
| --- | --- |
| `high` | Directly observed in the fetched bytes: a header, a status code, a count of elements. |
| `medium` | Strongly implied by several signals but not directly observed, e.g. facts in images inferred from a low word count next to in-content images. |
| `low` | A markup-pattern inference that needs human or browser confirmation, e.g. an overlay that may or may not auto-open. |
