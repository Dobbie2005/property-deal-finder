"""Turn listing-alert emails (and any email with listing links) into listing records.

The listing URL itself tells us the source, listing number, suburb, city and
province, so we rely on it first. Price, bedrooms, etc. are read from the text
that sits next to each link in the email.
"""
from __future__ import annotations

import re
from email.message import EmailMessage
from typing import Callable, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup

from . import config

P24_RE = re.compile(
    r"property24\.com/for-sale/([a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9-]+)/(\d+)/(\d+)", re.I
)
PP_RE = re.compile(
    r"privateproperty\.co\.za/for-sale/((?:[a-z0-9-]+/){2,5})([A-Z]{1,3}\d{4,})", re.I
)

PRICE_RE = re.compile(r"\bR\s?(\d{1,3}(?:[   ,]\d{3})+|\d{5,})(?!\d)")
BEDS_RE = [
    re.compile(r"(\d+(?:[.,]5)?)\s*(?:bed(?:room)?s?)\b", re.I),
    re.compile(r"\bbed(?:room)?s?\s*[:\-]?\s*(\d+(?:[.,]5)?)", re.I),
]
BATHS_RE = [
    re.compile(r"(\d+(?:[.,]5)?)\s*(?:bath(?:room)?s?)\b", re.I),
    re.compile(r"\bbath(?:room)?s?\s*[:\-]?\s*(\d+(?:[.,]5)?)", re.I),
]
AREA_RE = re.compile(r"(\d{1,3}(?:[  ,]\d{3})+|\d+)(?:[.,]\d+)?\s*(?:m²|m2|sqm|m\^2)", re.I)

TYPE_KEYWORDS = [
    ("townhouse", r"town\s?house|cluster|duplex|simplex"),
    ("apartment", r"apartment|flat\b|unit\b|penthouse|studio"),
    ("land", r"vacant land|\bplot\b|\bstand\b|\berf\b for sale|vacant"),
    ("farm", r"\bfarm|smallholding|small holding|plot with"),
    ("house", r"\bhouse|\bhome\b|family home"),
]

SKIP_HREF = re.compile(r"unsubscribe|preference|privacy|mailto:|facebook|twitter|instagram|linkedin|youtube|tiktok", re.I)


def slug_to_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.strip().split("-") if w)


def match_listing(url: str) -> Optional[dict]:
    """Return {id, source, url, suburb, city, province} if the URL is a listing."""
    if not url:
        return None
    u = unquote(unquote(url))
    m = P24_RE.search(u)
    if m:
        sub, city, prov, sid, lid = m.groups()
        return {
            "id": f"p24:{lid}",
            "source": "property24",
            "url": f"https://www.property24.com/for-sale/{sub}/{city}/{prov}/{sid}/{lid}".lower(),
            "suburb": slug_to_name(sub),
            "city": slug_to_name(city),
            "province": slug_to_name(prov),
        }
    m = PP_RE.search(u)
    if m:
        parts = [p for p in m.group(1).split("/") if p]
        lid = m.group(2).upper()
        return {
            "id": f"pp:{lid}",
            "source": "privateproperty",
            "url": "https://www.privateproperty.co.za/for-sale/" + "/".join(parts).lower() + "/" + lid,
            "suburb": slug_to_name(parts[-1]),
            "city": slug_to_name(parts[1]) if len(parts) > 1 else None,
            "province": slug_to_name(parts[0]),
        }
    return None


