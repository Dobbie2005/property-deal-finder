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
            # The path is province/metro/area/suburb, e.g. western-cape/cape-town/durbanville/kenridge.
            # Only trust a part that is one of our towns; "Cape Town" (the metro) is too broad.
            "city": next((slug_to_name(x) for x in parts[1:-1] if slug_to_name(x) in config.TOWNS), None),
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


# --- Property24 alert emails ("New Properties", "Price Reduced", "Properties on Show") ---------
# Each card in the plain-text version looks like:
#   [image: P24-117748166] <https://www.property24.com/General/RedirectToListing?listingNumber=P24-117748166&...>
#   R 1 999 000
#   1 Bedroom Townhouse
#   Vierlanden
#   Spectrum Realty
#   [image: bedroom] 1     [image: bathroom] 1     [image: garage] 1
# The town isn't printed, but the alert's search (criteriaString) carries Property24's town number.

P24_ALERT_ID_RE = re.compile(r"(?:listingNumber=|\[image:\s*)P24-(\d+)", re.I)
P24_CRITERIA_RE = re.compile(r"criteriaString=([A-Za-z0-9%+/=]+)")
P24_TOWNS = {  # Property24 town id -> (town, province)
    "439": ("Durbanville", "Western Cape"),
    "441": ("Bellville", "Western Cape"),
    "454": ("Brackenfell", "Western Cape"),
    "703": ("Polokwane", "Limpopo"),
    "708": ("Tzaneen", "Limpopo"),
}
P24_TYPES = [  # (words on the card, our property_type)
    ("townhouse", "townhouse"), ("cluster", "townhouse"), ("duplex", "townhouse"), ("simplex", "townhouse"),
    ("apartment", "apartment"), ("flat", "apartment"), ("penthouse", "apartment"), ("studio", "apartment"),
    ("vacant land", "land"), ("land", "land"), ("farm", "farm"), ("smallholding", "farm"), ("plot", "farm"),
    ("commercial", "commercial"), ("industrial", "commercial"), ("retail", "commercial"), ("office", "commercial"),
    ("house", "house"),
]
P24_SECTION_HEADINGS = {"new properties", "price reduced", "properties on show"}


def _p24_town(text: str) -> tuple[Optional[str], Optional[str]]:
    import base64
    from urllib.parse import unquote as uq
    m = P24_CRITERIA_RE.search(text)
    if not m:
        return None, None
    try:
        decoded = base64.b64decode(uq(m.group(1)) + "==").decode("utf-8", "ignore")
        cid = re.search(r"cid=(\d+)", uq(uq(decoded)))
    except Exception:  # noqa: BLE001
        return None, None
    return P24_TOWNS.get(cid.group(1), (None, None)) if cid else (None, None)


def _p24_type(line: str) -> Optional[str]:
    low = line.lower()
    for words, ptype in P24_TYPES:
        if re.search(rf"\b{words}\b", low):
            return ptype
    return None


def p24_photos(html: Optional[str]) -> dict[str, str]:
    """Listing photos in the HTML version: <img src="https://images.prop24.com/..." alt="P24-117748166">."""
    if not html:
        return {}
    photos = {}
    for img in BeautifulSoup(html, "html.parser").find_all("img", alt=re.compile(r"^\s*P24-\d+\s*$")):
        src = (img.get("src") or "").strip()
        if src.startswith("https://"):
            photos[img["alt"].strip()[4:]] = src
    return photos


def parse_property24_alert(text: str, html: Optional[str] = None) -> list[dict]:
    """Parse the plain-text body of a Property24 alert email (photos come from the HTML part)."""
    town, province = _p24_town(text)
    photos = p24_photos(html)
    cards: dict[str, list[str]] = {}
    current = None
    for raw_line in text.splitlines():
        m = P24_ALERT_ID_RE.search(raw_line)
        if m:
            current = m.group(1)
            cards.setdefault(current, [])
        line = re.sub(r"<https?://[^>]*>|https?://\S+", "", raw_line).strip()
        if not line or current is None or P24_ALERT_ID_RE.search(line):
            continue
        if line.lower() in P24_SECTION_HEADINGS or line.lower().startswith(("view more", "view matching")):
            current = None  # end of the cards
            continue
        cards[current].append(line)

    out = []
    for lid, lines in cards.items():
        rec = {"id": f"p24:{lid}", "source": "property24",
               "url": f"https://www.property24.com/General/RedirectToListing?listingNumber=P24-{lid}",
               "city": town, "province": province, "price": None, "bedrooms": None, "bathrooms": None,
               "floor_m2": None, "erf_m2": None, "property_type": None, "suburb": None, "title": None,
               "image_url": photos.get(lid), "agency": None}
        idx_type = None
        for i, line in enumerate(lines):
            if rec["price"] is None and PRICE_RE.search(line):
                p = _num(PRICE_RE.search(line).group(1))
                rec["price"] = int(p) if p and p >= config.MIN_PRICE else None
            elif idx_type is None and rec["price"] is not None and _p24_type(line):
                idx_type = i
                rec["property_type"] = _p24_type(line)
                rec["title"] = line
                beds = re.match(r"([\d.]+)\s+bedroom", line, re.I)
                if beds:
                    rec["bedrooms"] = float(beds.group(1))
        if idx_type is not None and idx_type + 1 < len(lines):
            rec["suburb"] = lines[idx_type + 1]
            nxt = lines[idx_type + 2] if idx_type + 2 < len(lines) else ""
            if nxt and not nxt.startswith("[") and not nxt.lower().startswith("view") and not PRICE_RE.search(nxt):
                rec["agency"] = nxt[:120]  # e.g. "Keller Williams North" (auction houses get flagged)
            for t, p in P24_TOWNS.values():  # e.g. "Polokwane Central" belongs to Polokwane
                if t.lower() in rec["suburb"].lower() and t != rec["city"]:
                    rec["city"], rec["province"] = t, p
        icons = " ".join(lines)
        for key, word in (("bedrooms", "bedroom"), ("bathrooms", "bathroom")):
            m = re.search(rf"\[image:\s*{word}\]\s*([\d.]+)", icons, re.I)
            if m:
                rec[key] = float(m.group(1))
        if rec["title"] and rec["suburb"]:
            rec["title"] = f"{rec['title']} in {rec['suburb']}"
        if rec["suburb"]:
            out.append(rec)
    return out


