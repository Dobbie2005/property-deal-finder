"""Settings you might want to change. Secrets come from environment variables
(GitHub repository secrets), never from this file."""
import os

# --- Supabase -----------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://oqctqrhxeuqlhopnmsmt.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

# --- Gmail (the dedicated listings inbox) -------------------------------------
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))  # re-read the last N days each run

# Deal alerts: new listings / price drops at least this % below similar homes are queued; each
# recipient (set up on the map, under Settings) picks their own minimum.
ALERT_QUEUE_MIN = 5

# Optional: send a daily summary of new good deals to this address
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "").strip()
NOTIFY_MIN_SCORE = float(os.environ.get("NOTIFY_MIN_SCORE", "10"))  # % below suburb median

# Emails from these senders may contain tracking links that we follow (HEAD request only)
# to find the real listing URL.
LISTING_SENDER_DOMAINS = ["property24.com", "privateproperty.co.za"]
MAX_REDIRECT_LOOKUPS = 150

# --- Target areas -------------------------------------------------------------
# The areas you watch live in the Supabase `regions` table (add them on the map: Settings -> Areas you
# watch). These defaults are only used until the table has been read (and by the tests).
# A listing belongs to an area if it lies within radius_km of the centre.
DEFAULT_REGIONS = [
    {"key": "durbanville", "label": "Durbanville & surrounds", "lat": -33.8320, "lng": 18.6470, "radius_km": 20,
     "province": "Western Cape", "towns": [
         {"name": "Durbanville", "lat": -33.8403, "lng": 18.6494, "p24_id": "439"},
         {"name": "Bellville", "lat": -33.9064, "lng": 18.6271, "p24_id": "441"},
         {"name": "Brackenfell", "lat": -33.8831, "lng": 18.7000, "p24_id": "454"}]},
    {"key": "polokwane", "label": "Polokwane", "lat": -23.9045, "lng": 29.4689, "radius_km": 25, "province": "Limpopo",
     "towns": [{"name": "Polokwane", "lat": -23.9058, "lng": 29.4614, "p24_id": "703", "aliases": ["Pietersburg"]}]},
    {"key": "tzaneen", "label": "Tzaneen", "lat": -23.8332, "lng": 30.1635, "radius_km": 25, "province": "Limpopo",
     "towns": [{"name": "Tzaneen", "lat": -23.8319, "lng": 30.1611, "p24_id": "708"}]},
]
REGIONS: dict = {}       # key -> {"label", "lat", "lng", "radius_km", "province"}
TOWNS: dict = {}         # town name -> (lat, lng, province)
TOWN_ALIASES: dict = {}  # letters-only lower-case name or alias -> town name ("naboomspruit" -> "Mookgophong")
P24_TOWN_IDS: dict = {}  # Property24 town id -> (town, province)


def _key(name) -> str:
    return "".join(c for c in (name or "").lower() if c.isalpha())


def set_regions(rows: list) -> None:
    """Replace the areas (in place, so every module sees the change)."""
    rows = [r for r in rows if r.get("active", True)]
    if not rows:
        return
    REGIONS.clear(); TOWNS.clear(); TOWN_ALIASES.clear(); P24_TOWN_IDS.clear()
    for r in sorted(rows, key=lambda r: (r.get("sort") or 100, r["key"])):
        REGIONS[r["key"]] = {"label": r["label"], "lat": float(r["lat"]), "lng": float(r["lng"]),
                             "radius_km": float(r.get("radius_km") or 20), "province": r.get("province")}
        towns = r.get("towns") or [{"name": r["label"], "lat": r["lat"], "lng": r["lng"]}]
        for t in towns:
            name = t["name"]
            TOWNS[name] = (float(t.get("lat", r["lat"])), float(t.get("lng", r["lng"])), r.get("province"))
            for alias in [name] + list(t.get("aliases") or []):
                TOWN_ALIASES[_key(alias)] = name
            if t.get("p24_id"):
                P24_TOWN_IDS[str(t["p24_id"])] = (name, r.get("province"))


def canonical_town(name):
    """'Naboomspruit' -> 'Mookgophong', 'Polokwane (Pietersburg)' -> 'Polokwane'; None if it isn't one of yours."""
    if not name:
        return None
    k = _key(name.split("(")[0]) or _key(name)
    return TOWN_ALIASES.get(k) or TOWN_ALIASES.get(_key(name))


set_regions(DEFAULT_REGIONS)

# --- Scoring ------------------------------------------------------------------
COMPS_WINDOW_DAYS = 180  # only compare against listings seen in the last ~6 months
MIN_COMPS = 3            # need at least this many similar listings to give a score
NEARBY_KM = 3            # the map's "+ nearby suburbs" option looks this far around a suburb
MIN_PRICE = 50_000       # ignore smaller "prices" (levies, rates, rentals)
BEDROOM_STEP = 0.12      # when comparing a 3-bed with a 4-bed, adjust the price by ~12% per bedroom
MAX_COMP_PRICE = 20_000_000  # listings above this are left out of comparisons
OUTLIER_FACTOR = 3       # ...and so are listings over 3x (or under 1/3 of) the typical price

# --- Keeping the free database small (500 MB limit) -----------------------------
KEEP_LISTINGS_DAYS = 365   # listings not seen in an alert for a year are deleted
KEEP_EMAILS_DAYS = 7       # copied emails are deleted a week after they've been read
DB_WARN_MB = 400           # the run summary warns when the database gets this big

# --- Geocoding (free OpenStreetMap Nominatim; max 1 request per second) ---------
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "")
MAX_GEOCODES_PER_RUN = 60
MAX_OUTLINES_PER_RUN = 25   # suburb outlines for the map's Areas button (each suburb is looked up once)

# --- Property24 listing pages (floor size, plot size, levies, rates, area-trend link) ---
# Off: Property24's bot protection answers GitHub's servers with 503, and we respect that.
# Sizes typed into a listing's popup on the map are used instead.
READ_LISTING_PAGES = False
MAX_PAGE_VISITS_PER_RUN = 40
