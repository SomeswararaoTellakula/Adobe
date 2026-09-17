# Quotability rules

## The mechanism

Retrieval works on passages. A page is split into chunks, chunks are matched against a
question, and one is used to build an answer. The chunk arrives without its page title,
navigation or surrounding context.

So the test for any sentence is: **cut it out of the page and hand it to a stranger. Does it
still say something true, specific and attributable?**

| Fails the test | Passes the test |
| --- | --- |
| "We reduce downtime by up to 30%." | "Acme Robotics customers reported 30% less line downtime in a 2026 survey of 412 plants." |
| "Pricing that scales with you." | "The Pro plan costs $49 per user per month, billed annually, with a 10-seat minimum." |
| "Industry-leading accuracy." | "The AR-200 holds 0.02 mm repeatability across 200-cycle acceptance tests." |
| "Trusted by leading brands." | "Used by 340 manufacturing lines across 12 countries as of June 2026." |

## Checks and thresholds

| Id | Fires when | Severity |
| --- | --- | --- |
| `ANS-001` | No definitional sentence on the homepage | `high` if absent from about too, `medium` if about has one |
| `ANS-002` | 40%+ of content pages never name the brand in body copy | medium |
| `ANS-003` | A pricing page contains no currency figure | high |
| `ANS-004` | 60%+ of product/docs pages have no table, definition list or list | medium |
| `ANS-005` | Superlatives outnumber specific figures more than 2:1, with 4+ superlatives | medium |
| `ANS-006` | 2+ paragraphs over 120 words on a 300+ word page | low |
| `ANS-007` | 400+ words under 2 or fewer headings | low |
| `ANS-008` | No question-shaped heading anywhere in the sample | medium |
| `ANS-009` | Multi-section pages with no heading ids | low |
| `ANS-010` | Navigation and footer text outweighs content on a thin page | low |

Pages withheld by JavaScript are excluded — that is a stage-2 rendering finding, and
reporting them here as badly written would be both wrong and double-counting.

## The definitional sentence

The single highest-leverage sentence on a site. Shape:

> `<Brand>` is a `<category>` `<verb>` `<what>` for `<whom>` `<qualifier>`.

> Acme Robotics is an industrial automation company in Pune that builds pick-and-place arms
> for electronics manufacturers.

Put it in the first paragraph of the homepage, verbatim in the about page, verbatim in the
`Organization.description`, and verbatim in the meta description. The repetition is not
redundancy — it is corroboration, and it gives every system that describes the brand the
same words to copy.

## Answer-first structure

Lead each section with its conclusion, then support it. A chunk that opens with context and
buries the answer at the end frequently gets truncated before the answer arrives.

Phrase headings as the question a user would ask ("How much does the AR-200 cost?") rather
than as a label ("Investment"). Then answer in the first sentence, in a form that stands
alone if the heading is stripped.

## What not to over-correct

Marketing language is not banned, and stuffing brand names into every sentence reads badly
to humans, who are still the ones deciding whether to buy. The failure mode this catches is
copy where there is *nothing else* — where a reader finishes the page unable to state a
single fact. One brand mention per major section and one specific figure per claim is
enough.
