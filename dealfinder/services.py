"""Talking to the outside world: Gmail (IMAP/SMTP), Supabase (REST) and OpenStreetMap."""
from __future__ import annotations

import email
import imaplib
import smtplib
import time
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from typing import Iterator, Optional

import requests

from . import config

UA = "property-deal-finder/1.0 (personal project)"


# --- Gmail ----------------------------------------------------------------------
def fetch_recent_emails(address: str, app_password: str, days: int) -> Iterator[EmailMessage]:
    """Read (without marking as read) every inbox email from the last `days` days."""
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(address, app_password)
    try:
        imap.select("INBOX", readonly=True)  # includes the Promotions/Updates tabs
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = imap.search(None, "SINCE", since)
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            raw = next((part[1] for part in msg_data if isinstance(part, tuple)), None)
            if raw:
                yield email.message_from_bytes(raw, policy=policy.default)
    finally:
        imap.logout()


def send_email(address: str, app_password: str, to: str, subject: str, html: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = address, to, subject
    msg.set_content("Open this email in an HTML-capable mail app to see the deals.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(address, app_password)
        s.send_message(msg)


class RedirectResolver:
    """Follows email tracking links to find the real listing URL (HEAD requests only)."""

    def __init__(self, limit: int):
        self.limit, self.used, self.cache = limit, 0, {}

    def __call__(self, url: str) -> Optional[str]:
        if url in self.cache:
            return self.cache[url]
        if self.used >= self.limit:
            return None
        self.used += 1
        final = None
        try:
            r = requests.head(url, allow_redirects=True, timeout=10, headers={"User-Agent": UA})
            final = r.url
            if final == url or r.status_code >= 400:  # some sites don't answer HEAD: follow with GET, read nothing
                with requests.get(url, allow_redirects=True, timeout=10, stream=True,
                                  headers={"User-Agent": UA}) as g:
                    final = g.url
        except requests.RequestException:
            pass
        self.cache[url] = final
        return final


# --- Supabase -------------------------------------------------------------------
class Supabase:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # legacy JWT keys also go in the Authorization header
            self.headers["Authorization"] = f"Bearer {key}"

    def select_all(self, table: str, order: str, select: str = "*", filters: Optional[dict] = None) -> list[dict]:
        rows, offset, page = [], 0, 1000
        while True:
            r = requests.get(
                f"{self.base}/{table}",
                headers=self.headers,
                params={"select": select, "order": order, "limit": page, "offset": offset, **(filters or {})},
                timeout=60,
            )
            r.raise_for_status()
            batch = r.json()
            rows.extend(batch)
            if len(batch) < page:
                return rows
            offset += page

    def upsert(self, table: str, rows: list[dict], chunk: int = 500) -> None:
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for i in range(0, len(rows), chunk):
            r = requests.post(f"{self.base}/{table}", headers=headers, json=rows[i:i + chunk], timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f"Supabase upsert into {table} failed ({r.status_code}): {r.text[:500]}")

    def mark_processed(self, table: str, ids: list[str], chunk: int = 100) -> None:
        headers = {**self.headers, "Prefer": "return=minimal"}
        for i in range(0, len(ids), chunk):
            id_list = ",".join('"' + x.replace('"', "") + '"' for x in ids[i:i + chunk])
            r = requests.patch(f"{self.base}/{table}", headers=headers, params={"id": f"in.({id_list})"},
                               json={"processed": True}, timeout=60)
            if r.status_code >= 300:
                raise RuntimeError(f"Supabase update of {table} failed ({r.status_code}): {r.text[:500]}")

    def rpc(self, fn: str, args: Optional[dict] = None):
        r = requests.post(f"{self.base}/rpc/{fn}", headers=self.headers, json=args or {}, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase function {fn} failed ({r.status_code}): {r.text[:300]}")
        return r.json()

    def delete_where(self, table: str, filters: dict) -> None:
        r = requests.delete(f"{self.base}/{table}", headers={**self.headers, "Prefer": "return=minimal"},
                            params=filters, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase delete from {table} failed ({r.status_code}): {r.text[:500]}")


# --- Geocoding ------------------------------------------------------------------
class Geocoder:
    """Suburb-level coordinates from OpenStreetMap, cached in the `suburbs` table."""

    def __init__(self, db: Supabase):
        self.db = db
        self.cache = {row["key"]: row for row in db.select_all("suburbs", order="key")}
        self.new_rows: list[dict] = []
        self.calls = 0

    @staticmethod
    def key(suburb, city, province) -> str:
        return "|".join((x or "").strip().lower() for x in (suburb, city, province))

    def _lookup(self, query: str) -> tuple[Optional[float], Optional[float]]:
        hits = self._search(query, 1)
        return hits[0] if hits else (None, None)

    def _search(self, query: str, limit: int = 1) -> list[tuple[float, float]]:
        if self.calls >= config.MAX_GEOCODES_PER_RUN:
            return []
        self.calls += 1
        time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
        params = {"q": query, "format": "json", "limit": limit, "countrycodes": "za"}
        if config.NOMINATIM_EMAIL:
            params["email"] = config.NOMINATIM_EMAIL
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search", params=params,
                             headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            return [(float(h["lat"]), float(h["lon"])) for h in r.json()]
        except (requests.RequestException, ValueError, KeyError):
            return []

    def _get(self, suburb, city, province) -> tuple[Optional[float], Optional[float]]:
        k = self.key(suburb, city, province)
        if k in self.cache:
            return self.cache[k].get("lat"), self.cache[k].get("lng")
        if self.calls >= config.MAX_GEOCODES_PER_RUN:
            return None, None  # try again next run
        query = ", ".join(x for x in (suburb, city, province, "South Africa") if x)
        lat, lng = self._lookup(query)
        row = {"key": k, "suburb": suburb, "city": city, "province": province, "lat": lat, "lng": lng}
        self.cache[k] = row
        self.new_rows.append(row)
        return lat, lng

    MAX_KM_FROM_TOWN = 15  # a suburb further than this from its town is a wrong match (e.g. another "Normandie")

    def resolve_town(self, rec: dict, known: dict) -> None:
        """Fill in city/province for listings whose email didn't say (Private Property).
        known: normalised suburb name -> (city, province), from listings that do say."""
        from .scoring import haversine_km, norm
        sub = rec.get("suburb")
        if rec.get("city") or not sub:
            return
        if norm(sub) in known:
            rec["city"], rec["province"] = known[norm(sub)]
            return
        for town, (_, _, prov) in config.TOWNS.items():  # e.g. "22.7 ha farm in Tzaneen"
            if norm(town) == norm(sub):
                rec["city"], rec["province"] = town, prov
                return
        k = "town?|" + norm(sub)
        if k not in self.cache:
            if self.calls >= config.MAX_GEOCODES_PER_RUN:
                return  # try again next run
            row = {"key": k, "suburb": sub, "city": None, "province": None, "lat": None, "lng": None}
            for lat, lng in self._search(f"{sub}, South Africa", 5):
                town, dist = min(((t, haversine_km(lat, lng, v[0], v[1])) for t, v in config.TOWNS.items()),
                                 key=lambda x: x[1])
                if dist <= self.MAX_KM_FROM_TOWN:
                    row.update(city=town, province=config.TOWNS[town][2], lat=lat, lng=lng)
                    break
            self.cache[k] = row
            self.new_rows.append(row)
        hit = self.cache[k]
        if hit.get("city"):
            rec["city"], rec["province"] = hit["city"], hit["province"]

    MAX_KM_FROM_SUBURB = 3  # a street address further than this from its suburb is a wrong match

    def locate(self, rec: dict) -> tuple[Optional[float], Optional[float]]:
        """Street address if we have one, else suburb centre, else town centre (sets rec['pin_level'])."""
        from .scoring import haversine_km
        town = self._get(None, rec.get("city"), rec.get("province")) if rec.get("city") else (None, None)

        def near_town(pt):
            return pt[0] is not None and (town[0] is None or
                                          haversine_km(pt[0], pt[1], town[0], town[1]) <= self.MAX_KM_FROM_TOWN)

        pt = self._get(rec.get("suburb"), rec.get("city"), rec.get("province"))
        if not near_town(pt) and rec.get("city"):  # the suburb may be filed under a neighbouring town
            pt = self._get(rec.get("suburb"), None, rec.get("province"))
        rec["pin_level"] = "suburb" if near_town(pt) else ("town" if town[0] is not None else None)
        if not near_town(pt):  # fall back to the town centre
            pt = town
        if rec.get("address") and pt[0] is not None:
            addr = self._get(rec["address"], rec.get("suburb"), rec.get("city"))
            if addr[0] is not None and haversine_km(addr[0], addr[1], pt[0], pt[1]) <= self.MAX_KM_FROM_SUBURB:
                rec["pin_level"] = "address"
                return addr
        return pt

    def save(self) -> None:
        if self.new_rows:
            self.db.upsert("suburbs", self.new_rows)
