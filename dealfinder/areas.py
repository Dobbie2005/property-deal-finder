"""Suburb outlines ("areas") for the map's Areas button.

Each suburb that has listings gets a row in the `areas` table. The job looks up its outline on
OpenStreetMap once; where OpenStreetMap only has a point for the suburb, the outline can be drawn
on the map instead (those are never overwritten here)."""
import math
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


# --- Official boundaries (free, published by government GIS servers) -----------------------------
# Tried first: they're the real suburb edges. Each source is (label, query URL, name field, regions).
GIS = "https://gis.westerncape.gov.za/server2/rest/services/SpatialDataWarehouse"
OFFICIAL_SOURCES = [
    ("City of Cape Town official suburbs", f"{GIS}/CoCT_Management_Boundaries/MapServer/23/query", "OFC_SBRB_N", {"durbanville"}),
    ("Stats SA census sub-places", f"{GIS}/StatsSA_CensusBoundaries/MapServer/1/query", "SP_NAME", None),
]
OFFICIAL_RECHECK_DAYS = 30
MAX_KM_EXACT = 5    # a same-name suburb further than this from the suburb's pins is a different place
MAX_KM_FUZZY = 3    # 'Sonstraal' -> 'Sonstraal Heights' only when it's right there


def _bbox_centre(geo: dict) -> tuple[float, float]:
    pts = [pt for poly in (geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]])
           for ring in poly for pt in ring]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def _merge(geos: list[dict]) -> dict:
    if len(geos) == 1:
        return geos[0]
    polys = []
    for g in geos:
        polys.extend(g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])
    return {"type": "MultiPolygon", "coordinates": polys}


class OfficialOutlines:
    """Downloads the official suburb shapes around each region once per run, then matches by name."""

    def __init__(self):
        self.cache: dict[str, list] = {}
        self.failed: set[str] = set()
        self.notes: list[str] = []

    def _query(self, url: str, field: str, region: str) -> list[dict]:
        r = config.REGIONS[region]
        dlat = r["radius_km"] / 110.6
        dlng = r["radius_km"] / (111.3 * math.cos(math.radians(r["lat"])))
        params = {"where": "1=1", "outFields": field, "returnGeometry": "true", "f": "geojson",
                  "geometry": f"{r['lng'] - dlng},{r['lat'] - dlat},{r['lng'] + dlng},{r['lat'] + dlat}",
                  "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                  "outSR": 4326, "maxAllowableOffset": 0.00008, "geometryPrecision": 5}
        res = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=90)
        res.raise_for_status()
        data = res.json()
        if "error" in data:
            raise RuntimeError(str(data["error"])[:200])
        out = []
        for f in data.get("features") or []:
            geo, name = f.get("geometry") or {}, (f.get("properties") or {}).get(field)
            if name and geo.get("type") in ("Polygon", "MultiPolygon") and geo.get("coordinates"):
                out.append({"name": str(name).strip(), "geometry": geo, "centre": _bbox_centre(geo)})
        return out

    def shapes(self, region: str) -> list[tuple[str, list[dict]]]:
        if region not in self.cache:
            sources = []
            for label, url, field, regions in OFFICIAL_SOURCES:
                if regions and region not in regions:
                    continue
                try:
                    feats = self._query(url, field, region)
                    sources.append((label, feats))
                    self.notes.append(f"{label} around {region}: {len(feats)} shapes")
                except Exception as e:  # noqa: BLE001  (try again next run)
                    self.failed.add(region)
                    self.notes.append(f"{label} around {region}: failed ({str(e)[:120]})")
            self.cache[region] = sources
        return self.cache[region]


