# AI and answer-engine agents, by role

The single most useful distinction in this whole audit. Agents do different jobs, and
blocking them has different consequences, so a report that treats them alike is wrong.

The registry lives in `scripts/collect_site_pack.py` (`AI_AGENTS`) so the code and this
document cannot drift apart. Access is evaluated by reading robots.txt rules for each
token — never by sending requests with someone else's user-agent.

## User-triggered fetchers — blocking these costs the most

They fetch a page *because a user just asked something*. The demand already exists.

| Token | Operator |
| --- | --- |
| `ChatGPT-User` | OpenAI |
| `Claude-User` | Anthropic |
| `Perplexity-User` | Perplexity |

Blocking these is reported `critical`. The answer still gets written; it is just written
without you, from whatever source did respond.

## Retrieval crawlers — build the indexes assistants search

| Token | Operator |
| --- | --- |
| `OAI-SearchBot` | OpenAI (ChatGPT search) |
| `Claude-SearchBot` | Anthropic |
| `PerplexityBot` | Perplexity |
| `Googlebot` | Google Search, which grounds AI Overviews |
| `Bingbot` | Microsoft, which grounds Copilot |
| `Applebot` | Apple (Siri, Spotlight) |
| `DuckAssistBot` | DuckDuckGo |
| `Amazonbot`, `YouBot` | Amazon, You.com |

Blocking these is reported `high`, or `critical` for Googlebot and Bingbot, because those
two carry the general-purpose indexes most answers are grounded in.

## Training crawlers — a policy choice, not a defect

| Token | Operator |
| --- | --- |
| `GPTBot` | OpenAI |
| `ClaudeBot`, `anthropic-ai` | Anthropic |
| `Google-Extended` | Google (Gemini training/grounding; does not affect Search indexing) |
| `Applebot-Extended` | Apple |
| `CCBot` | Common Crawl |
| `Bytespider`, `meta-externalagent`, `cohere-ai`, `Diffbot`, `Timpibot` | Various |

Reported `info` with the tradeoff stated: the model is less likely to describe the brand
correctly from memory, while live citation still works if retrieval agents are allowed.
Many organisations make this trade deliberately. Calling it a bug is a false positive that
discredits the rest of the report.

## The mistake this catches most often

A site blocks `GPTBot` to opt out of training, and the same group — or a copied
`Disallow: /` — also blocks `ChatGPT-User` and `OAI-SearchBot`. The brand keeps the
training opt-out it wanted and loses the citations it wanted to keep, usually without
anyone noticing, because nothing breaks visibly.

Robots directives are also honoured only by compliant agents, and this evaluation says
nothing about non-compliant ones. That is worth stating in the report rather than implying
that robots.txt is an access control.
