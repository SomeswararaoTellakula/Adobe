#!/usr/bin/env python3
"""
check_freshness.py - stage 5 of the audit: will a machine trust the fact enough to repeat it?

Offline analyzer over a site pack. Extraction is not citation. A fact that is undated,
unattributed, and stated in exactly one place on the internet is fragile: systems prefer
claims that are current, attributable, and repeated by independent sources, and they avoid
entities they cannot tell apart from a namesake.

  python3 check_freshness.py --pack ./pack [--out findings.json]

Off-site corroboration cannot be measured from the site alone. This script measures what is
observable on-site (outbound identity links, dates, attribution) and emits explicit
verification tasks for the agent to run as searches; see references/corroboration-surfaces.md.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timezone

SKILL = "freshness-corroboration-audit"
DIMENSION = "discoverability.trust"

# Independent surfaces that commonly carry a second, corroborating description of a brand.
CORROBORATION_HOSTS = {
    "wikipedia.org": "Wikipedia", "wikidata.org": "Wikidata", "linkedin.com": "LinkedIn",
    "crunchbase.com": "Crunchbase", "github.com": "GitHub", "g2.com": "G2",
    "capterra.com": "Capterra", "trustpilot.com": "Trustpilot", "glassdoor.com": "Glassdoor",
    "apps.apple.com": "App Store", "play.google.com": "Google Play", "producthunt.com": "Product Hunt",
    "youtube.com": "YouTube", "facebook.com": "Facebook", "instagram.com": "Instagram",
    "x.com": "X", "twitter.com": "X", "yelp.com": "Yelp", "bbb.org": "BBB",
    "opencorporates.com": "OpenCorporates", "zaubacorp.com": "ZaubaCorp",
}
TIME_SENSITIVE = {"pricing", "product", "docs"}


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


def emit(findings, notes, tasks, out):
    payload = {"skill": SKILL, "dimension": DIMENSION, "version": "1.0.0",
               "findings": sorted(findings, key=lambda f: f["check_id"]),
               "agent_verification_tasks": tasks, "notes": notes}
    text = json.dumps(payload, indent=2)
    if out:
        with open(out, "w") as f:
            f.write(text)
    else:
        print(text)


def objects_of(page, wanted):
    for obj in page.get("jsonld_objects", []):
        t = obj.get("@type")
        names = {t.lower()} if isinstance(t, str) else {str(x).lower() for x in (t or [])}
        if names & wanted:
            yield obj


def year_of(value):
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def analyze(meta, pages):
    out, notes, tasks = [], [], []
    ok = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    n = len(ok)
    now_year = datetime.now(timezone.utc).year
    brand = meta.get("brand_tokens", [])
    site = meta.get("site", "")
    if not ok:
        notes.append("No successfully parsed pages in the pack; trust checks skipped.")
        return out, notes, tasks

    articles = [p for p in ok if p.get("page_class") == "article"]

    # -- FRC-001: undated content ---------------------------------------------
    undated = [p for p in articles if not p.get("visible_dates")
               and not any(o.get("datePublished") or o.get("dateModified")
                           for o in objects_of(p, {"article", "blogposting", "newsarticle", "techarticle"}))]
    if articles and undated:
        out.append(finding(
            "FRC-001", "Articles carry no visible or machine-readable date", "medium", "high",
            [p["url"] for p in undated],
            f"{len(undated)}/{len(articles)} article page(s) show no date in the text and declare none in "
            "structured data. An undated claim cannot be checked for currency, so a system deciding between "
            "two sources has a reason to prefer the dated one.",
            "Show a published date and a genuine last-updated date on every article, and mirror both in markup.",
            "Content becomes datable, which is a precondition for being treated as current.",
            steps=["Render 'Published <date>' and, where relevant, 'Updated <date>' near the title.",
                   "Emit datePublished and dateModified in ISO 8601 in the Article markup.",
                   "Only change dateModified when the content actually changes."],
            scales=True, tags=("freshness", "dates")))

    # -- FRC-002: stale signals -----------------------------------------------
    stale_reasons = []
    copyright_years = sorted({year_of(y) for p in ok for y in p.get("copyright_years", []) if year_of(y)})
    if copyright_years and max(copyright_years) < now_year - 1:
        stale_reasons.append(f"the newest footer copyright year on the site is {max(copyright_years)}")
    # "The newest article is from <year>" is only meaningful if dates were actually found on
    # a reasonable share of the articles. With dates parsed on one article out of twelve,
    # what we have is one old date and eleven unknowns - not evidence the site is dormant.
    # A national daily was reported as abandoned on exactly that basis, because its dates
    # were written in a format the collector could not read.
    dated_articles = [p for p in articles
                      if any(year_of(d) for d in p.get("visible_dates", []))]
    article_years = sorted({year_of(d) for p in dated_articles
                            for d in p.get("visible_dates", []) if year_of(d)})
    enough = len(dated_articles) >= max(3, 0.5 * len(articles)) if articles else False
    if article_years and enough and max(article_years) < now_year - 1:
        stale_reasons.append(f"the most recent dated article is from {max(article_years)} "
                             f"(dates read on {len(dated_articles)}/{len(articles)} article pages)")
    elif articles and not enough and article_years:
        notes.append(f"Staleness not asserted from article dates: a date could be read on only "
                     f"{len(dated_articles)}/{len(articles)} article page(s), which is too few to "
                     "conclude anything about the site's currency. Dates may be present in a format "
                     "this collector does not parse.")
    sitemap_lastmods = [year_of(lm) for s in meta.get("sitemaps", []) for lm in s.get("lastmods", []) if year_of(lm)]
    if sitemap_lastmods and max(sitemap_lastmods) < now_year - 1:
        stale_reasons.append(f"the newest sitemap lastmod is {max(sitemap_lastmods)}")
    if stale_reasons:
        out.append(finding(
            "FRC-002", "The site reads as abandoned", "medium", "high",
            [meta.get("origin", "")] + [p["url"] for p in articles[:5]],
            "Staleness signals: " + "; ".join(stale_reasons) +
            f" (current year {now_year}). Age signals push a source down the ranking when a fresher one says "
            "something similar, and they make every undated claim on the site look uncertain too.",
            "Refresh the signals that carry a date, and put a real review cadence behind the pages that matter.",
            "Removes the 'abandoned site' signal and lets current claims be treated as current.",
            steps=["Make the footer copyright year dynamic.",
                   "Review the top 10 revenue-relevant pages, correct anything out of date, and stamp them "
                   "with a real 'last reviewed' date.",
                   "Regenerate the sitemap with accurate lastmod values.",
                   "Never mass-update dates without changing content - fake freshness is detectable and "
                   "costs trust when caught."],
            tags=("freshness",)))

    # -- FRC-003: uniform machine dates (fake freshness) ----------------------
    modified = [str(o.get("dateModified"))[:10] for p in ok
                for o in objects_of(p, {"article", "blogposting", "newsarticle", "webpage", "techarticle"})
                if o.get("dateModified")]
    if len(modified) >= 4:
        common, count = Counter(modified).most_common(1)[0]
        if count == len(modified) and common >= datetime.now(timezone.utc).strftime("%Y-%m-%d"):
            out.append(finding(
                "FRC-003", "Every page reports the same current dateModified", "low", "medium",
                [p["url"] for p in ok if p.get("jsonld_count")],
                f"All {len(modified)} dateModified values in the sample are {common}. A build timestamp "
                "applied site-wide tells a system nothing about which content actually changed, and reads as "
                "manufactured freshness.",
                "Emit dateModified from real content-change timestamps, not from the build.",
                "Freshness signals regain meaning and stop looking synthetic.",
                steps=["Source dateModified from the CMS record's updated_at, per page.",
                       "Leave it unchanged when only templates or assets change."],
                tags=("freshness", "integrity")))

    # -- FRC-004: attribution --------------------------------------------------
    unattributed = [p for p in articles
                    if not any(o.get("author") for o in objects_of(
                        p, {"article", "blogposting", "newsarticle", "techarticle"}))
                    and not re.search(r"\b(?:by|author|written by)\b", p.get("text_main_sample", "")[:400], re.I)]
    if articles and len(unattributed) >= max(2, 0.6 * len(articles)):
        out.append(finding(
            "FRC-004", "Content has no named author or attribution", "low", "high",
            [p["url"] for p in unattributed],
            f"{len(unattributed)}/{len(articles)} article page(s) name no author in text or markup. "
            "Anonymous claims are weaker to repeat than attributed ones, particularly on topics where "
            "expertise matters.",
            "Attribute content to a named person with a real profile page, and link the two.",
            "Claims become attributable, which raises their odds of being repeated with the brand named.",
            steps=["Add a visible byline linking to an author page with credentials.",
                   "Mark the author up as a Person node with sameAs links to their professional profiles.",
                   "For institutional content, attribute to the Organization explicitly rather than leaving "
                   "it blank."],
            scales=True, tags=("attribution", "trust")))

    # -- FRC-005: outbound identity links --------------------------------------
    external_hosts = {h for p in ok for h in p.get("external_domains", [])}
    linked_surfaces = sorted({label for host, label in CORROBORATION_HOSTS.items()
                              if any(h == host or h.endswith("." + host) for h in external_hosts)})
    sameas = set()
    for p in ok:
        for obj in p.get("jsonld_objects", []):
            value = obj.get("sameAs")
            if isinstance(value, str):
                sameas.add(value)
            elif isinstance(value, list):
                sameas.update(str(v) for v in value)
    sameas_surfaces = {label for host, label in CORROBORATION_HOSTS.items()
                       if any(host in str(v).lower() for v in sameas)}
    surfaces = sorted(set(linked_surfaces) | sameas_surfaces)
    if len(surfaces) <= 2:
        out.append(finding(
            "FRC-005", "The site barely connects itself to any independent source", "medium", "medium",
            [meta.get("origin", "")],
            f"Only {len(surfaces)} recognised external identity surface(s) across outbound links and sameAs"
            + (f" ({', '.join(surfaces)})" if surfaces else "") +
            f"; {len(sameas)} sameAs value(s) declared in total. A claim that exists in exactly one place "
            "on the web is fragile; systems weight facts that several unrelated sources state the same way.",
            "Establish and link a consistent presence on the independent surfaces that matter for this category, "
            "using identical core facts everywhere.",
            "Turns single-source claims into corroborated ones, which is what makes a fact safe to repeat.",
            steps=["Claim and complete the profiles that apply: Wikidata, LinkedIn, Crunchbase, industry "
                   "registries, app stores, review platforms, and the trade bodies in your sector.",
                   "Use the exact same one-sentence description, legal name, founding year and location on "
                   "every one of them - agreement across sources is the entire mechanism.",
                   "Link them from the site (footer and about page) and declare them in Organization.sameAs.",
                   "Pursue independent mentions - customer case studies published by the customer, conference "
                   "listings, directories, press - so the facts appear on domains you do not control."],
            verify="Search the brand name and inspect which independent domains describe it, and whether they "
                   "agree with the site.",
            tags=("corroboration", "entity")))

    # -- FRC-006: entity disambiguation ----------------------------------------
    # This check is deliberately narrow: it is about whether PLAIN TEXT on the page pairs
    # the brand with a category, not about whether Organization markup exists (that is
    # SD-004.about / SD-004.home's job - checking it here too just duplicates that finding
    # under a scarier headline). A brand whose homepage H1, meta description or opening
    # paragraph already states its category has disambiguated itself in the one place that
    # matters most: what a human or a fetcher reads first. Requiring structured data on top
    # of that produced false positives on sites that were never actually ambiguous.
    # Category nouns across the Latin-script languages the collector has lexicons for.
    # Many are near-cognates, which is what makes one combined pattern workable; the
    # language-support gate below covers the rest.
    CATEGORY_WORDS = re.compile(
        r"\b(?:software|platform|plataforma|plateforme|piattaforma|tool|tools|app|application|"
        r"aplicaci\w+|suite|product|products|producto\w*|produit\w*|prodotto\w*|produkt\w*|"
        r"service|services|servicio\w*|servi\w+|dienst\w*|solution|solutions|soluci\w+|"
        r"sdk|api|framework|engine|infrastructure|dashboard|analytics|observability|"
        r"agency|agencia|agence|agenzia|agentur|studio|estudio|atelier|"
        r"clinic|cl\w+nica|clinique|klinik|hospital|h\w+pital|ospedale|krankenhaus|"
        r"school|escuela|\w*cole|scuola|schule|college|university|universi\w+|universit\w+|"
        r"restaurant|restaurante|ristorante|caf\w*|cafeteria|hotel|albergo|"
        r"consult\w*|asesor\w*|berat\w*|manufactur\w*|fabricante|fabricant|produttore|hersteller|"
        r"panader\w+|boulangerie|bakery|b\w+ckerei|panificio|"
        r"logistics|log\w+stica|logistique|bank|banco|banque|banca|insurance|seguros|assurance|"
        r"retail|store|shop|tienda|magasin|negozio|laden|law|legal|abogad\w+|avocat|"
        r"marketing|design|dise\w+o|engineering|ingenier\w+|robotics|rob\w+tica|"
        r"labs?|laboratori\w+|foundation|fundaci\w+n|ngo|institute|institut\w*|"
        r"network|red|marketplace|mercado|community|comunidad)\b", re.I)
    home_about = [p for p in ok if p.get("page_class") in ("home", "about")]
    plain_text = " ".join(
        (p.get("title", "") + " " + p.get("meta_description", "") + " " + p.get("first_chunk", ""))
        for p in home_about)
    descriptor_in_text = bool(CATEGORY_WORDS.search(plain_text))
    definitional = any(p.get("definitional_sentence") for p in home_about)
    # A page whose language has no lexicon cannot be judged on either signal.
    lexicon_ok = any(p.get("lang_lexicon", True) for p in home_about)
    if home_about and not lexicon_ok:
        notes.append("Entity-disambiguation check skipped: no category-word lexicon for the page "
                     "language. Reported as unchecked rather than as a defect.")
    if home_about and lexicon_ok and not (descriptor_in_text or definitional):
        out.append(finding(
            "FRC-006", "Nothing in the page text distinguishes the brand from anything else with the same name",
            "medium", "medium", [meta.get("origin", "")],
            "Checked the homepage/about title, meta description and opening paragraph: none pairs the brand "
            "name with a category word, and no sentence defines what the brand is. When several entities "
            "share a name, a system with nothing to separate them in the text it actually reads either picks "
            "the better-described one or blends them together.",
            "State the brand's category in the homepage title, meta description and opening sentence.",
            "Makes the brand resolvable as a distinct entity from plain text alone, before structured data "
            "is even consulted.",
            steps=["Use the pattern '<Brand> - <category> in <place>' in the homepage title and meta description.",
                   "Open the homepage with one sentence naming the category and what the brand does.",
                   "If a well-known namesake exists, say what you are not where it is natural to "
                   "('Ajanta Systems, the Pune-based logistics software firm')."],
            verify="Search the brand name alone and see which entity dominates the results.",
            tags=("entity", "ambiguity")))

    # -- FRC-007: unsourced claims ---------------------------------------------
    claim_pages = [p for p in ok if p.get("specific_facts", 0) >= 3 and p.get("links_external", 0) == 0]
    if claim_pages and len(claim_pages) >= max(2, 0.4 * n):
        out.append(finding(
            "FRC-007", "Statistics are stated without a source", "low", "medium",
            [p["url"] for p in claim_pages],
            f"{len(claim_pages)}/{n} pages carry three or more numeric claims and link to nothing external. "
            "An unsourced number cannot be corroborated, so it is the first thing a cautious system drops.",
            "Cite the source of every statistic, and link to it where it is public.",
            "Numbers become checkable, which is what allows them to be repeated with the brand attached.",
            steps=["Attribute each figure inline ('per our 2026 customer survey (n=412)').",
                   "Link to the underlying report, filing or dataset when it is public.",
                   "Publish your own data as a citable page - original data is the most reliable way to be "
                   "quoted and linked by others."],
            scales=True, tags=("corroboration", "claims")))

    # -- agent verification tasks (require the open web, not the pack) ---------
    if brand:
        name = meta.get("brand_name") or brand[0]
        tasks = [
            {"id": "V-001", "purpose": "Entity resolution",
             "query": f"{name}", "check": "Does this brand dominate results for its bare name, or does a "
             "namesake? Record the competing entities."},
            {"id": "V-002", "purpose": "Independent corroboration",
             "query": f"\"{name}\" -site:{site}",
             "check": "How many independent domains describe the brand, and do their descriptions match the "
             "site's own one-sentence definition?"},
            {"id": "V-003", "purpose": "Reference-surface presence",
             "query": f"{name} wikipedia OR wikidata OR crunchbase OR linkedin",
             "check": "Which structured reference surfaces carry an entry, and is the data on them current?"},
            {"id": "V-004", "purpose": "Assistant-visible description",
             "query": f"what is {name}",
             "check": "Ask an assistant this directly. Record what it says, whether it cites the brand's own "
             "site, and whether anything is wrong or out of date. That answer is the ground truth this audit "
             "is trying to change."},
            {"id": "V-005", "purpose": "Category competition",
             "query": f"best <category> for <use case> - the question {name} wants to win",
             "check": "Which sources get cited for the category question the brand wants to win, and what do "
             "those pages do that this site does not?"},
        ]
        notes.append("Corroboration and entity checks that need the open web are emitted as verification tasks "
                     "rather than asserted from on-site evidence.")

    notes.append(f"Trust checks evaluated {n} parsed pages ({len(articles)} article pages).")
    return out, notes, tasks


def main():
    ap = argparse.ArgumentParser(description="Freshness/corroboration analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages = load_pack(args.pack)
    findings, notes, tasks = analyze(meta, pages)
    emit(findings, notes, tasks, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
