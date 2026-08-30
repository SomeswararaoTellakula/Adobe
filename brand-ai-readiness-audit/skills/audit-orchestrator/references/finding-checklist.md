# Finding Checklist — Audit Orchestrator

Master list of every finding type the marketplace is designed to detect.
Each carries severity, evidence threshold, and suggested-action logic in
`scripts/audit_engine.py`.

## 1. Crawl & render (CR / RD prefixes pre-renumbering)

| Title pattern                                                              | Severity |
|----------------------------------------------------------------------------|----------|
| robots.txt is unreachable or missing                                       | medium   |
| robots.txt blocks all crawlers (`Disallow: /` on `User-agent: *`)          | **critical** |
| Homepage URL is blocked by robots.txt                                      | **critical** |
| No sitemap.xml is exposed                                                  | high     |
| Sitemap has very few URLs or is nearly empty                               | medium   |
| Homepage meta robots blocks indexing/snippets (noindex / none)             | **critical** |
| Homepage meta robots uses nofollow                                         | high     |
| X-Robots-Tag blocks indexing                                               | **critical** |
| Homepage is unreachable over HTTP(S)                                       | **critical** |
| Homepage returns HTTP ≥ 400                                                | **critical** |
| Homepage issues HTTP 3xx redirect                                          | low      |
| Strict-Transport-Security header is missing (HTTPS origins)                | low      |
| Content-Type header omits charset declaration                              | low      |
| Page body appears to be client-rendered without SSR/noscript fallback      | high     |
| No `<meta charset>` in `<head>`                                            | low      |
| Page has no `<title>` tag                                                  | high     |
| Multiple `<title>` tags in `<head>`                                        | medium   |
| No viewport meta tag                                                       | medium   |
| `<base>` tag without href                                                  | low      |
| N sampled internal pages lead to HTTP errors                               | high     |
| Duplicate page titles across N sampled pages                               | high     |

## 2. Structured data (SD prefixes)

| Title pattern                                                              | Severity |
|----------------------------------------------------------------------------|----------|
| No structured data (JSON-LD or microdata) found on homepage                | **critical** |
| Invalid JSON-LD block (JSON parse error)                                   | high     |
| Missing Organization/LocalBusiness schema                                  | high     |
| Organization schema has no `sameAs` links (entity disambiguation)          | high     |
| Organization schema missing `name` / `url` / `logo`                        | medium   |
| Missing WebSite schema with SearchAction (site-links search box)           | medium   |
| WebSite schema has no `potentialAction` SearchAction                       | medium   |
| Missing BreadcrumbList schema                                              | medium   |
| Open Graph tags are incomplete (og:title/type/image/url)                   | medium   |
| `og:image` is a relative URL                                               | low      |
| No Twitter card meta tags                                                  | low      |
| Twitter card tags are incomplete                                           | low      |
| No `rel=canonical` link                                                    | high     |
| `rel=canonical` is a relative URL                                          | medium   |
| N sampled pages lack `rel=canonical`                                       | medium   |
| 0/N sampled internal pages carry any structured data                       | high     |

## 3. Content accessibility (CA prefixes)

| Title pattern                                                              | Severity |
|----------------------------------------------------------------------------|----------|
| Many informative `<img>` elements lack alt text (≥25% of imgs without alt) | high     |
| All images declare empty alt — possible missed informative content         | medium   |
| SVG elements lack `<title>` accessibility labels                           | low      |
| `<video>` / `<audio>` has no captions/subtitles `<track>`                  | medium   |
| `<iframe>` missing title attribute                                         | low      |
| `<title>` is too short to be distinctive (<15 chars)                       | medium   |
| `<title>` exceeds typical visible length (>70 chars)                       | low      |
| No meta description                                                        | medium   |
| Meta description is very short (<80 chars)                                 | low      |
| Meta description is likely truncated (>170 chars)                          | low      |
| Page has no `<h1>` heading                                                 | high     |
| Multiple `<h1>` headings on one page                                       | medium   |
| Heading levels skip (H1 → H3 without H2 …)                                 | low      |
| Page has no semantic headings at all (H1–H6)                               | high     |
| Homepage carries very little readable text (<300 visible chars)            | high     |
| Text-to-HTML ratio is very low (<6%)                                       | medium   |
| Content appears to live mostly in data-* attributes                        | medium   |
| N sampled pages lack a meta description                                    | medium   |
| Duplicate meta descriptions across sampled pages                           | medium   |
| Duplicate H1 headings across sampled pages                                 | medium   |

## 4. Freshness & entity (FE prefixes)

| Title pattern                                                              | Severity |
|----------------------------------------------------------------------------|----------|
| No `Last-Modified` or `ETag` cache headers                                 | low      |
| No machine-readable publish/update timestamps on article content           | medium   |
| Copyright year in footer appears stale                                     | low      |
| No cross-web entity corroboration (sameAs / Wikipedia / Wikidata)          | high     |
| `<html>` has no `lang` attribute                                           | low      |
| hreflang set missing a self-referencing tag                                | low      |

## 5. On-site engagement (EG prefixes)

| Title pattern                                                              | Severity |
|----------------------------------------------------------------------------|----------|
| Homepage has very few internal links (<5)                                   | high     |
| All internal links on homepage point only at the homepage                  | medium   |
| No `<nav>` landmark found                                                  | medium   |
| No on-site search mechanism detected (form + SearchAction)                 | medium   |
| No prominent call-to-action buttons or links (long page, 0 CTA verbs)      | high     |
| Only one CTA on the homepage                                               | low      |
| No related/recent/featured content block found (long page)                 | medium   |
| Long list without pagination or `rel=next` signals                         | low      |
| Footer contains very few links (<5)                                        | low      |
| No `<footer>` landmark present                                             | medium   |
| HTML payload is very large (>500 KB)                                       | medium   |
| No preconnect/dns-prefetch resource hints for third-party origins          | low      |

## Proactive (beyond-defect) recommendations surfaced via severity=info/low

- HSTS, charset, viewport, viewport best-practices
- Sitemap to GSC/Bing submission advisory
- og:image absolute URL, twitter cards full set
- Copyright year dynamic templating
- Third-party preconnect/dns-prefetch hints
- Pagination vs infinite-scroll best practice
- Rich footer vs. thin footer
- Breadcrumb visual trails (mirrors structured BreadcrumbList)

The `suggested_action.summary` for every finding targets **root causes**
not symptoms, and cites the exact mechanism by which the change improves
AI discovery or on-site engagement.
