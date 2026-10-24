"""Bank sales (repossessions and bank-assisted sales: Absa, Standard Bank, Nedbank, SA Home Loans) from
Private Property's public bank-sales pages.

These pages are open to everyone (no login) and Private Property's robots.txt allows them. The listing
check reads them at most twice a day: a handful of pages, an honest user agent, pauses between pages,
and it stops for the day if the site pushes back. Individual listing pages are never visited."""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from . import config
from .details import ListingPageReader
from .parsers import _p24_type

BASE = "https://www.privateproperty.co.za"
# Private Property's bank-sales page for each province; the provinces of the areas you watch are read.
PROVINCE_PAGES = {
    "Western Cape": "western-cape/4", "Gauteng": "gauteng/3", "KwaZulu-Natal": "kwazulu-natal/2",
    "Northern Cape": "northern-cape/5", "Free State": "free-state/6", "Eastern Cape": "eastern-cape/7",
    "Limpopo": "limpopo/8", "North West": "north-west/9", "Mpumalanga": "mpumalanga/10",
}
PAGES_PER_PROVINCE = 6  # 20 listings a page; big provinces (Gauteng) are only partly covered


def pages() -> list[tuple[str, str]]:
    """(label, first page) for every province you have an area in."""
    provs = sorted({r.get("province") for r in config.REGIONS.values()} & set(PROVINCE_PAGES))
    return [(p, f"{BASE}/bank-sales/{PROVINCE_PAGES[p]}") for p in provs]


MAX_PAGES = 16         # per run, all provinces together
EVERY_HOURS = 11       # so about twice a day
_NUM = re.compile(r"[\d\s ,.]+")


def _number(text: str) -> Optional[float]:
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def parse_bank_sales_page(html: str) -> tuple[list[dict], Optional[str]]:
    """(listings, next page URL) from one bank-sales results page."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.listing-result"):
        href = a.get("href") or ""
        m = re.search(r"/(T\d{5,9})(?:[/?#]|$)", href)
        if not m:
            continue
        lid = m.group(1)
        banners = {b.get_text(strip=True).lower() for b in a.select(".listing-banner")}
        ld = {}
        script = a.find("script", attrs={"type": "application/ld+json"})
        if script and script.string:
            try:
                ld = json.loads(script.string)
            except ValueError:
                ld = {}
        addr = ld.get("address") or {}
        locality = [p.strip() for p in (addr.get("addressLocality") or "").split(",") if p.strip()]
        suburb_el = a.select_one(".listing-result__desktop-suburb")
        suburb = suburb_el.get_text(strip=True) if suburb_el else (locality[0] if locality else None)
        props = {p.get("name"): p.get("value") for p in ld.get("additionalProperty") or [] if isinstance(p, dict)}
        title = a.get("title") or ""
        price_el = a.select_one(".listing-result__price")
        price = _number(price_el.get_text(" ", strip=True)) if price_el else None

        def feature(name):
            el = a.select_one(f'.listing-result__feature[title="{name}"]')
            if not el:
                return None
            txt = el.get_text(" ", strip=True)
            v = _number(txt)
            return v * 10_000 if v and re.search(r"\bha\b", txt) else v

        geo = ld.get("geo") or {}
        logo = a.select_one("img.listing-result__logo")
        agent = a.select_one(".listing-result__agent-name")
        desc = a.select_one(".listing-result__description")
        photo = (ld.get("photo") or [{}])[0].get("contentUrl") if ld.get("photo") else None
        bank = logo.get("alt").strip() if logo and logo.get("alt") else None
        rec = {
            "id": f"pp:{lid}", "source": "privateproperty", "url": BASE + href.split("?")[0],
            "title": f"{title} in {suburb}" if title and suburb else title or None,
            "property_type": _p24_type(title) if title else None,
            "price": int(price) if price and price >= 50_000 else None,
            "bedrooms": _number(props.get("Bedrooms")) if props.get("Bedrooms") else None,
            "bathrooms": _number(props.get("Bathrooms")) if props.get("Bathrooms") else None,
            "floor_m2": feature("Floor size"), "erf_m2": feature("Land size") or feature("Erf size"),
            "suburb": suburb, "city": re.sub(r"\s*\(.*?\)", "", locality[1]).strip() if len(locality) > 1 else None,
            "province": addr.get("addressRegion"), "address": addr.get("streetAddress"),
            "image_url": photo, "bank": bank,
            "agency": " · ".join(x for x in (bank, agent.get_text(strip=True) if agent else None) if x) or None,
            "description": desc.get_text(" ", strip=True)[:400] if desc else None,
            "status": "sold" if "sold" in banners else "under_offer" if "under offer" in banners else "for_sale",
        }
        if geo.get("latitude") is not None and geo.get("longitude") is not None:
            rec["lat"], rec["lng"], rec["pin_level"] = float(geo["latitude"]), float(geo["longitude"]), "address"
        out.append(rec)
    nxt = soup.find("link", rel="next")
    return out, (nxt.get("href") if nxt and nxt.get("href", "").startswith(BASE + "/bank-sales") else None)


def read_bank_sales(reader: Optional[ListingPageReader] = None) -> tuple[list[dict], list[str]]:
    """All bank-sale listings on the pages above, plus notes for the run summary."""
    reader = reader or ListingPageReader(MAX_PAGES, pause=3)
    found, notes = [], []
    for label, url in pages():
        n, start = 0, reader.visits
        while url and not reader.blocked and reader.visits < reader.limit and reader.visits - start < PAGES_PER_PROVINCE:
            got = reader.read(url)
            if not got:
                break
            recs, url = parse_bank_sales_page(got[1])
            found += recs
            n += len(recs)
        notes.append(f"{label}: {n}")
    if reader.blocked:
        notes.append(f"⚠️ {reader.blocked}")
    return found, notes
