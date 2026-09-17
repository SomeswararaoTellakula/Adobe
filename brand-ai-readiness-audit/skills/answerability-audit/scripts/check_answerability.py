#!/usr/bin/env python3
"""
check_answerability.py - stage 4 of the audit: having read the page, can a machine quote a
clear fact from it?

Offline analyzer over a site pack. A page can be perfectly crawlable, fully rendered and
richly marked up and still never be cited, because nothing on it is a self-contained,
checkable statement. This analyzer looks for the properties that make a passage quotable:
an explicit definition of what the brand is, facts stated as text rather than implied,
retrievable chunk boundaries, and specifics instead of adjectives.

  python3 check_answerability.py --pack ./pack [--out findings.json]
"""

import argparse
import json
import os
import sys

SKILL = "answerability-audit"
DIMENSION = "discoverability.answerability"

PRIMARY = {"home", "product", "pricing", "article", "docs", "about"}
SPA_FRAMEWORKS = {"next", "nuxt", "react", "angular", "vue", "svelte", "remix", "gatsby"}


def render_blocked(page):
    """True when a page is thin because JavaScript withheld it, not because it is badly written.

    Excluding these keeps one root cause (client-side rendering) from generating a second set
    of findings here. A short page that WAS served is still assessed - a pricing page reading
    'contact us for pricing' is the exact defect this analyzer looks for.
    """
    thin = page.get("word_count", 0) < 120
    shell = page.get("spa_shell") or (page.get("noscript_words", 0) > 0 and page.get("word_count", 0) < 60)
    return thin and (shell or bool(set(page.get("framework", [])) & SPA_FRAMEWORKS))


def load_pack(pack):
    with open(os.path.join(pack, "meta.json")) as f:
        meta = json.load(f)
    pages, path = [], os.path.join(pack, "pages.jsonl")
    if os.path.exists(path):
        with open(path) as f:
            pages = [json.loads(line) for line in f if line.strip()]
    return meta, pages


def finding(check_id, title, severity, confidence, urls, evidence, action, effect,
            steps=(), scales=False, verify="", tags=()):
    urls = sorted(set(urls))
    return {
        "check_id": check_id, "title": title, "severity": severity, "confidence": confidence,
        "affected_urls": urls[:20], "affected_count": len(urls), "evidence": evidence,
        "scales_with_coverage": scales, "verify_by": verify, "tags": list(tags),
        "suggested_action": {"summary": action, "steps": list(steps), "expected_effect": effect},
    }


def emit(findings, notes, out):
    payload = {"skill": SKILL, "dimension": DIMENSION, "version": "1.0.0",
               "findings": sorted(findings, key=lambda f: f["check_id"]), "notes": notes}
    text = json.dumps(payload, indent=2)
    if out:
        with open(out, "w") as f:
            f.write(text)
    else:
        print(text)


