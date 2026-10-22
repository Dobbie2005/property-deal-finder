"""Score each listing against similar listings in the same suburb.

deal_score = how far (in %) the asking price is below the estimated value.
  +20  -> 20% cheaper than similar listings (interesting)
  -10  -> 10% more expensive than similar listings

Estimate, in order of preference:
  1. median price per m² of similar listings in the suburb x this listing's floor size
  2. median price of listings in the suburb with the same number of bedrooms
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from . import config


def type_group(ptype: Optional[str]) -> str:
    if ptype in ("apartment", "land", "farm"):
        return ptype
    return "house"  # houses, townhouses and unknown types are compared together


def _key(r: dict) -> tuple:
    return ((r.get("suburb") or "").lower(), (r.get("city") or "").lower(), type_group(r.get("property_type")))


def score_listings(rows: list[dict], now: datetime) -> None:
    """Adds suburb_median, comps_count, deal_score, score_basis to each row (in place)."""
    cutoff = now - timedelta(days=config.COMPS_WINDOW_DAYS)
    groups = defaultdict(list)
    for r in rows:
        if r.get("price") and r.get("last_seen") and r["last_seen"] >= cutoff:
            groups[_key(r)].append(r)

    for r in rows:
        r.update(suburb_median=None, comps_count=None, deal_score=None, score_basis=None)
        price = r.get("price")
        if not price:
            continue
        comps = [c for c in groups.get(_key(r), []) if c["id"] != r["id"]]

        floor = r.get("floor_m2")
        ppm = [c["price"] / c["floor_m2"] for c in comps if c.get("floor_m2") and c["floor_m2"] >= 20]
        if floor and floor >= 20 and len(ppm) >= config.MIN_COMPS:
            med = median(ppm)
            estimate, basis, n = med * floor, "price_per_m2", len(ppm)
        else:
            beds = r.get("bedrooms")
            same = [c["price"] for c in comps if beds and c.get("bedrooms") == beds]
            if len(same) < config.MIN_COMPS:
                continue
            med = median(same)
            estimate, basis, n = med, "price_same_beds", len(same)

        r["suburb_median"] = round(med, 2)
        r["comps_count"] = n
        r["deal_score"] = round((estimate - price) / estimate * 100, 1)
        r["score_basis"] = basis


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def assign_region(r: dict) -> str:
    if r.get("lat") is not None and r.get("lng") is not None:
        for key, reg in config.REGIONS.items():
            if haversine_km(r["lat"], r["lng"], reg["lat"], reg["lng"]) <= reg["radius_km"]:
                return key
    names = f"{r.get('suburb') or ''} {r.get('city') or ''}".lower()
    for key in config.REGIONS:
        if key in names:
            return key
    return "other"
