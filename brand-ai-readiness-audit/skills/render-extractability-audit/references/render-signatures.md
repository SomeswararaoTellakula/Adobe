# Render and extractability signatures

## What is measured

For each page the collector records: served word count, HTML byte size, text-to-markup
ratio, framework signatures, empty-root-container detection, `<noscript>` text, in-content
images and their alt attributes, iframes, media elements and text tracks, and the count of
elements carrying `aria-controls`.

## Framework signatures

`__NEXT_DATA__`/`_next/static`, `window.__NUXT__`, `data-reactroot`, `ng-version`,
`<app-root`, Vue scoped-style attributes, SvelteKit markers, `__remixContext`, Gatsby
chunks, plus platform markers (WordPress, Shopify, Webflow, Squarespace, Wix).

**A framework signature alone is never a finding.** Server-rendered Next.js ships complete
HTML. The empty-shell pattern is what matters:

```html
<div id="__next"></div>          <!-- shell with no text -->
<noscript>Please enable JavaScript</noscript>
```

`REN-001` requires a thin served document **and** a shell or framework signature. With a
`rendered/` capture present the raw-vs-rendered delta is measured directly and confidence
rises to high; without one the finding carries a verification note.

## Check catalogue

| Id | Fires when | Gate |
| --- | --- | --- |
| `REN-001` | Under 120 served words plus a shell or framework signature | Both conditions required; escalates to critical only when at least half the sample is affected |
| `REN-002` | Rendered text more than double the served text | Requires an actual rendered capture |
| `REN-003` | Product/pricing/docs page with in-content images, under 120 words, and no table, definition list or written price | The textual-facts gate is what stops dense spec pages being flagged |
| `REN-004` | 5+ images with half or more missing alt | Decorative `alt=""` is correct and expected |
| `REN-005` | `<video>`/`<audio>` with no `<track>` | Medium confidence: a transcript may exist elsewhere on the page |
| `REN-006` | Content page under 200 words with a remote iframe | Flagged for confirmation; maps and players are not content losses |
| `REN-007` | 4+ `aria-controls` elements with under 250 served words | Low confidence: panels may be present but CSS-hidden, which is fine |
| `REN-008` | Over 120 KB of HTML with a text ratio under 3% | — |

## Fix patterns

**Client-side rendering.** Server-render or pre-render the content. Serve the same HTML to
everyone — do not detect bots and serve them a special version, because it is fragile,
penalised, and tends to drift out of sync with what users see.

**Facts in images.** A pricing table published as a PNG is invisible to every text system
and to every screen reader. Publish the values as an HTML table and keep the graphic as an
illustration with alt text that repeats the key fact.

**Interaction-gated content.** Ship panel content in the initial HTML and toggle visibility
with CSS rather than fetching on click. This also removes a click between the visitor and
the answer they arrived for.

**PDFs.** A PDF is a worse container than HTML for anything you want quoted: extraction is
lossier, scanned PDFs have no text layer at all, and the content cannot carry structured
data. Publish an HTML version and keep the PDF as the download.
