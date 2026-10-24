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
import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from . import config


def type_group(ptype: Optional[str]) -> str:
    if ptype in ("apartment", "land", "farm", "commercial"):
        return ptype
    return "house"  # houses, townhouses and unknown types are compared together


def norm(name: Optional[str]) -> str:
    """'De Bron', 'Debron' and 'de-bron' are the same suburb."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _key(r: dict) -> tuple:
    return (norm(r.get("suburb")), norm(r.get("city")), type_group(r.get("property_type")))


def mark_duplicates(rows: list[dict]) -> int:
    """The same home listed on two sites: same suburb, type and bedrooms, price within 2%.
    The Property24 copy (or the older one) stays; the other gets duplicate_of = its id."""
    for r in rows:
        r["duplicate_of"] = None
    groups = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r.get("source") != "property24", str(r.get("first_seen") or ""))):
        if r.get("price") and r.get("suburb"):
            groups[(norm(r["suburb"]), type_group(r.get("property_type")), r.get("bedrooms"))].append(r)
    n = 0
    for members in groups.values():
        kept: list[dict] = []
        twinned: set = set()  # (kept id, other site): a home has at most one twin per site
        for r in members:
            options = [k for k in kept if k.get("source") != r.get("source")
                       and (k["id"], r.get("source")) not in twinned
                       and abs(k["price"] - r["price"]) <= 0.02 * max(k["price"], r["price"])]
            if options:
                twin = min(options, key=lambda k: abs(k["price"] - r["price"]))
                twinned.add((twin["id"], r.get("source")))
                r["duplicate_of"] = twin["id"]
                n += 1
            else:
                kept.append(r)
    return n


def _clean(comps: list[dict]) -> list[dict]:
    """Leave out listings that would skew a comparison: very expensive ones and wild outliers."""
    comps = [c for c in comps if c.get("price") and c["price"] <= config.MAX_COMP_PRICE]
    if len(comps) >= 4:
        mid = median(c["price"] for c in comps)
        comps = [c for c in comps if mid / config.OUTLIER_FACTOR <= c["price"] <= mid * config.OUTLIER_FACTOR]
    return comps


def _estimate(r: dict, comps: list[dict]) -> Optional[tuple]:
    """(median, estimate, basis, n) from comparable listings, or None if there aren't enough.
    Best first: price per m² (when sizes are known), same bedrooms, then ±1 bedroom adjusted."""
    comps = _clean(comps)
    floor = r.get("floor_m2")
    ppm = [c["price"] / c["floor_m2"] for c in comps if c.get("floor_m2") and c["floor_m2"] >= 20]
    if floor and floor >= 20 and len(ppm) >= config.MIN_COMPS:
        med = median(ppm)
        return med, med * floor, "price_per_m2", len(ppm)
    beds = r.get("bedrooms")
    if beds:
        same = [c["price"] for c in comps if c.get("bedrooms") == beds]
        if len(same) >= config.MIN_COMPS:
            med = median(same)
            return med, med, "price_same_beds", len(same)
        # One bedroom more or less, adjusted: a 4-bed at R3m counts as ~R2.6m for a 3-bed
        similar = [c["price"] * (1 + config.BEDROOM_STEP * (beds - c["bedrooms"]))
                   for c in comps if c.get("bedrooms") and abs(c["bedrooms"] - beds) <= 1]
        if len(similar) >= config.MIN_COMPS:
            med = median(similar)
            return med, med, "price_similar_beds", len(similar)
        return None
    if type_group(r.get("property_type")) == "land":  # plots have no bedrooms: compare plot prices
        plots = [c["price"] for c in comps]
        if len(plots) >= config.MIN_COMPS:
            med = median(plots)
            return med, med, "price_same_beds", len(plots)
    return None


def score_listings(rows: list[dict], now: datetime) -> None:
    """Adds two scores to each row (in place):
    - deal_score etc.: compared with similar listings in the same suburb only
    - wide_score etc.: the same, but if the suburb has too few, compared with similar listings
      in suburbs within NEARBY_KM (the map has a switch to use these)
    """
    cutoff = now - timedelta(days=config.COMPS_WINDOW_DAYS)
    recent = [r for r in rows if r.get("price") and r.get("last_seen") and r["last_seen"] >= cutoff
              and not r.get("duplicate_of")]  # a home listed on two sites only counts once
    groups = defaultdict(list)
    for r in recent:
        groups[_key(r)].append(r)

    for r in rows:
        r.update(suburb_median=None, comps_count=None, deal_score=None, score_basis=None,
                 wide_median=None, wide_comps=None, wide_score=None, wide_basis=None)
        price = r.get("price")
        if not price:
            continue

        comps = [c for c in groups.get(_key(r), []) if c["id"] != r["id"]]
        est = _estimate(r, comps)
        if est:
            med, estimate, basis, n = est
            r.update(suburb_median=round(med, 2), comps_count=n,
                     deal_score=round((estimate - price) / estimate * 100, 1), score_basis=basis)
            r.update(wide_median=r["suburb_median"], wide_comps=n, wide_score=r["deal_score"], wide_basis=basis)
            continue

        if r.get("lat") is None:
            continue
        group = type_group(r.get("property_type"))
        nearby = [c for c in recent
                  if c["id"] != r["id"] and type_group(c.get("property_type")) == group and c.get("lat") is not None
                  and haversine_km(r["lat"], r["lng"], c["lat"], c["lng"]) <= config.NEARBY_KM]
        est = _estimate(r, nearby)
        if est:
            med, estimate, basis, n = est
            r.update(wide_median=round(med, 2), wide_comps=n,
                     wide_score=round((estimate - price) / estimate * 100, 1), wide_basis="nearby_" + basis)


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
