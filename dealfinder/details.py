"""Read floor size, plot size, levies and rates from Property24 listing pages.

Rules this follows:
- Only sites whose robots.txt allows it (Property24 allows listing pages; Private Property does not,
  so it is never read).
- One visit per listing, ever. Pauses between visits, a capped number per run, and an honest
  User-Agent. If the site answers with "blocked" / "too many requests", it stops for the run.
"""
from __future__ import annotations

import re
import time
from typing import Optional
from urllib import robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = "property-deal-finder/1.0 (personal project; one visit per new listing)"
PAUSE_SECONDS = 2.0

_NUM = r"(\d{1,3}(?:[  ,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"


def _num(s: str) -> Optional[float]:
    s = re.sub(r"[  ]", "", s)
    if s.count(",") == 1 and len(s.split(",")[1]) != 3:  # "1,5" decimal comma
        s = s.replace(",", ".")
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_property24_page(html: str) -> dict:
    """Pull the key facts from a Property24 listing page (first occurrence = the listing itself)."""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    out = {"floor_m2": None, "erf_m2": None, "levies": None, "rates": None, "bedrooms": None, "bathrooms": None}

    m = re.search(rf"Floor Size\s*:?\s*{_NUM}\s*(?:m²|m2|sqm)", text, re.I)
    if m:
        v = _num(m.group(1))
        out["floor_m2"] = v if v and v >= 15 else None
    m = re.search(rf"Erf Size\s*:?\s*{_NUM}\s*(m²|m2|sqm|ha)", text, re.I)
    if m:
        v = _num(m.group(1))
        if v:
            out["erf_m2"] = v * 10_000 if m.group(2).lower() == "ha" else v
    for key, label in (("levies", r"Levies"), ("rates", r"Rates and Taxes")):
        m = re.search(rf"{label}\s*:?\s*R\s*{_NUM}", text, re.I)
        if m:
            out[key] = _num(m.group(1))
    for key, label in (("bedrooms", "Bedrooms"), ("bathrooms", "Bathrooms")):
        m = re.search(rf"{label}\s*:\s*([\d.]+)", text, re.I)
        if m:
            out[key] = _num(m.group(1))
    return out


class ListingPageReader:
    def __init__(self, limit: int, pause: float = PAUSE_SECONDS):
        self.limit, self.pause = limit, pause
        self.visits = 0
        self.blocked: Optional[str] = None
        self._robots: dict[str, robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": UA}, timeout=15)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse(["User-agent: *", "Disallow: /"])  # can't check -> don't visit
            self._robots[host] = rp
        return self._robots[host].can_fetch(UA, url)

    def read(self, url: str) -> Optional[tuple[str, str]]:
        """(final url, html) or None. Never visits pages robots.txt disallows."""
        if self.blocked or self.visits >= self.limit or not self.allowed(url):
            return None
        if self.visits:
            time.sleep(self.pause)
        self.visits += 1
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        except requests.RequestException:
            return None
        if r.status_code in (403, 429, 503):
            self.blocked = f"{urlparse(url).netloc} answered {r.status_code}; stopped for this run"
            return None
        if r.status_code != 200 or not self.allowed(r.url):
            return None
        return r.url, r.text
