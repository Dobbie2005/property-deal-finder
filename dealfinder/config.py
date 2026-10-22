"""Settings you might want to change. Secrets come from environment variables
(GitHub repository secrets), never from this file."""
import os

# --- Supabase -----------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://oqctqrhxeuqlhopnmsmt.supabase.co")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

# --- Gmail (the dedicated listings inbox) -------------------------------------
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))  # re-read the last N days each run

# Optional: send a daily summary of new good deals to this address
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")
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

# --- Scoring ------------------------------------------------------------------
COMPS_WINDOW_DAYS = 180  # only compare against listings seen in the last ~6 months
MIN_COMPS = 3            # need at least this many similar listings to give a score
MIN_PRICE = 50_000       # ignore smaller "prices" (levies, rates, rentals)

# --- Geocoding (free OpenStreetMap Nominatim; max 1 request per second) ---------
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "")
MAX_GEOCODES_PER_RUN = 60
