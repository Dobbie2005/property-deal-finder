"""One-off: fetch Private Property's public bank-sales pages (allowed by its robots.txt) so the page layout
can be checked before writing the reader. Stores the HTML in a temporary table."""
import os
import time
import urllib.robotparser

import requests

UA = "property-deal-finder/1.0 (personal project; github.com/Dobbie2005/property-deal-finder)"
BASE = "https://www.privateproperty.co.za"
URLS = [f"{BASE}/bank-sales", f"{BASE}/bank-sales/western-cape/4", f"{BASE}/bank-sales/western-cape/4?page=2"]
rp = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
rp.read()
key = os.environ["SUPABASE_SECRET_KEY"]
h = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
for u in URLS:
    allowed = rp.can_fetch(UA, u)
    if allowed:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
        status, body = r.status_code, r.text[:900000]
    else:
        status, body = 0, "disallowed by robots.txt"
    print(u, allowed, status, len(body))
    requests.post("https://oqctqrhxeuqlhopnmsmt.supabase.co/rest/v1/debug_pages", headers=h,
                  json=[{"url": u, "status": status, "body": body}], timeout=60).raise_for_status()
    time.sleep(3)
