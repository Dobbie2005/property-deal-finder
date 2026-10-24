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
# A listing belongs to a region if its suburb lies within radius_km of the centre.
REGIONS = {
    "durbanville": {"label": "Durbanville & surrounds", "lat": -33.8320, "lng": 18.6470, "radius_km": 20},
    "polokwane":   {"label": "Polokwane",               "lat": -23.9045, "lng": 29.4689, "radius_km": 25},
    "tzaneen":     {"label": "Tzaneen",                 "lat": -23.8332, "lng": 30.1635, "radius_km": 25},
}

# Towns inside those areas (used to work out the town for listings whose email doesn't say)
TOWNS = {
    "Durbanville": (-33.8403, 18.6494, "Western Cape"),
    "Bellville":   (-33.9064, 18.6271, "Western Cape"),
    "Brackenfell": (-33.8831, 18.7000, "Western Cape"),
    "Polokwane":   (-23.9058, 29.4614, "Limpopo"),
    "Tzaneen":     (-23.8319, 30.1611, "Limpopo"),
}

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

# --- Property24 listing pages (floor size, plot size, levies, rates, area-trend link) ---
# Off: Property24's bot protection answers GitHub's servers with 503, and we respect that.
# Sizes typed into a listing's popup on the map are used instead.
READ_LISTING_PAGES = False
MAX_PAGE_VISITS_PER_RUN = 40