def analyze(meta, pages):
    out, notes = [], []
    ok = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    # Pages withheld by JavaScript are a rendering problem, not an answerability problem.
    # Excluding only those stops one root cause producing two sets of findings.
    readable = [p for p in ok if not render_blocked(p)]
    n = len(readable)
    brand = meta.get("brand_tokens", [])
    # brand_tokens are for substring matching; brand_name is the human-readable name and is
    # the only one that should ever appear in evidence text shown to a reader.
    brand_name = meta.get("brand_name") or (brand[0].title() if brand else "")
    if not readable:
        notes.append("No page in the sample carried enough served text to assess answerability; "
                     "see the extractability findings first.")
        return out, notes

    # -- ANS-001: no definitional sentence ------------------------------------
    identity_pages = [p for p in readable if p.get("page_class") in ("home", "about")]
    home_pages = [p for p in identity_pages if p.get("page_class") == "home"]
    # definitional_sentence is tri-state: True / False / None where None means the page's
    # language has no lexicon in the collector, so the question was never asked. Treating
    # None as "missing" reported a HIGH defect on well-written non-English sites whose
    # opening sentence defined the brand perfectly.
    unchecked = [p for p in identity_pages if p.get("definitional_sentence") is None]
    checkable = [p for p in identity_pages if p.get("definitional_sentence") is not None]
    checkable_home = [p for p in home_pages if p.get("definitional_sentence") is not None]
    defined_anywhere = any(p.get("definitional_sentence") for p in checkable)
    home_defined = (any(p.get("definitional_sentence") for p in checkable_home)
                    if checkable_home else defined_anywhere)
    if unchecked and not checkable:
        langs = sorted({(p.get("html_lang") or "unspecified") for p in unchecked})
        notes.append(f"Definitional-sentence check skipped: no phrase lexicon for the page language "
                     f"({', '.join(langs)}). Reported as unchecked rather than as a defect.")
    if brand and checkable and not home_defined:
        out.append(finding(
            "ANS-001", "No plain sentence stating what the brand is",
            "medium" if defined_anywhere else "high", "medium",
            [p["url"] for p in (home_pages or identity_pages)],
            ("The about page defines the brand but the homepage does not. "
             if defined_anywhere else "") +
            f"No sampled identity page opens with a sentence of the form "
            f"'{brand_name} is a <category> that <does what> for <whom>'. Sampled openings instead lead "
            "with positioning language. When an assistant is asked 'what is this company', there is no "
            "sentence on the site it can lift and repeat.",
            "Open the homepage and about page with one literal, self-contained definitional sentence, then keep "
            "the positioning copy underneath it.",
            "Gives every downstream system a single sentence to quote, and makes the brand describable without "
            "inference.",
            steps=["Write one sentence naming the brand, its category, what it does and who it serves - no "
                   "metaphors, no slogan.",
                   "Place it in the first paragraph of the homepage and repeat it verbatim in the about page, "
                   "the Organization description property and the meta description.",
                   "Keep the wording identical across all of those surfaces so repetitions corroborate each other.",
                   "Example shape: 'Acme Robotics is an industrial automation company in Pune that builds "
                   "pick-and-place arms for electronics manufacturers.'"],
            verify="Read the first 50 words of the homepage and confirm whether a stranger could state the "
                   "company's category from them.",
            tags=("identity", "quotability")))

    # -- ANS-002: brand absent from body content -------------------------------
    if brand:
        # Brand-mention counting is language-neutral (it is a substring match), so this
        # check is safe on any language.
        anonymous = [p for p in readable
                     if p.get("page_class") in PRIMARY and p.get("brand_mentions", 0) == 0
                     and p.get("word_count", 0) >= 120]
        if anonymous and len(anonymous) >= max(2, 0.4 * n):
            out.append(finding(
                "ANS-002", "Body copy never names the brand", "medium", "high",
                [p["url"] for p in anonymous],
                f"{len(anonymous)}/{n} content pages never mention '{brand_name}' in the body, relying on "
                "'we' and 'our'. Retrieval systems index passages, not whole pages: a chunk that says 'we "
                "reduce downtime by 30%' loses its subject the moment it is separated from the header.",
                "Name the brand explicitly at least once per major section, especially in claims and headings.",
                "Extracted passages stay attributable to the brand instead of becoming orphaned statements.",
                steps=["Replace the first 'we' of each major section with the brand name.",
                       "Put the brand name in claim sentences that carry a number.",
                       "Do not over-do it - once per section is enough to keep chunks self-contained."],
                scales=True, tags=("chunking", "identity")))

    # -- ANS-003: the key fact is not text ------------------------------------
    pricing = [p for p in readable if p.get("page_class") == "pricing"]
    priceless = [p for p in pricing if not p.get("price_numbers")]
    if pricing and priceless:
        out.append(finding(
            "ANS-003", "Pricing pages contain no price", "high", "high", [p["url"] for p in priceless],
            f"{len(priceless)}/{len(pricing)} pricing page(s) contain no currency figure in the served text. "
            "'Contact us for pricing' cannot be quoted, so the brand is absent from every comparison answer "
            "where cost is the question - and a competitor who publishes a number gets cited instead.",
            "Publish concrete numbers: list prices, starting-from prices, or a worked example with the "
            "variables named.",
            "Makes the brand eligible for price and comparison questions rather than silently excluded.",
            steps=["If list pricing is genuinely impossible, publish a 'starting from' figure, a typical "
                   "deployment range, or a worked example ('a 50-seat rollout is typically Rs 4-6 lakh a year').",
                   "State what the price includes and what drives it up or down, as text.",
                   "Mirror the same figures in Offer/PriceSpecification structured data.",
                   "Date the pricing page so the number is checkable rather than suspect."],
            tags=("quotability", "pricing")))

    fact_pages = [p for p in readable if p.get("page_class") in ("product", "docs")]
    tableless = [p for p in fact_pages
                 if p.get("counts", {}).get("table", 0) == 0
                 and p.get("counts", {}).get("dl", 0) == 0
                 and p.get("counts", {}).get("ul", 0) + p.get("counts", {}).get("ol", 0) <= 1]
    if fact_pages and len(tableless) >= max(2, 0.6 * len(fact_pages)):
        out.append(finding(
            "ANS-004", "Specifications are written as prose rather than structured text", "medium", "high",
            [p["url"] for p in tableless],
            f"{len(tableless)}/{len(fact_pages)} product/documentation page(s) contain no table, definition "
            "list or meaningful list. Attribute-value facts buried in paragraphs are far harder to extract "
            "reliably than the same facts in a table.",
            "Present attribute-value facts in HTML tables or definition lists.",
            "Specifications become individually extractable, which is what comparison answers are assembled from.",
            steps=["Convert spec blocks to <table> with a header row, or <dl> pairs.",
                   "Keep one fact per row, with units in the cell.",
                   "Do not replace prose entirely - add the structured version alongside the explanation."],
            scales=True, tags=("quotability", "structure")))

    # -- ANS-005: adjectives instead of facts ---------------------------------
    fluffy = [p for p in readable
              if p.get("superlatives", 0) >= 4 and p.get("superlatives", 0) > 2 * p.get("specific_facts", 0)]
    if fluffy:
        worst = sorted(fluffy, key=lambda p: -p.get("superlatives", 0))[:5]
        out.append(finding(
            "ANS-005", "Copy is dense with marketing adjectives and thin on checkable facts", "medium", "high",
            [p["url"] for p in fluffy],
            "; ".join(f"{p['url']}: {p.get('superlatives')} superlative phrases vs "
                      f"{p.get('specific_facts')} specific figures" for p in worst) +
            f" ({len(fluffy)}/{n} pages). Phrases like 'world-class' and 'industry-leading' are unfalsifiable, "
            "so there is nothing to extract, nothing to corroborate elsewhere, and nothing worth citing.",
            "Replace superlatives with specific, checkable facts: numbers, dates, named customers, measured outcomes.",
            "Turns unquotable claims into statements an assistant can repeat and a third party can confirm.",
            steps=["For each superlative, ask 'compared to what, measured how?' and write that instead.",
                   "'Industry-leading uptime' becomes '99.95% measured uptime across 2025, published monthly'.",
                   "Attach a source or a date to every number so it is verifiable.",
                   "Facts that are repeated elsewhere on the web are far more likely to be believed - specifics "
                   "are what other sites can repeat."],
            scales=True, tags=("quotability", "corroboration")))

    # -- ANS-006: chunk boundaries --------------------------------------------
    walls = [p for p in readable if p.get("long_paragraphs", 0) >= 2 and p.get("word_count", 0) >= 300]
    if walls:
        out.append(finding(
            "ANS-006", "Long unbroken paragraphs give retrieval nothing to split on", "low", "high",
            [p["url"] for p in walls],
            f"{len(walls)}/{n} pages contain two or more paragraphs over 120 words. Long blocks get chunked "
            "arbitrarily, so the quotable sentence arrives surrounded by unrelated text and is passed over.",
            "Break long passages into short paragraphs under clear subheadings.",
            "Each idea occupies its own retrievable chunk.",
            steps=["Split paragraphs at the point where the subject changes.",
                   "Add a descriptive H2/H3 above each block.",
                   "Lead each section with its conclusion, then support it."],
            scales=True, tags=("chunking",)))

    headless = [p for p in readable
                if p.get("word_count", 0) >= 400 and len(p.get("headings", [])) <= 2]
    if headless:
        out.append(finding(
            "ANS-007", "Long pages have almost no subheadings", "low", "high", [p["url"] for p in headless],
            f"{len(headless)}/{n} pages carry 400+ words under two or fewer headings.",
            "Add a subheading for each distinct question the section answers.",
            "Sections become independently addressable and independently quotable.",
            steps=["Outline the questions the page actually answers.",
                   "Turn each into an H2 phrased the way a user would ask it.",
                   "Keep sections to a few hundred words."],
            scales=True, tags=("chunking", "structure")))

    # -- ANS-008: no question-shaped content ----------------------------------
    question_total = sum(p.get("question_headings", 0) for p in readable)
    if n >= 4 and question_total == 0:
        out.append(finding(
            "ANS-008", "No content is phrased as an answer to a question", "medium", "high",
            [p["url"] for p in readable if p.get("page_class") in PRIMARY],
            f"Across {n} sampled pages, not one heading is phrased as a question or a direct informational "
            "statement. Assistants match a user's question against passages; pages organised around "
            "themes rather than questions match nothing.",
            "Add question-shaped headings and a short FAQ covering the questions buyers actually ask, with a "
            "direct answer in the first sentence under each.",
            "Creates direct matches for the questions users ask, with the answer already isolated as a chunk.",
            steps=["Collect real questions from sales calls, support tickets and site search logs.",
                   "Add an FAQ section (or a FAQ page) with each question as a heading.",
                   "Answer in the first sentence, in full sentences that stand alone without the question.",
                   "Mark it up as FAQPage so the pairing is explicit."],
            tags=("quotability", "faq")))

    # -- ANS-009: no citable anchors ------------------------------------------
    anchorless = [p for p in readable
                  if len(p.get("headings", [])) >= 4 and p.get("heading_ids", 0) == 0]
    if anchorless and len(anchorless) >= max(2, 0.5 * n):
        out.append(finding(
            "ANS-009", "Headings have no id attributes, so sections cannot be linked to", "low", "high",
            [p["url"] for p in anchorless],
            f"{len(anchorless)}/{n} multi-section pages have no id on any heading. A citation can then only "
            "point at the whole page, and a visitor arriving from an answer lands at the top with no idea "
            "where the quoted line came from.",
            "Give every heading a stable id and expose visible anchor links.",
            "Answers can deep-link to the exact passage, and arriving visitors land on the relevant section.",
            steps=["Auto-generate slug ids from heading text in the template.",
                   "Keep ids stable across edits - changing them breaks existing citations.",
                   "Add a visible anchor affordance so people can copy section links."],
            scales=True, tags=("citation", "structure")))

    # -- ANS-010: boilerplate dominance ---------------------------------------
    noisy = [p for p in readable if p.get("boiler_ratio", 0) >= 0.6 and p.get("word_count", 0) < 400]
    if noisy:
        out.append(finding(
            "ANS-010", "Navigation and footer text outweighs page content", "low", "high",
            [p["url"] for p in noisy],
            "; ".join(f"{p['url']}: {p.get('boiler_words')} words of nav/footer vs {p.get('word_count')} words "
                      f"of content" for p in noisy[:5]) +
            f" ({len(noisy)} page(s)). Repeated boilerplate dilutes each page's distinctive signal and makes "
            "pages look near-duplicate to a retrieval system.",
            "Increase the substance of thin pages and keep repeated navigation compact.",
            "Each page carries more unique signal relative to shared furniture.",
            steps=["Expand thin pages with the specifics a visitor actually needs.",
                   "Collapse mega-menus and long footer link lists where possible.",
                   "Use semantic <main> so the primary content is unambiguous."],
            scales=True, tags=("signal", "structure")))

    notes.append(f"Answerability assessed on {n} pages whose text was actually served "
                 f"({len(ok) - n} page(s) excluded as rendering problems rather than writing problems).")
    return out, notes


def main():
    ap = argparse.ArgumentParser(description="Answerability analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages = load_pack(args.pack)
    findings, notes = analyze(meta, pages)
    emit(findings, notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