def match_official(a: dict, sources: list[tuple[str, list[dict]]]) -> Optional[tuple[dict, str, str]]:
    """(geometry, official name, source label) for this suburb, or None."""
    target = norm(a["name"])
    if not target:
        return None
    variants = {target}
    for suffix in ("central", "cbd", "ah", "smallholdings"):
        if target.endswith(suffix) and len(target) > len(suffix) + 3:
            variants.add(target[: -len(suffix)])
    centre = (a.get("lat"), a.get("lng"))
    if centre[0] is None and a.get("city") in config.TOWNS:
        centre, max_exact = config.TOWNS[a["city"]][:2], 15  # only the town is known
    else:
        max_exact = MAX_KM_EXACT
    dist = (lambda f: haversine_km(f["centre"][0], f["centre"][1], centre[0], centre[1])) if centre[0] is not None \
        else (lambda f: 0.0)
    for label, feats in sources:
        exact = [f for f in feats if norm(f["name"]) in variants and dist(f) <= max_exact]
        if not exact and centre[0] is not None and a.get("lat") is not None:
            exact = [f for f in feats if len(norm(f["name"])) >= 5 and dist(f) <= MAX_KM_FUZZY and
                     (norm(f["name"]).startswith(target) or target.startswith(norm(f["name"])))]
        if exact:
            best = min(exact, key=dist)
            same = [f for f in exact if f["name"] == best["name"]]  # one suburb split into several pieces
            return _merge([f["geometry"] for f in same]), best["name"], label
    return None


def update_areas(db, rows: list[dict], finder: OutlineFinder, now: datetime,
                 official: Optional[OfficialOutlines] = None) -> dict:
    """Keep the areas table in step with the listings, and fetch missing outlines:
    official boundaries first, then OpenStreetMap. Outlines drawn on the map are never touched."""
    official = official or OfficialOutlines()
    areas = collect_areas(rows)
    existing = {a["key"]: a for a in db.select_all(
        "areas", order="key",
        select="key,name,city,region,lat,lng,boundary_source,boundary_checked_at,official_checked_at,hidden,merged_into")}
    if areas:
        db.upsert("areas", list(areas.values()))  # names, regions and centres only

    def older(ts, days):
        return not ts or datetime.fromisoformat(str(ts).replace("Z", "+00:00")) <= now - timedelta(days=days)

    # Every suburb: those with listings, plus ones added by hand on the map
    candidates = {k: {**v, **areas.get(k, {})} for k, v in existing.items()}
    candidates.update({k: {**existing.get(k, {}), **v} for k, v in areas.items()})

    # 1) Official boundaries
    got, missed = [], []
    for key, a in candidates.items():
        if a.get("hidden") or a.get("merged_into") or a.get("boundary_source") in ("official", "drawn") or a.get("region") not in config.REGIONS:
            continue
        if not older(a.get("official_checked_at"), OFFICIAL_RECHECK_DAYS):
            continue
        sources = official.shapes(a["region"])
        if a["region"] in official.failed:
            continue
        m = match_official(a, sources)
        if m:
            got.append({"key": key, "name": a["name"], "boundary": m[0], "boundary_source": "official",
                        "official_name": f"{m[1]} ({m[2]})", "official_checked_at": now.isoformat(),
                        "boundary_checked_at": now.isoformat()})
        else:
            missed.append({"key": key, "name": a["name"], "official_checked_at": now.isoformat()})
    if got:
        db.upsert("areas", got)
    if missed:
        db.upsert("areas", missed)
    done = {r["key"] for r in got}

    # 2) OpenStreetMap, for the rest
    checked, found = [], 0
    for key, a in sorted(areas.items(), key=lambda kv: kv[0] in existing):  # new suburbs first
        ex = existing.get(key) or {}
        src = ex.get("boundary_source")
        if key in done or ex.get("hidden") or ex.get("merged_into") or src in ("osm", "drawn", "official"):
            continue
        if src == "none" and not older(ex.get("boundary_checked_at"), RECHECK_DAYS):
            continue
        if not finder.can_search():
            break
        geo = pick_outline(finder.search(a["name"], a["city"]), (a["lat"], a["lng"]))
        found += geo is not None
        checked.append({"key": key, "name": a["name"], "boundary": geo,
                        "boundary_source": "osm" if geo else "none", "boundary_checked_at": now.isoformat()})
    if checked:
        db.upsert("areas", checked)
    return {"areas": len(areas), "checked": len(checked), "found": found,
            "official": len(got), "official_missed": len(missed), "notes": official.notes}


