#!/usr/bin/env python3
"""
check_structured_data.py - stage 3 of the audit: can a machine parse the entity and its facts?

Offline analyzer over a site pack. Checks schema.org markup, the head-level metadata that
identifies a page, and the heading structure that decides how a page is chunked.

  python3 check_structured_data.py --pack ./pack [--out findings.json]

Expected types are gated by the site type inferred during collection, so a documentation
site is never told it is missing Product markup.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

SKILL = "structured-data-audit"
DIMENSION = "discoverability.semantics"

# What each page class should declare, and why. Only classes present in the sample are checked.
EXPECTED = {
    "home": ({"organization", "localbusiness", "website", "webpage", "professionalservice", "store"},
             "Organization (or LocalBusiness) plus WebSite"),
    # SoftwareApplication is the schema.org type for a SaaS product, not Product (which is
    # for physical/e-commerce goods). Both are accepted so a SaaS product page is not told
    # to add e-commerce markup it does not need.
    "product": ({"product", "productgroup", "offer", "itemlist", "softwareapplication", "service"},
                "Product (or SoftwareApplication for software) with an Offer"),
    "article": ({"article", "blogposting", "newsarticle", "techarticle", "webpage"}, "Article/BlogPosting"),
    "docs": ({"faqpage", "howto", "techarticle", "article", "webpage", "qapage"}, "FAQPage/HowTo/TechArticle"),
    "contact": ({"localbusiness", "organization", "contactpage", "store", "restaurant"},
                "LocalBusiness or ContactPage"),
    "about": ({"organization", "aboutpage", "localbusiness", "webpage"}, "Organization or AboutPage"),
    "pricing": ({"offer", "product", "service", "softwareapplication", "aggregateoffer", "pricespecification"},
                "Offer/Service/SoftwareApplication with price"),
}
ORG_TYPES = {"organization", "localbusiness", "corporation", "store", "restaurant", "professionalservice",
             "ngo", "educationalorganization", "onlinebusiness", "onlinestore"}


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


def types_of(page):
    return {t.lower() for t in page.get("jsonld_types", [])} | {
        t.rsplit("/", 1)[-1].lower() for t in page.get("microdata_types", [])}


def objects_of(page, wanted):
    for obj in page.get("jsonld_objects", []):
        t = obj.get("@type")
        names = {t.lower()} if isinstance(t, str) else {str(x).lower() for x in (t or [])}
        if names & wanted:
            yield obj


def has_prop(obj, *names):
    return any(obj.get(n) not in (None, "", [], {}) for n in names)


def analyze(meta, pages):
    out, notes = [], []
    ok = [p for p in pages if p.get("status") == 200 and p.get("parse_ok")]
    n = len(ok)
    if not ok:
        notes.append("No successfully parsed pages in the pack; structured-data checks skipped.")
        return out, notes

    with_sd = [p for p in ok if p.get("jsonld_count", 0) > 0 or p.get("microdata_types")]

    # -- SD-001: no structured data at all ------------------------------------
    if not with_sd:
        out.append(finding(
            "SD-001", "No structured data anywhere in the sample", "high", "high", [p["url"] for p in ok],
            f"0/{n} sampled pages contain JSON-LD or microdata. Without it, every fact has to be guessed from "
            "prose, and the brand has no machine-readable identity to attach claims to.",
            "Add schema.org JSON-LD, starting with Organization + WebSite on the homepage and the type that "
            "matches each page class.",
            "Facts become explicitly typed rather than inferred, which raises both extraction accuracy and the "
            "chance of being used as a source.",
            steps=["Add an Organization block on the homepage with name, url, logo, description and sameAs "
                   "links to the brand's authoritative profiles.",
                   "Add the page-level type for each template (Product+Offer, Article, FAQPage, LocalBusiness).",
                   "Give each entity a stable @id URL and reference it from other pages so the graph connects.",
                   "Validate with the Rich Results Test and schema.org validator before shipping."],
            scales=True, tags=("schema", "jsonld")))
    else:
        missing = [p["url"] for p in ok if p not in with_sd
                   and p.get("page_class") in EXPECTED and p.get("page_class") != "other"]
        if missing and len(missing) >= 2:
            out.append(finding(
                "SD-002", "Structured data is applied inconsistently across templates", "medium", "high",
                missing,
                f"{len(with_sd)}/{n} sampled pages carry structured data, but {len(missing)} content page(s) of "
                "types that should have it carry none.",
                "Move structured data into the shared page templates so coverage is uniform.",
                "Every page of a class is machine-readable, not just the ones that were done by hand.",
                steps=["Identify which template renders the pages listed in the evidence.",
                       "Emit the type-appropriate JSON-LD from that template using real page data.",
                       "Spot-check a page per template after deploy."],
                scales=True, tags=("schema", "coverage")))

    # -- SD-003: invalid JSON-LD ----------------------------------------------
    broken = [p for p in ok if p.get("jsonld_errors")]
    if broken:
        sample = broken[0]["jsonld_errors"][0][:120]
        out.append(finding(
            "SD-003", "JSON-LD blocks fail to parse", "high", "high", [p["url"] for p in broken],
            f"{len(broken)}/{n} sampled pages contain a JSON-LD script that is not valid JSON "
            f"(first error: {sample}). A block that does not parse is discarded entirely - the markup is "
            "there but contributes nothing.",
            "Fix the malformed JSON-LD and generate it by serialising a data structure rather than string-concatenating.",
            "The markup starts counting instead of being silently dropped.",
            steps=["Reproduce the parse error with a JSON validator on the affected page's script block.",
                   "Common causes: unescaped quotes in a description, a trailing comma, or template variables "
                   "left unquoted.",
                   "Generate JSON-LD with the platform's JSON serializer so escaping is automatic.",
                   "Add a build-time or CI check that parses every JSON-LD block."],
            tags=("schema", "validity")))

    # -- SD-004: expected types by page class ---------------------------------
    by_class = {}
    for p in ok:
        by_class.setdefault(p.get("page_class"), []).append(p)
    gaps = []
    for cls, expected in EXPECTED.items():
        group = by_class.get(cls, [])
        if not group:
            continue
        wanted, label = expected
        lacking = [p for p in group if not (types_of(p) & wanted)]
        if lacking and len(lacking) == len(group):
            gaps.append((cls, label, lacking))
    for cls, label, lacking in gaps:
        sev = "high" if cls in ("home", "product", "pricing") else "medium"
        out.append(finding(
            f"SD-004.{cls}", f"{cls.capitalize()} pages declare no {label} markup", sev, "high",
            [p["url"] for p in lacking],
            f"{len(lacking)}/{len(lacking)} sampled {cls} page(s) lack {label} in JSON-LD or microdata.",
            f"Add {label} markup to the {cls} template, populated from the same data the page displays.",
            "The page's core facts become typed values an assistant can lift with confidence.",
            steps=[f"Add the {label} type to the {cls} template.",
                   "Populate every property from live page data - never hard-code values that can drift.",
                   "Include the properties that carry the answer (price, availability, dates, address, author).",
                   "Validate the output and confirm the markup matches what the visitor sees."],
            scales=True, tags=("schema", cls)))

    # -- SD-005: incomplete high-value properties -----------------------------
    weak_products, weak_articles, weak_local = [], [], []
    for p in ok:
        for obj in objects_of(p, {"product", "productgroup"}):
            offers = obj.get("offers")
            has_price = False
            if isinstance(offers, dict):
                has_price = has_prop(offers, "price", "lowPrice", "highPrice")
            elif isinstance(offers, list):
                has_price = any(isinstance(o, dict) and has_prop(o, "price", "lowPrice", "highPrice") for o in offers)
            if not has_price:
                weak_products.append(p["url"])
        for obj in objects_of(p, {"article", "blogposting", "newsarticle", "techarticle"}):
            if not has_prop(obj, "datePublished") or not has_prop(obj, "author"):
                weak_articles.append(p["url"])
        for obj in objects_of(p, {"localbusiness", "store", "restaurant", "professionalservice"}):
            if not has_prop(obj, "address") or not has_prop(obj, "telephone"):
                weak_local.append(p["url"])

    if weak_products:
        out.append(finding(
            "SD-005", "Product markup omits price or availability", "high", "high", weak_products,
            f"{len(set(weak_products))} page(s) declare Product without an Offer carrying price/availability. "
            "Price and stock status are the two facts shopping answers are built from.",
            "Complete the Offer node with price, priceCurrency, availability and priceValidUntil.",
            "The product becomes eligible for answers that compare price and availability.",
            steps=["Extend the Product JSON-LD with an offers node: price, priceCurrency, availability "
                   "(schema.org/InStock etc.), url and priceValidUntil.",
                   "Ensure the values are rendered from live inventory data.",
                   "Keep the markup and the visible price identical - a mismatch invalidates both."],
            scales=True, tags=("schema", "product")))
    if weak_articles:
        out.append(finding(
            "SD-006", "Article markup omits publication date or author", "medium", "high", weak_articles,
            f"{len(set(weak_articles))} article page(s) declare Article/BlogPosting without datePublished or "
            "author. Both are used to decide whether a claim is current and attributable.",
            "Add datePublished, dateModified and a structured author to article markup.",
            "Content can be dated and attributed, which is what makes it safe to repeat.",
            steps=["Add datePublished and dateModified in ISO 8601 form, driven by the CMS.",
                   "Add author as a Person or Organization node with a name and a URL to a real profile page.",
                   "Mirror the same dates visibly on the page."],
            scales=True, tags=("schema", "article", "freshness")))
    if weak_local:
        out.append(finding(
            "SD-007", "LocalBusiness markup omits address or telephone", "medium", "high", weak_local,
            f"{len(set(weak_local))} page(s) declare a local business type without a full address or telephone.",
            "Complete the PostalAddress and telephone properties, matching the visible contact details exactly.",
            "The business can be resolved to a real place and matched against third-party listings.",
            steps=["Add a nested PostalAddress with street, locality, region, postal code and country.",
                   "Add telephone in international format and openingHoursSpecification.",
                   "Make sure these match the name, address and phone shown on the page and on external listings."],
            tags=("schema", "local")))

    # -- SD-008: entity identity (sameAs / @id) -------------------------------
    org_pages = [p for p in ok if types_of(p) & ORG_TYPES]
    if org_pages:
        no_sameas = [p["url"] for p in org_pages
                     if not any(has_prop(o, "sameAs") for o in objects_of(p, ORG_TYPES))]
        if no_sameas:
            out.append(finding(
                "SD-008", "Organization markup has no sameAs links", "medium", "high", no_sameas,
                f"{len(no_sameas)}/{len(org_pages)} page(s) with Organization markup declare no sameAs. sameAs is "
                "the explicit statement that 'this brand is that profile', and it is how a system links a site to "
                "the independent sources that corroborate it.",
                "Add sameAs links from the Organization node to the brand's authoritative external profiles.",
                "Ties the site to corroborating sources and reduces the chance of being confused with a "
                "similarly-named entity.",
                steps=["List the profiles the brand actually controls or is listed on: Wikidata/Wikipedia entry, "
                       "LinkedIn company page, official social accounts, Crunchbase, app-store listings, "
                       "industry registries, review platforms.",
                       "Add them as a sameAs array on the Organization node.",
                       "Only include profiles that genuinely refer to this entity - wrong links actively "
                       "cause misidentification."],
                tags=("schema", "entity")))
        no_id = [p["url"] for p in org_pages
                 if not any(has_prop(o, "@id") for o in objects_of(p, ORG_TYPES))]
        if no_id and len(no_id) == len(org_pages):
            out.append(finding(
                "SD-009", "Structured data has no stable @id to connect entities", "low", "high", no_id,
                f"No Organization node in the sample declares an @id, so each page's markup describes an "
                "unconnected entity instead of contributing to one graph.",
                "Give each core entity a canonical @id URL and reference it from other pages' markup.",
                "Markup across the site resolves to one entity rather than many lookalikes.",
                steps=["Choose a stable @id per entity (e.g. https://<domain>/#organization).",
                       "Reference that @id from WebSite.publisher, Article.publisher, Product.brand and so on."],
                tags=("schema", "entity")))

    # -- SD-010: markup vs visible content ------------------------------------
    mismatch = []
    for p in ok:
        page_numbers = set(p.get("price_numbers", []))
        if not page_numbers:
            continue
        for obj in objects_of(p, {"offer", "aggregateoffer"}):
            price = obj.get("price") or obj.get("lowPrice")
            if price is None:
                continue
            token = re.sub(r"[^\d.]", "", str(price)).rstrip(".0") or str(price)
            if token and not any(token in num for num in page_numbers):
                mismatch.append((p["url"], str(price)))
    if mismatch:
        out.append(finding(
            "SD-010", "Prices in structured data do not appear in the visible page", "medium", "medium",
            [m[0] for m in mismatch],
            "; ".join(f"{u}: markup price {v} not found in the page's visible prices" for u, v in mismatch[:5]) +
            ". Markup that contradicts the page is a trust problem: systems that detect the mismatch discount "
            "both signals, and any answer built on the stale value is wrong.",
            "Render structured data from the same source as the displayed price so the two cannot drift.",
            "Markup and page agree, so the extracted value is safe to quote.",
            steps=["Trace where the markup price comes from - hard-coded values and cached fragments are the "
                   "usual cause.",
                   "Generate both the visible price and the JSON-LD from one server-side value.",
                   "Add a test that compares them on a sample of pages."],
            verify="Confirm currency and locale formatting before treating a difference as an error.",
            tags=("schema", "consistency")))

    # -- SD-011/012: head metadata --------------------------------------------
    no_title = [p["url"] for p in ok if not p.get("title")]
    no_desc = [p["url"] for p in ok if not p.get("meta_description")]
    titles = Counter(p.get("title", "") for p in ok if p.get("title"))
    dupe_titles = [t for t, c in titles.items() if c > 1 and t]
    if no_title or len(no_desc) >= max(2, 0.5 * n) or dupe_titles:
        parts = []
        if no_title:
            parts.append(f"{len(no_title)}/{n} pages have no <title>")
        if no_desc:
            parts.append(f"{len(no_desc)}/{n} have no meta description")
        if dupe_titles:
            parts.append(f"{len(dupe_titles)} title(s) are reused across pages")
        affected = set(no_title) | set(no_desc) | {p["url"] for p in ok if p.get("title") in dupe_titles}
        out.append(finding(
            "SD-011", "Page-level metadata is missing or duplicated", "medium", "high", affected,
            "; ".join(parts) + ". Title and description are the shortest statement of what a page is about, and "
            "duplicates make pages indistinguishable to a retrieval system.",
            "Give every page a unique, specific title and description generated from its own content.",
            "Each page becomes distinguishable and self-describing in a result set.",
            steps=["Template titles as '<specific page subject> | <brand>' rather than a shared slogan.",
                   "Write descriptions that state the page's actual answer, not marketing copy.",
                   "Fail the build on duplicate titles across templates."],
            scales=True, tags=("metadata",)))

    no_h1 = [p["url"] for p in ok if not p.get("h1")]
    multi_h1 = [p["url"] for p in ok if len(p.get("h1", [])) > 2]
    if no_h1 or multi_h1:
        # Report only the clause that actually fired. Printing both unconditionally produced
        # evidence that led with a zero ("0/20 pages have no H1 and 11 have three or more"),
        # which reads as self-contradictory against the finding's own title.
        clauses = []
        if no_h1:
            clauses.append(f"{len(no_h1)}/{n} page(s) have no H1 at all")
        if multi_h1:
            clauses.append(f"{len(multi_h1)}/{n} page(s) carry three or more H1s, so the page's subject is ambiguous")
        title = ("Pages have no H1 heading" if no_h1 and not multi_h1
                 else "Pages declare multiple competing H1 headings" if multi_h1 and not no_h1
                 else "Heading structure is missing or ambiguous")
        out.append(finding(
            "SD-012", title, "low", "high", set(no_h1) | set(multi_h1),
            "; ".join(clauses) + ". Headings define the "
            "chunk boundaries a retrieval system splits a page on; without them, one undifferentiated blob is "
            "indexed.",
            "Use exactly one H1 that states the page's subject, then a properly nested H2/H3 outline.",
            "Sections become independently retrievable, so the right passage can be quoted.",
            steps=["Set one H1 per page describing the subject in plain words.",
                   "Nest subsections with H2/H3 without skipping levels.",
                   "Make headings descriptive of content, not stylistic labels."],
            scales=True, tags=("structure",)))

    no_lang = [p["url"] for p in ok if not p.get("html_lang")]
    if no_lang and len(no_lang) >= max(2, 0.5 * n):
        out.append(finding(
            "SD-013", "Documents do not declare a language", "low", "high", no_lang,
            f"{len(no_lang)}/{n} pages have no lang attribute on <html>.",
            "Add a lang attribute to every page and hreflang if you serve multiple locales.",
            "Content is matched to users asking in that language instead of being ambiguous.",
            steps=["Set <html lang=\"en\"> (or the correct locale) in the base template.",
                   "For multi-locale sites, add reciprocal hreflang annotations."],
            scales=True, tags=("metadata", "i18n")))

    no_og = [p["url"] for p in ok if not p.get("og")]
    if no_og and len(no_og) >= max(2, 0.5 * n):
        out.append(finding(
            "SD-014", "No Open Graph metadata", "low", "high", no_og,
            f"{len(no_og)}/{n} pages declare no og: tags, so any surface that unfurls a link - including chat "
            "clients and assistant citation cards - has to guess a title and image.",
            "Add og:title, og:description, og:image and og:type to the base template.",
            "Shared and cited links render with a controlled title, summary and image.",
            steps=["Emit og:title, og:description, og:url, og:type and a 1200x630 og:image per page.",
                   "Add twitter:card summary_large_image for parity."],
            scales=True, tags=("metadata", "social")))

    notes.append(f"Structured-data checks evaluated {n} parsed pages; site type inferred as "
                 f"'{meta.get('site_type', 'unknown')}', so type expectations were gated accordingly.")
    return out, notes


def main():
    ap = argparse.ArgumentParser(description="Structured-data analyzer for a collected site pack.")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    meta, pages = load_pack(args.pack)
    findings, notes = analyze(meta, pages)
    emit(findings, notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
