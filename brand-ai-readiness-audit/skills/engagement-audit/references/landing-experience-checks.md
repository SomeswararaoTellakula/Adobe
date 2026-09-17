# Deep-landing experience checks

## The visitor this stage is about

Not a homepage visitor. Someone an assistant sent to one page, holding a claim it made,
wanting to confirm it or act on it. They did not navigate in, they have no memory of the
brand's structure, and the conversation that produced the click is invisible to the site.

Three consequences that ordinary UX audits miss:

- **The page must be self-sufficient.** Anything the homepage would have explained has to
  be re-established here, briefly.
- **The specific fact must be visible immediately.** They did not come to browse. If the
  claim they arrived to check is three clicks deep, the visit is over.
- **Mobile is the default.** An overlay covers proportionally far more of a phone screen.

## Checks and thresholds

| Id | Fires when | Confidence |
| --- | --- | --- |
| `ENG-001` | Half or more deep pages have no breadcrumb and never name the brand in the opening content | medium |
| `ENG-002` | 40%+ of pages carry modal/consent/overlay markup, or auto-open triggers | low — markup presence is not proof of behaviour |
| `ENG-003` | A content page has no in-content internal links | high |
| `ENG-004` | No viewport meta tag | high |
| `ENG-005` | 5+ images with 60%+ missing width/height | high |
| `ENG-006` | Over 400 KB of HTML or over 4000 ms response | high |
| `ENG-007` | The 404 page has under 40 words and fewer than 3 links | high |
| `ENG-008` | 5+ links labelled "click here", "learn more" and similar | high |
| `ENG-009` | A thin deep page carrying an email or password field | low — needs visual confirmation of an actual gate |
| `ENG-010` | No search input found across 6+ sampled pages | medium — search may be rendered client-side |

Low-confidence findings are demoted one severity level by the orchestrator and carry a
`verify_by` note. Confirming most of them takes one minute in a private window at
phone width.

## Fix patterns

**Orientation.** Breadcrumb marked up as `BreadcrumbList`, an `H1` that states the subject
in full rather than a fragment, and a one-line "what this page is" under it. Write every
deep page as if the visitor has never seen the homepage, because increasingly they have not.

**Interstitials.** Suppress promotional modals on the first pageview of a session. Keep
consent banners compact and non-blocking where the law allows. Delay chat widgets until
there is a scroll or dwell signal. The visitor who arrived for one fact and got a popup is
the cheapest conversion a site will ever lose.

**Next step.** From each page ask: what does someone who just read this want next? Link it
in the body text, not only in the navigation, and make the CTA task-continuing ("See plan
limits") rather than generic ("Learn more").

**404s.** Assistants sometimes cite URLs that have moved. A 404 with search and the main
destinations recovers that visitor. Keep the 404 status code — only the content should
improve, because a soft 200 creates a crawling problem in exchange.

## What this stage deliberately leaves alone

Conversion-rate optimisation in general, brand and visual design, copy tone, and anything
requiring analytics the audit cannot see. The scope is the mechanical properties of the
landing experience that are visible in the served markup and that specifically affect a
deep, context-free arrival.
