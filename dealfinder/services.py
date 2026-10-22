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
        if self.calls >= config.MAX_GEOCODES_PER_RUN:
            return None, None
        self.calls += 1
        time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
        params = {"q": query, "format": "json", "limit": 1, "countrycodes": "za"}
        if config.NOMINATIM_EMAIL:
            params["email"] = config.NOMINATIM_EMAIL
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search", params=params,
                             headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            hits = r.json()
            if hits:
                return float(hits[0]["lat"]), float(hits[0]["lon"])
        except (requests.RequestException, ValueError, KeyError):
            pass
        return None, None

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

    def locate(self, rec: dict) -> tuple[Optional[float], Optional[float]]:
        lat, lng = self._get(rec.get("suburb"), rec.get("city"), rec.get("province"))
        if lat is None:  # fall back to the town centre
            lat, lng = self._get(None, rec.get("city"), rec.get("province"))
        return lat, lng

    def save(self) -> None:
        if self.new_rows:
            self.db.upsert("suburbs", self.new_rows)
