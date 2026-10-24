"""Suburb outlines ("areas") for the map's Areas button.

Each suburb that has listings gets a row in the `areas` table. The job looks up its outline on
OpenStreetMap once; where OpenStreetMap only has a point for the suburb, the outline can be drawn
on the map instead (those are never overwritten here)."""
import statistics
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from . import config
from .scoring import haversine_km, norm

UA = "property-deal-finder/1.0 (personal project; github.com/Dobbie2005/property-deal-finder)"
OUTLINE_CLASSES = {"place", "boundary", "landuse"}  # not schools, malls, streets named after the suburb
MAX_SPAN_DEG = 0.12   # ~13 km: anything bigger is a whole town or municipality, not a suburb
MIN_SPAN_DEG = 0.002  # ~200 m: anything smaller is a single property or building
MAX_KM_FROM_PIN = 4   # the outline's middle must be near where we put the suburb's pins
RECHECK_DAYS = 30     # suburbs OpenStreetMap had no outline for are tried again after this


def area_key(suburb: Optional[str], city: Optional[str]) -> str:
    """Same rule as the map: 'durbanville/sonstraal'."""
    return f"{norm(city)}/{norm(suburb)}"


def collect_areas(rows: list[dict]) -> dict[str, dict]:
    """One entry per suburb with listings in one of the regions."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("duplicate_of") or not r.get("suburb") or not r.get("city"):
            continue
        if r.get("region") not in config.REGIONS:
            continue
        groups.setdefault(area_key(r["suburb"], r["city"]), []).append(r)
    out = {}
    for key, rs in groups.items():
        pts = [r for r in rs if r.get("lat") is not None and r.get("pin_level") == "suburb"] or \
              [r for r in rs if r.get("lat") is not None and r.get("pin_level") != "town"]
        name = max((r["suburb"] for r in rs), key=lambda s: (sum(x["suburb"] == s for x in rs), len(s)))
        out[key] = {
            "key": key, "name": name, "city": rs[0]["city"], "region": rs[0]["region"],
            "lat": statistics.median(r["lat"] for r in pts) if pts else None,
            "lng": statistics.median(r["lng"] for r in pts) if pts else None,
        }
    return out


def pick_outline(hits: list[dict], centre: tuple) -> Optional[dict]:
    """The first search result that looks like a suburb outline near the suburb's pins."""
    for h in hits:
        geo = h.get("geojson") or {}
        if geo.get("type") not in ("Polygon", "MultiPolygon") or h.get("class") not in OUTLINE_CLASSES:
            continue
        try:
            s, n, w, e = (float(x) for x in h["boundingbox"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (MIN_SPAN_DEG <= n - s <= MAX_SPAN_DEG and MIN_SPAN_DEG <= e - w <= MAX_SPAN_DEG):
            continue
        if centre[0] is not None and haversine_km((s + n) / 2, (w + e) / 2, centre[0], centre[1]) > MAX_KM_FROM_PIN:
            continue
        return geo
    return None


class OutlineFinder:
    """Asks OpenStreetMap's Nominatim for suburb outlines (max 1 request a second)."""

    def __init__(self, limit: int = config.MAX_OUTLINES_PER_RUN):
        self.limit, self.calls = limit, 0

    def can_search(self) -> bool:
        return self.calls < self.limit

    def search(self, name: str, city: str) -> list[dict]:
        self.calls += 1
        time.sleep(1.1)
        params = {"q": f"{name}, {city}, South Africa", "format": "json", "limit": 5, "countrycodes": "za",
                  "polygon_geojson": 1, "polygon_threshold": 0.0002}  # simplified to ~20 m: small and fast
        if config.NOMINATIM_EMAIL:
            params["email"] = config.NOMINATIM_EMAIL
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search", params=params,
                             headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            self.calls = self.limit  # Nominatim is unhappy: stop for this run
            return []


def update_areas(db, rows: list[dict], finder: OutlineFinder, now: datetime) -> dict:
    """Keep the areas table in step with the listings, and fetch missing outlines."""
    areas = collect_areas(rows)
    existing = {a["key"]: a for a in db.select_all("areas", order="key",
                                                    select="key,boundary_source,boundary_checked_at")}
    if areas:
        db.upsert("areas", list(areas.values()))  # names, regions and centres only

    checked, found = [], 0
    for key, a in sorted(areas.items(), key=lambda kv: kv[0] in existing):  # new suburbs first
        ex = existing.get(key) or {}
        src = ex.get("boundary_source")
        if src in ("osm", "drawn"):
            continue
        last = ex.get("boundary_checked_at")
        if src == "none" and last and datetime.fromisoformat(str(last).replace("Z", "+00:00")) > now - timedelta(days=RECHECK_DAYS):
            continue
        if not finder.can_search():
            break
        geo = pick_outline(finder.search(a["name"], a["city"]), (a["lat"], a["lng"]))
        found += geo is not None
        checked.append({"key": key, "name": a["name"], "boundary": geo,
                        "boundary_source": "osm" if geo else "none", "boundary_checked_at": now.isoformat()})
    if checked:
        db.upsert("areas", checked)
    return {"areas": len(areas), "checked": len(checked), "found": found}
