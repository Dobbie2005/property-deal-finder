"""Temporary test: which property sites let an honest, polite bot read their public search pages?
Checks robots.txt first and only fetches what it allows. Saves what came back to a temporary table."""
import os, re, sys, time
sys.path.insert(0, ".")
from dealfinder.robots import allowed as robots_allowed
import requests

UA = "property-deal-finder/1.0 (personal project; github.com/Dobbie2005/property-deal-finder)"
SITES = {
    "privateproperty": ["https://www.privateproperty.co.za/for-sale/limpopo/limpopo-bushveld/polokwane-pietersburg/1219"],
    "remax": ["https://www.remax.co.za/property-for-sale-south-africa/limpopo/polokwane?page=2"],
    "chaseveritt": ["https://www.chaseveritt.co.za/results/residential/for-sale/polokwane/"],
    "seeff": ["https://www.seeff.com/results/residential/for-sale/polokwane/"],
    "harcourts": ["https://www.harcourts.co.za/results/residential/for-sale/polokwane/"],
    "century21": ["https://www.century21.co.za/results/residential/for-sale/polokwane/"],
    "pamgolding": ["https://www.pamgolding.co.za/sitemap.xml"],
}
PRICE = re.compile(r"R\s?\d{1,3}(?:[\s ,]\d{3}){1,3}")
BLOCK = re.compile(r"captcha|cf-chl|cloudflare|access denied|are you a robot|attention required|incapsula|perimeterx|datadome", re.I)
key = os.environ["SUPABASE_SECRET_KEY"]
H = {"apikey": key, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}

def save(row):
    requests.post("https://oqctqrhxeuqlhopnmsmt.supabase.co/rest/v1/debug_pages", headers=H, json=[row], timeout=60).raise_for_status()

for site, urls in SITES.items():
    base = re.match(r"https?://[^/]+", urls[0]).group(0)
    try:
        r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=20)
        robots = r.text if r.status_code == 200 else ""
        save({"url": base + "/robots.txt", "site": site, "kind": "robots", "allowed": True, "status": r.status_code,
              "size": len(r.text), "body": robots[:60000]})
    except requests.RequestException as e:
        save({"url": base + "/robots.txt", "site": site, "kind": "robots", "status": 0, "body": str(e)[:500]})
        continue
    for u in urls:
        path = re.sub(r"^https?://[^/]+", "", u) or "/"
        ok = robots_allowed(robots, path)
        row = {"url": u, "site": site, "kind": "page", "allowed": ok}
        if ok:
            time.sleep(3)
            try:
                r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
                body = r.text
                row.update(status=r.status_code, final_url=r.url, size=len(body), prices=len(PRICE.findall(body)),
                           ldjson=body.count("application/ld+json"), blocked_words=",".join(sorted({m.lower() for m in BLOCK.findall(body)}))[:200],
                           body=body[:500000])
            except requests.RequestException as e:
                row.update(status=0, body=str(e)[:500])
        save(row)
        print(site, u, ok, row.get("status"), row.get("size"), row.get("prices"))
        if site == "pamgolding" and row.get("status") == 200:  # find the search pages for our towns in the sitemaps
            kids = re.findall(r"<loc>([^<]+)</loc>", row["body"])[:40]
            hits = []
            for k in kids:
                if not robots_allowed(robots, re.sub(r"^https?://[^/]+", "", k)):
                    continue
                time.sleep(2)
                try:
                    t = requests.get(k, headers={"User-Agent": UA}, timeout=30).text
                except requests.RequestException:
                    continue
                hits += [x for x in re.findall(r"<loc>([^<]+)</loc>", t) if re.search(r"polokwane|tzaneen|durbanville|naboomspruit|mookgop|bellville|brackenfell", x, re.I)]
            save({"url": "pamgolding-sitemap-hits", "site": site, "kind": "hits", "size": len(hits), "body": "\n".join(hits[:2000])})