def _num(s: str) -> Optional[float]:
    s = re.sub(r"[   ,]", "", s).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def extract_details(text: str) -> dict:
    """Pull price / beds / baths / sizes / type out of a chunk of text."""
    text = re.sub(r"\s+", " ", text or "")
    out = {"price": None, "bedrooms": None, "bathrooms": None,
           "floor_m2": None, "erf_m2": None, "property_type": None}

    prices = [p for p in (_num(m.group(1)) for m in PRICE_RE.finditer(text)) if p and p >= config.MIN_PRICE]
    if prices:
        # "Reduced from R1 400 000 to R1 250 000" -> the lower one is the current asking price
        out["price"] = int(min(prices))

    for key, patterns in (("bedrooms", BEDS_RE), ("bathrooms", BATHS_RE)):
        for rx in patterns:
            m = rx.search(text)
            if m:
                v = _num(m.group(1).replace(",", "."))
                if v is not None and 0 < v < 30:
                    out[key] = v
                    break

    for m in AREA_RE.finditer(text):
        v = _num(m.group(1))
        if not v or v < 10:
            continue
        before = text[max(0, m.start() - 30):m.start()].lower()
        if re.search(r"erf|land|stand|plot|yard", before):
            out["erf_m2"] = out["erf_m2"] or v
        elif out["floor_m2"] is None:
            out["floor_m2"] = v

    low = text.lower()
    for ptype, rx in TYPE_KEYWORDS:
        if re.search(rx, low):
            out["property_type"] = ptype
            break
    return out


def _merge(a: dict, b: dict) -> dict:
    """Keep non-empty values from a, fill gaps from b."""
    merged = dict(a)
    for k, v in b.items():
        if merged.get(k) in (None, "") and v not in (None, ""):
            merged[k] = v
    return merged


def parse_html(html: str, resolver: Optional[Callable[[str], Optional[str]]] = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)

    # 1) Map each link to a listing (following tracking redirects if a resolver is given)
    a_to_listing = {}
    for a in anchors:
        href = a["href"].strip()
        info = match_listing(href)
        if not info and resolver and href.startswith("http") and not SKIP_HREF.search(href):
            final = resolver(href)
            info = match_listing(final) if final else None
        if info:
            a_to_listing[id(a)] = info

    # 2) For each listing link, find the biggest surrounding block that holds only that listing
    def listing_ids_in(node) -> set:
        return {a_to_listing[id(x)]["id"] for x in node.find_all("a", href=True) if id(x) in a_to_listing}

    found: dict[str, dict] = {}
    for a in anchors:
        info = a_to_listing.get(id(a))
        if not info:
            continue
        block = a
        for parent in a.parents:
            if parent.name in ("[document]", "html"):
                break
            if len(listing_ids_in(parent)) > 1:
                break
            block = parent
        text = block.get_text(" ", strip=True)
        rec = dict(info)
        rec.update(extract_details(text))
        title = a.get_text(" ", strip=True)
        rec["title"] = title if 8 <= len(title) <= 200 else None
        found[rec["id"]] = _merge(found.get(rec["id"], {}), rec)
    return list(found.values())


def parse_text(text: str) -> list[dict]:
    """Plain-text emails: split the text at each listing URL."""
    urls = list(re.finditer(r"https?://\S+", text))
    hits = [(m, match_listing(m.group(0))) for m in urls]
    hits = [(m, info) for m, info in hits if info]
    found: dict[str, dict] = {}
    for i, (m, info) in enumerate(hits):
        # Alerts usually put the details *above* the link; only look below if nothing was found
        start = hits[i - 1][0].end() if i > 0 else max(0, m.start() - 400)
        end = hits[i + 1][0].start() if i + 1 < len(hits) else min(len(text), m.end() + 400)
        rec = dict(info)
        details = extract_details(text[start:m.start()])
        if details["price"] is None:
            details = extract_details(text[m.end():end])
        rec.update(details)
        rec["title"] = None
        found[rec["id"]] = _merge(found.get(rec["id"], {}), rec)
    return list(found.values())


def parse_message(msg: EmailMessage, resolver: Optional[Callable[[str], Optional[str]]] = None) -> list[dict]:
    sender = (msg.get("From") or "").lower()
    use_resolver = resolver if any(d in sender for d in config.LISTING_SENDER_DOMAINS) else None
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        listings = parse_html(html_part.get_content(), use_resolver)
        if listings:
            return listings
    text_part = msg.get_body(preferencelist=("plain",))
    if text_part is not None:
        return parse_text(text_part.get_content())
    return []