# --- Private Property alert emails (daily summary, instant alerts, price drops) ------------
# Each card in the plain-text version looks like:
#   <https://www.privateproperty.co.za/ld/T5666495?utm_source=...>
#   R 3 900 000
#   4 bed house in Bendor
#   27 Bendor Drive                      (street address, sometimes)
#   View Property
# Instant alerts put "View Property" before the price. The town isn't in the email; run.py works it
# out from the suburb.

PP_SHORT_RE = re.compile(r"privateproperty\.co\.za/ld/([A-Z]{1,3}\d{4,})", re.I)
PP_LABELS = {"new", "price reduced", "view property", "reduced", "on show"}
PP_DESC_RE = re.compile(r"^(?P<what>.+?)\s+in\s+(?P<suburb>[^,]+?)\s*$", re.I)
ADDRESS_RE = re.compile(r"^\d+[A-Za-z]?\s+\S+|\b(road|street|str|st|drive|dr|avenue|ave|crescent|close|lane|way|"
                        r"boulevard|place|circle|estate)\b", re.I)


def pp_photos(html: Optional[str]) -> dict[str, str]:
    """Listing photos: <a href=".../ld/T5666495..."><img src="https://images.pp.co.za/..."></a>."""
    if not html:
        return {}
    photos = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=PP_SHORT_RE):
        img = a.find("img")
        src = (img.get("src") or "").strip() if img else ""
        if src.startswith("https://"):
            photos.setdefault(PP_SHORT_RE.search(a["href"]).group(1).upper(), src)
    return photos


def parse_privateproperty_alert(text: str, html: Optional[str] = None) -> list[dict]:
    photos = pp_photos(html)
    cards: dict[str, list[str]] = {}
    current = None
    for raw_line in text.splitlines():
        m = PP_SHORT_RE.search(raw_line)
        if m:
            current = m.group(1).upper()
            cards.setdefault(current, [])
            continue
        line = re.sub(r"<https?://[^>]*>|https?://\S+", "", raw_line).strip()
        if not line or current is None or line.lower() in PP_LABELS:
            continue
        cards[current].append(line)

    out = []
    for lid, lines in cards.items():
        rec = {"id": f"pp:{lid}", "source": "privateproperty", "url": f"https://www.privateproperty.co.za/ld/{lid}",
               "price": None, "bedrooms": None, "bathrooms": None, "floor_m2": None, "erf_m2": None,
               "property_type": None, "suburb": None, "city": None, "province": None, "title": None,
               "address": None, "image_url": photos.get(lid)}
        for i, line in enumerate(lines):
            if rec["price"] is None and PRICE_RE.fullmatch(line.replace("\u00a0", " ")):
                p = _num(PRICE_RE.search(line).group(1))
                rec["price"] = int(p) if p and p >= config.MIN_PRICE else None
                continue
            m = PP_DESC_RE.match(line)
            if rec["price"] is not None and rec["suburb"] is None and m and _p24_type(m.group("what")):
                what = m.group("what")
                rec["suburb"] = m.group("suburb").strip()
                rec["property_type"] = _p24_type(what)
                rec["title"] = line
                beds = re.match(r"([\d.]+)\s+bed", what, re.I)
                if beds:
                    rec["bedrooms"] = float(beds.group(1))
                ha = re.search(r"([\d.]+)\s*ha\b", what, re.I)
                if ha:
                    rec["erf_m2"] = float(ha.group(1)) * 10_000
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt and len(nxt) <= 80 and ADDRESS_RE.search(nxt) and not PRICE_RE.search(nxt):
                    rec["address"] = nxt
                break
        if rec["suburb"] and rec["price"]:
            out.append(rec)
    return out


def parse_message(msg: EmailMessage, resolver: Optional[Callable[[str], Optional[str]]] = None) -> list[dict]:
    sender = (msg.get("From") or "").lower()
    text_part = msg.get_body(preferencelist=("plain",))
    if "privateproperty" in sender and text_part is not None:
        text = text_part.get_content()
        if PP_SHORT_RE.search(text):
            html_part = msg.get_body(preferencelist=("html",))
            return parse_privateproperty_alert(text, html_part.get_content() if html_part is not None else None)
    if "property24" in sender and text_part is not None:
        text = text_part.get_content()
        if "RedirectToListing" in text:
            html_part = msg.get_body(preferencelist=("html",))
            return parse_property24_alert(text, html_part.get_content() if html_part is not None else None)
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
