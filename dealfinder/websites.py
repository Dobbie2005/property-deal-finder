"""Listings from estate agencies' own websites: RE/MAX, Pam Golding, Seeff, Century 21, Chas Everitt,
Harcourts. Each area has a list of search-result pages to check (regions.sources, editable on the map).

Rules this follows (tested per site on 24 Oct 2026): robots.txt is checked for every page under the
standard (longest-match) rules; only the first results page is read (newest listings first), with an
honest user agent, a pause between pages, and a stop for the run if a site pushes back. Individual
listing pages are never opened. Property24 and MyProperty block automated visitors, so they're not here."""
import json
import re
from typing import Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import config
from .details import ListingPageReader
from .parsers import _p24_type

EVERY_HOURS = 4          # about five times a day
MAX_PAGES = 40           # per run, all sites and areas together
PAUSE = 3

SITE_NAMES = {"remax": "RE/MAX", "pamgolding": "Pam Golding", "seeff": "Seeff", "century21": "Century 21",
              "chaseveritt": "Chas Everitt", "harcourts": "Harcourts"}


def site_of(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    for key, domain in (("remax", "remax.co.za"), ("pamgolding", "pamgolding.co.za"), ("seeff", "seeff.com"),
                        ("century21", "century21.co.za"), ("chaseveritt", "chaseveritt.co.za"),
                        ("harcourts", "harcourts.co.za")):
        if host.endswith(domain):
            return key
    return None


def _num(text) -> Optional[float]:
    m = re.search(r"\d[\d\s ,]*(?:\.\d+)?", str(text or ""))
    if not m:
        return None
    try:
        return float(re.sub(r"[\s ,]", "", m.group(0)))
    except ValueError:
        return None


def _price(text) -> Optional[int]:
    v = _num(re.sub(r"(?i)^.*?R\s*", "", str(text or ""), count=1)) if "R" in str(text or "") else None
    return int(v) if v and v >= config.MIN_PRICE else None


def _beds_from_title(title: str) -> Optional[float]:
    m = re.match(r"\s*([\d.]+)\s+bed", title or "", re.I)
    return float(m.group(1)) if m else None


def _suburb_from_title(title: str) -> Optional[str]:
    m = re.search(r"\bin\s+(.+?)\s*$", title or "", re.I)
    return m.group(1).strip() if m else None


EXTRA_TYPES = [("freestanding", "house"), ("free standing", "house"), ("sectional title", "apartment"),
               ("residence", "house"), ("home", "house")]


def _type(title: str) -> Optional[str]:
    t = _p24_type(title) if title else None
    if t:
        return t
    low = (title or "").lower()
    return next((pt for words, pt in EXTRA_TYPES if words in low), None)


def _sizes(rec: dict) -> None:
    """These sites show one 'size'. For a home, anything big is the plot, not the floor area."""
    f = rec.get("floor_m2")
    if f and (rec.get("property_type") in ("land", "farm") or f > 400):
        rec["erf_m2"], rec["floor_m2"] = rec.get("erf_m2") or f, None


def _base(rec_id: str, site: str, url: str, title: str) -> dict:
    title = re.sub(r"\s+", " ", title or "").strip()
    return {"id": rec_id, "source": site, "url": url, "title": title or None,
            "property_type": _type(title), "bedrooms": _beds_from_title(title),
            "suburb": _suburb_from_title(title), "price": None, "bathrooms": None, "floor_m2": None,
            "erf_m2": None, "image_url": None, "agency": SITE_NAMES[site], "description": None}


# --- RE/MAX: everything is in one schema.org graph on the page ------------------------------------
def parse_remax(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    graph = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "")
        except ValueError:
            continue
        graph += data.get("@graph", []) if isinstance(data, dict) else []
    by_id = {n.get("@id"): n for n in graph if isinstance(n, dict)}
    ref = lambda v: by_id.get(v.get("@id")) if isinstance(v, dict) else None  # noqa: E731
    out = []
    for n in graph:
        if not isinstance(n, dict) or n.get("@type") != "RealEstateListing":
            continue
        url = n.get("url") or ""
        m = re.search(r"-(\d{6,12})/?$", url)
        if not m:
            continue
        rec = _base(f"remax:{m.group(1)}", "remax", url, n.get("name"))
        offer = ref(n.get("offers")) or {}
        rec["price"] = int(offer["price"]) if str(offer.get("price", "")).replace(".", "").isdigit() and float(offer["price"]) >= config.MIN_PRICE else None
        prop = ref(offer.get("itemOffered")) or {}
        rec["suburb"] = ((prop.get("address") or {}).get("addressLocality")) or rec["suburb"]
        img = ref(n.get("image")) or {}
        rec["image_url"] = img.get("contentUrl") or img.get("url")
        agent = ref(n.get("provider")) or {}
        if agent.get("name"):
            rec["agency"] = agent["name"]
        if not rec["property_type"] and (prop.get("@type") in ("House", "SingleFamilyResidence")):
            rec["property_type"] = "house"
        out.append(rec)
    return out


# --- Seeff -------------------------------------------------------------------------------------------
def _stats(card) -> dict:
    """Beds / baths / sizes from the little icon + number rows on Seeff, Century 21, Harcourts, Chas Everitt."""
    out = {}
    for svg in card.select("svg[class*=icon-solid], svg[class*=icon-outline]"):
        cls = " ".join(svg.get("class", []))
        holder = svg.parent
        txt = holder.get_text(" ", strip=True) if holder else ""
        val = _num(txt)
        if val is None:
            continue
        if "bed" in cls:
            out.setdefault("bedrooms", val)
        elif "bath" in cls:
            out.setdefault("bathrooms", val)
        elif "floor" in cls:
            out.setdefault("floor_m2", val)
        elif "erf" in cls or "land" in cls or "plot" in cls:
            out.setdefault("erf_m2", val)
    return out


def parse_seeff(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.seeff-listing-card[data-id]"):
        a = card.select_one('a[href*="/results/"]')
        if not a:
            continue
        url = "https://www.seeff.com" + a["href"] if a["href"].startswith("/") else a["href"]
        heading = card.select_one(".card-heading")
        title = heading.get_text(" ", strip=True) if heading else (card.select_one("img") or {}).get("alt", "")
        rec = _base(f"seeff:{card['data-id']}", "seeff", url, title)
        price = card.select_one(".card-price")
        rec["price"] = _price(price.get_text(" ", strip=True)) if price else None
        img = card.select_one("img[src]")
        rec["image_url"] = img["src"] if img else None
        addr = card.select_one(".card-address")
        if addr and addr.get_text(strip=True) and not rec["suburb"]:
            rec["suburb"] = addr.get_text(" ", strip=True)
        rec.update({k: v for k, v in _stats(card).items() if v})
        _sizes(rec)
        _from_propdata_url(rec, url)
        out.append(rec)
    return out


# --- Century 21, Harcourts, Chas Everitt (same website platform) -------------------------------------
def _from_propdata_url(rec: dict, url: str) -> None:
    m = re.search(r"/results/[a-z-]+/for-sale/([a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9-]+)/(\d+)/", url)
    if m and not rec.get("suburb"):
        rec["suburb"] = m.group(2).replace("-", " ").title()


def parse_property_cards(html: str, page_url: str) -> list[dict]:
    site = site_of(page_url)
    soup = BeautifulSoup(html, "html.parser")
    base = "https://" + urlparse(page_url).netloc
    out = []
    for card in soup.select("a.property-card-sm[href]"):
        href = card["href"]
        m = re.search(r"/(\d{5,})/", href)
        if not m or "/for-sale/" not in href:
            continue
        url = base + href if href.startswith("/") else href
        desc = card.select_one(".card-description")
        title = desc.get_text(" ", strip=True) if desc else ""
        rec = _base(f"{site}:{m.group(1)}", site, url, title)
        price = card.select_one(".card-price")
        rec["price"] = _price(price.get_text(" ", strip=True)) if price else None
        img = card.select_one("img[src]")
        rec["image_url"] = img["src"] if img and img["src"].startswith("http") else None
        rec.update({k: v for k, v in _stats(card).items() if v})
        _sizes(rec)
        tags = card.select_one(".card-tags")
        if tags and tags.get_text(strip=True):
            rec["description"] = tags.get_text(" ", strip=True)[:200]  # e.g. "Distressed Private Sale"
        _from_propdata_url(rec, url)
        out.append(rec)
    return out


# --- Pam Golding -----------------------------------------------------------------------------------
def parse_pamgolding(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for link in soup.select("a.results__item-heading-link[href*='/property-details/']"):
        href = link["href"]
        m = re.search(r"/property-details/[^/]+/([a-z0-9]+)/?$", href, re.I)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        card = link
        for _ in range(8):  # up to the element holding this listing's price and facts
            card = card.parent
            if card is None or card.select_one(".results__item-body-list"):
                break
        if card is None:
            continue
        url = "https://www.pamgolding.co.za" + href if href.startswith("/") else href
        title = link.get_text(" ", strip=True)
        rec = _base(f"pamgolding:{m.group(1).lower()}", "pamgolding", url, title)
        price = card.select_one(".price-display, .results__item-price")
        rec["price"] = _price(price.get_text(" ", strip=True)) if price else None
        for li in card.select(".results__item-body-item"):
            t = li.get_text(" ", strip=True).lower()
            v = _num(t)
            if v is None:
                continue
            if "bed" in t:
                rec["bedrooms"] = v
            elif "bath" in t:
                rec["bathrooms"] = v
            elif t.startswith("erf") or "land" in t:
                rec["erf_m2"] = v * 10_000 if re.search(r"\bha\b", t) else v
            elif t.startswith("floor") or "m²" in t and rec.get("floor_m2") is None and not t.startswith("erf"):
                rec["floor_m2"] = v
        desc = card.select_one(".results__item-body-description")
        rec["description"] = desc.get_text(" ", strip=True)[:400] if desc else None
        container = card.parent if card.parent is not None else card
        img = container.select_one("img[src^='http']")
        rec["image_url"] = img["src"] if img else None
        out.append(rec)
    return out


PARSERS: dict[str, Callable[[str, str], list[dict]]] = {
    "remax": parse_remax, "pamgolding": parse_pamgolding, "seeff": parse_seeff,
    "century21": parse_property_cards, "harcourts": parse_property_cards, "chaseveritt": parse_property_cards,
}


def default_sources() -> dict[str, list[dict]]:
    """Search pages for the starting areas (found from each site's own sitemap)."""
    seeff = "https://www.seeff.com/results/residential/for-sale/{}/"
    c21 = "https://www.century21.co.za/results/residential/for-sale/{}/"
    chas = "https://www.chaseveritt.co.za/results/residential/for-sale/{}/"
    harc = "https://www.harcourts.co.za/results/residential/for-sale/{}/"
    remax = "https://www.remax.co.za/property-for-sale-south-africa/{}"
    pam = "https://www.pamgolding.co.za/property-search/properties-for-sale-{}"
    S = lambda url, town: {"url": url, "town": town}  # noqa: E731
    return {
        "durbanville": [S(remax.format("western-cape/durbanville"), "Durbanville"), S(remax.format("western-cape/bellville"), "Bellville"),
                        S(remax.format("western-cape/brackenfell"), "Brackenfell"),
                        S(pam.format("durbanville/2216"), "Durbanville"), S(pam.format("bellville-cape-town/2211"), "Bellville"),
                        S(pam.format("brackenfell/2213"), "Brackenfell"),
                        S(seeff.format("durbanville"), "Durbanville"), S(seeff.format("bellville"), "Bellville"), S(seeff.format("brackenfell"), "Brackenfell"),
                        S(c21.format("durbanville"), "Durbanville"), S(c21.format("bellville"), "Bellville"),
                        S(chas.format("durbanville"), "Durbanville"), S(chas.format("bellville"), "Bellville"), S(chas.format("brackenfell"), "Brackenfell"),
                        S(harc.format("durbanville"), "Durbanville"), S(harc.format("bellville"), "Bellville"), S(harc.format("brackenfell"), "Brackenfell")],
        "polokwane": [S(remax.format("limpopo/polokwane"), "Polokwane"), S(pam.format("polokwane/1898"), "Polokwane"),
                      S(seeff.format("polokwane"), "Polokwane"), S(c21.format("polokwane"), "Polokwane"), S(chas.format("polokwane"), "Polokwane")],
        "tzaneen": [S(remax.format("limpopo/tzaneen"), "Tzaneen"), S(pam.format("tzaneen-limpopo-province/1907"), "Tzaneen"),
                    S(seeff.format("tzaneen"), "Tzaneen"), S(c21.format("tzaneen"), "Tzaneen")],
        "mookgophong": [S(remax.format("limpopo/mookgopong"), "Mookgophong"), S(pam.format("mookgopong-naboomspruit/1893"), "Mookgophong"),
                        S(seeff.format("mookgopong"), "Mookgophong"), S(c21.format("mookgopong"), "Mookgophong"), S(chas.format("mookgopong"), "Mookgophong")],
    }


def read_websites(sources: list[dict], reader: Optional[ListingPageReader] = None) -> tuple[list[dict], dict, list[str]]:
    """sources: [{"url", "town", "region"}]. Returns (listings, {site: count}, notes)."""
    reader = reader or ListingPageReader(MAX_PAGES, pause=PAUSE)
    found, counts, notes, blocked_hosts = [], {}, [], set()
    for src in sources:
        site = site_of(src.get("url", ""))
        host = urlparse(src.get("url", "")).netloc
        if not site or host in blocked_hosts:
            continue
        if reader.visits >= reader.limit:
            notes.append("page limit reached; the rest are checked next time")
            break
        got = reader.read(src["url"])
        if reader.blocked:  # this site pushed back: leave it alone for this run, carry on with the others
            notes.append(reader.blocked)
            blocked_hosts.add(host)
            reader.blocked = None
            continue
        if not got:
            notes.append(f"{SITE_NAMES[site]}: no page at {src['url']}")
            continue
        final_url, html = got
        try:
            recs = PARSERS[site](html, src["url"])
        except Exception as e:  # noqa: BLE001  (a layout change on one site never stops the run)
            notes.append(f"{SITE_NAMES[site]}: couldn't read {src['url']} ({str(e)[:80]})")
            continue
        if not recs and final_url.rstrip("/") != src["url"].rstrip("/"):
            notes.append(f"{SITE_NAMES[site]}: {src['url']} now goes to another page; check the address")
        town = config.canonical_town(src.get("town")) or src.get("town")
        for r in recs:
            r["city"] = town
            r["province"] = config.TOWNS.get(town, (None, None, None))[2] if town else None
        found += recs
        counts[SITE_NAMES[site]] = counts.get(SITE_NAMES[site], 0) + len(recs)
    return found, counts, notes
