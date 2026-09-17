# Corroboration surfaces and entity disambiguation

## Why off-site work is on the critical path

Nothing a brand does to its own website makes a single-source claim corroborated. A fact
stated in one place, on a domain the brand controls, is the weakest kind of evidence there
is. The strongest is the same fact, in the same words, on several domains it does not
control.

This is the part of an AI-visibility programme that takes months, and it is why a technically
flawless site can still lose to a competitor with a worse one.

## Surfaces that carry weight

**Universal**

- Wikidata — structured, machine-readable, widely consumed. Often the single highest-value
  entry to create, and eligible far below Wikipedia's notability bar.
- Wikipedia — where notability genuinely qualifies. Do not attempt otherwise.
- LinkedIn company page — near-universally indexed and trusted for firmographics.
- Crunchbase / OpenCorporates / national company registries — legal name, founding, filings.
- The brand's own claimed profiles on the major platforms.

**By sector**

| Sector | Surfaces |
| --- | --- |
| B2B software | G2, Capterra, TrustRadius, Product Hunt, GitHub, package registries |
| Consumer products | Retailer listings, Trustpilot, YouTube reviews, comparison sites |
| Local services | Google Business Profile, Apple Business Connect, Yelp, Justdial, local directories |
| Healthcare, legal, finance | Professional registries, regulator listings, association directories |
| Academic, research | ORCID, ROR, Google Scholar, institutional pages |
| Media, publishing | Muck Rack, press databases, syndication partners |

## What "agreement" has to mean

Same **words**, not same gist. Systems reconcile facts by matching strings, so:

- one canonical one-sentence description, used verbatim everywhere;
- one legal name and one trading name, used consistently;
- one founding year, one headquarters address, one phone format;
- product names spelled identically everywhere, including capitalisation.

A brand described three different ways across three profiles has given a machine three
weak signals instead of one strong one, and has made itself harder to recognise as one
entity rather than easier.

## Entity disambiguation

Name collisions are common and cause answers that are accurate about somebody else. The
fixes, in order of leverage:

1. **Always pair the name with a category and a place.** "Acme Robotics, the Pune-based
   industrial automation company" — in titles, descriptions, and structured data.
2. **Publish the hard identifiers.** Legal name, founding date, registration number where
   public, headquarters address, named leadership. These are what a system matches on when
   two candidates share a name.
3. **Create a Wikidata item** with those identifiers and `sameAs` links out to the profiles.
   It is the closest thing to a machine-readable primary key for an organisation.
4. **Name the confusion where it is natural.** If a famous namesake exists, a line on the
   about page that distinguishes them is worth more than hoping the ambiguity resolves.

## Verification tasks

These cannot be answered from the site, so the skill emits them for the agent to run:

| Id | Query shape | What to record |
| --- | --- | --- |
| `V-001` | the bare brand name | Does the brand own its own name, or does a namesake? |
| `V-002` | `"<brand>" -site:<domain>` | How many independent domains describe it; do they agree with the site? |
| `V-003` | `<brand> wikipedia OR wikidata OR crunchbase OR linkedin` | Which reference surfaces exist; is the data current? |
| `V-004` | ask an assistant "what is `<brand>`" | The actual answer, what it cites, and what it gets wrong |
| `V-005` | the category question the brand wants to win | Who gets cited, and what those pages do that this site does not |

`V-004` is the ground truth the whole audit is trying to change. Run it even if nothing else
gets run, and record the answer verbatim so the next audit can be compared against it.