# --- Pins inside suburb outlines -------------------------------------------------------------------
def _ring_area_centroid(ring: list) -> tuple[float, float, float]:
    """(signed area, centroid lng, centroid lat) of a ring of [lng, lat] points."""
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-14:
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return a / 2, cx / (3 * a), cy / (3 * a)


def _inside(ring: list, x: float, y: float) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def inside_point(geo: dict) -> Optional[tuple[float, float]]:
    """(lat, lng) of a point inside the outline's biggest piece: its centre if that's inside
    (most shapes), else the middle of the widest stretch across it at that latitude (odd shapes)."""
    try:
        polys = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
        rings = [[(float(p[0]), float(p[1])) for p in poly[0]] for poly in polys if poly and len(poly[0]) >= 3]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not rings:
        return None
    ring = max(rings, key=lambda r: abs(_ring_area_centroid(r)[0]))
    _, cx, cy = _ring_area_centroid(ring)
    if _inside(ring, cx, cy):
        return cy, cx
    xs = sorted(x1 + (cy - y1) * (x2 - x1) / (y2 - y1)
                for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]) if (y1 > cy) != (y2 > cy))
    spans = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
    if not spans:
        return cy, cx
    a, b = max(spans, key=lambda s: s[1] - s[0])
    return cy, (a + b) / 2


def area_pins(area_rows: list[dict]) -> dict[str, tuple[float, float, Optional[str]]]:
    """key -> (lat, lng, region) for every area with an outline that hasn't been deleted."""
    out = {}
    for a in area_rows:
        if a.get("hidden") or a.get("merged_into") or not a.get("boundary"):
            continue
        pt = inside_point(a["boundary"])
        if pt:
            out[a["key"]] = (pt[0], pt[1], a.get("region"))
    return out


def place_pins_in_areas(rows, pins: dict) -> int:
    """Listings without a street address go in the middle of their suburb's outline when it has one.
    The suburb's region wins (Haenertsburg is part of the Tzaneen area even though it's 25 km away)."""
    n = 0
    for r in rows:
        if r.get("pin_level") == "address" or not r.get("suburb") or not r.get("city"):
            continue
        p = pins.get(area_key(r["suburb"], r["city"]))
        if not p:
            continue
        r["lat"], r["lng"], r["pin_level"] = p[0], p[1], "area"
        if p[2] in config.REGIONS:
            r["region"] = p[2]
        n += 1
    return n


# --- Merged suburbs ("Kuils River South" really is part of "Kuils River") ---------------------------
def apply_merges(rows, area_rows: list[dict]) -> int:
    """Listings in a suburb you merged into another are treated as that suburb everywhere (scores, pins,
    sold figures). The ad's own suburb is kept in listed_suburb/listed_city, so undoing a merge restores it."""
    by_key = {a["key"]: a for a in area_rows}
    n = 0
    for r in rows:
        orig_sub = r.get("listed_suburb") or r.get("suburb")
        orig_city = r.get("listed_city") or r.get("city") if r.get("listed_suburb") else r.get("city")
        if not orig_sub:
            continue
        key, seen = area_key(orig_sub, orig_city), set()
        while by_key.get(key, {}).get("merged_into") and key not in seen and len(seen) < 5:
            seen.add(key)
            key = by_key[key]["merged_into"]
        target = by_key.get(key) if seen else None
        new_sub, new_city = (target["name"], target.get("city") or orig_city) if target else (orig_sub, orig_city)
        if (new_sub, new_city) != (r.get("suburb"), r.get("city")):
            if r.get("pin_level") != "address":  # work out the pin again for its new suburb
                r["lat"] = r["lng"] = None
                r["pin_level"] = None
        r["suburb"], r["city"] = new_sub, new_city
        r["listed_suburb"], r["listed_city"] = (orig_sub, orig_city) if target else (None, None)
        n += bool(target)
    return n
