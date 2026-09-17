# Access check catalogue

Each check names its trigger, its evidence and the gate that keeps it from firing wrongly.

| Id | Fires when | Evidence recorded | False-positive gate |
| --- | --- | --- | --- |
| `ACC-001` | robots.txt disallows `/` for a user-triggered fetcher | Agent labels and the matching rule | Role classification: only user-fetch agents |
| `ACC-002` | robots.txt disallows `/` for a retrieval crawler | Agent labels, escalated for Googlebot/Bingbot | Training agents excluded |
| `ACC-003` | robots.txt disallows `/` for a training crawler | Agent labels | Emitted as `info`, never counted as a defect |
| `ACC-004` | `User-agent: *` group contains `Disallow: /` | The rule itself | — |
| `ACC-005` | robots.txt returns anything but 200 or 404 | Status code | 404 is fine — it means no rules |
| `ACC-006` | `noindex`, `nosnippet`, `max-snippet:0`, `noarchive`, `none` in meta robots or `X-Robots-Tag` | Directives found, page count | Reports directive names so a deliberate `max-snippet` policy is visible |
| `ACC-007` | Homepage returns 401/403/429 or a challenge body to a plain client | Status, `cf-mitigated`, challenge markers | Medium confidence with a browser-verification note: only a browser comparison proves a bot wall |
| `ACC-008` | No parseable sitemap from robots or conventional paths | Paths tried, statuses | — |
| `ACC-009` | Sitemap found but not declared in robots.txt | Sitemap URLs | Low severity |
| `ACC-010` | www and apex both return 200 without redirecting | Both hosts and final URLs | Only fires when the variant genuinely serves 200 |
| `ACC-011` | `http://` serves 200 without upgrading | Final URL | Only checked when the site was reached over https |
| `ACC-012` | Half or more sampled pages lack `rel=canonical` | Count over sample | Requires at least 4 pages sampled |
| `ACC-013` | `rel=canonical` points at a different host | The offending pages | Notes that syndication can be intentional |
| `ACC-014` | Sampled URLs return 4xx/5xx or fail | Failed URLs and statuses | URLs came from the site's own sitemap or links |
| `ACC-015` | A URL that cannot exist returns 200 | Probe URL and status | Uses an improbable probe path |
| `ACC-016` | Median response >2500 ms, or any page >5000 ms | Median and slow-page count | Reports both figures so a single outlier is visible |

## Fix patterns worth knowing

**Bot walls.** The durable fix is a verified-bot allowlist at the CDN validated by reverse
DNS or published IP ranges, not by user-agent string — user-agent allowlisting is trivially
spoofed and gives a false sense of control. Never serve different content to bots than to
users: cloaking is fragile and penalised.

**Snippet directives.** `nosnippet` and `max-snippet:0` are the quiet killers here. They do
not stop indexing, so traffic reports look normal, but they forbid reproducing text — which
is precisely what a citation is. Publishers who set them years ago for a different reason
are the common case.

**robots.txt returning 5xx.** Some crawlers treat a server error on robots.txt as "crawl
nothing at all". A flaky robots endpoint can suppress a whole site intermittently, which
makes it maddening to diagnose from analytics alone.
