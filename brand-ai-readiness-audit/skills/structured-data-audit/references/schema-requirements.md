# Structured data requirements by page class

Expectations are gated on the site type the collector infers, so a docs site is never told
it lacks `Product` markup.

## Minimum viable markup

| Page class | Types expected | Properties that carry the answer |
| --- | --- | --- |
| home | `Organization` (or `LocalBusiness`) + `WebSite` | `name`, `legalName`, `url`, `logo`, `description`, `sameAs`, `foundingDate`, `address` |
| product | `Product` with `offers` | `name`, `description`, `brand`, `sku`, `offers.price`, `offers.priceCurrency`, `offers.availability`, `aggregateRating` when genuine |
| pricing | `Offer` / `Service` / `SoftwareApplication` | `price`, `priceCurrency`, `priceValidUntil`, `eligibleQuantity` |
| article | `Article` / `BlogPosting` | `headline`, `datePublished`, `dateModified`, `author`, `publisher` |
| docs / faq | `FAQPage`, `HowTo`, `TechArticle` | `question`/`acceptedAnswer` pairs, `step` |
| contact | `LocalBusiness` / `ContactPage` | `address`, `telephone`, `openingHoursSpecification`, `geo` |
| about | `Organization` / `AboutPage` | `legalName`, `foundingDate`, `founder`, `numberOfEmployees` |

## The two properties that matter most for AI answers

**`sameAs`.** The explicit statement that this brand is that profile. It is how a site
connects to the independent sources that corroborate it, and the main defence against being
merged with a namesake. Include only profiles that genuinely refer to this entity: a wrong
`sameAs` actively causes misidentification rather than merely failing to prevent it.

**`@id`.** A stable identifier per entity (`https://example.com/#organization`) referenced
from `WebSite.publisher`, `Article.publisher`, `Product.brand`. Without it, each page
describes an unconnected entity that happens to share a name, and the markup never
accumulates into one graph.

## Checks and their gates

| Id | Fires when | Gate |
| --- | --- | --- |
| `SD-001` | No JSON-LD or microdata anywhere | Whole sample must be empty |
| `SD-002` | Some pages have markup, others of the same class do not | At least 2 pages missing |
| `SD-003` | A JSON-LD block fails to parse | Records the parser error |
| `SD-004.<class>` | Every sampled page of a class lacks its expected type | Only classes present in the sample; only when *all* of them lack it |
| `SD-005` | `Product` with no offer price | — |
| `SD-006` | `Article` with no `datePublished` or `author` | — |
| `SD-007` | Local business type with no address or telephone | — |
| `SD-008` | `Organization` without `sameAs` | Only where Organization markup exists |
| `SD-009` | No `@id` on any Organization node | Only when all of them lack it |
| `SD-010` | Markup price absent from the page's visible prices | Medium confidence; currency and locale formatting noted as a caveat |
| `SD-011` | Missing titles, mostly-missing descriptions, or duplicate titles | Requires a meaningful share of the sample |
| `SD-012` | No `H1`, or three or more `H1`s | Two `H1`s is tolerated as a common template pattern |
| `SD-013` | Half or more pages declare no `lang` | — |
| `SD-014` | Half or more pages have no Open Graph tags | — |

## Why mismatched markup is worse than missing markup

Markup that contradicts the page produces confidently wrong answers, and once a mismatch is
detected both signals get discounted. The usual cause is a hard-coded value or a cached
fragment. Generate the visible value and the JSON-LD from one server-side source and add a
test comparing them on a sample of pages.

## Validate before shipping

Run the Rich Results Test and the schema.org validator on one page per template. Markup
that does not validate contributes nothing while looking like it does — the worst of both
worlds, because it makes the problem invisible in a code review.
