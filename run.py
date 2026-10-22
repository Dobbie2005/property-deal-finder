"""Daily job: read listing alerts -> save to Supabase -> score -> (optionally) email new deals.

Usage
  python run.py                     # normal run (needs the secrets as environment variables)
  python run.py --eml tests/sample_emails   # offline test: parse saved .eml files and print them
"""
from __future__ import annotations

import argparse
import csv
import email
import hashlib
import imaplib
import html
import os
import sys
from datetime import datetime, timezone
from email import policy
from pathlib import Path

from dealfinder import config
from dealfinder.parsers import extract_details, match_listing, parse_message
from dealfinder.scoring import assign_region, score_listings
from dealfinder.services import Geocoder, RedirectResolver, Supabase, fetch_recent_emails, send_email

COLUMNS = ["id", "source", "url", "title", "property_type", "price", "bedrooms", "bathrooms",
           "floor_m2", "erf_m2", "suburb", "city", "province", "region", "lat", "lng", "last_seen",
           "suburb_median", "comps_count", "deal_score", "score_basis"]
MANUAL_CSV = Path(__file__).parent / "data" / "manual_listings.csv"


def _num(v):
    if v is None or str(v).strip() == "":
        return None
    v = str(v).replace("R", "").replace(" ", "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def read_manual_csv(path: Path = MANUAL_CSV) -> list[dict]:
    """Listings you add by hand (Facebook Marketplace, word of mouth, auctions...)."""
    if not path.exists():
        return []
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("url") or "").strip()
            if not url or url.startswith("#"):
                continue
            known = match_listing(url)  # a Property24 / Private Property link pasted in by hand
            rec = known or {
                "id": "manual:" + hashlib.sha1(url.encode()).hexdigest()[:12],
                "source": "facebook" if "facebook.com" in url else "manual",
                "url": url,
            }
            price = _num(row.get("price"))
            rec.update({
                "title": (row.get("title") or "").strip() or None,
                "property_type": (row.get("property_type") or "").strip().lower() or None,
                "price": int(price) if price else None,
                "bedrooms": _num(row.get("bedrooms")),
                "bathrooms": _num(row.get("bathrooms")),
                "floor_m2": _num(row.get("floor_m2")),
                "erf_m2": _num(row.get("erf_m2")),
            })
            for k in ("suburb", "city", "province"):
                if (row.get(k) or "").strip():
                    rec[k] = row[k].strip()
            out.append(rec)
    return out


def merge_into(existing: dict, new: dict) -> dict:
    """New non-empty values win; anything the email didn't mention keeps its old value."""
    merged = dict(existing)
    for k, v in new.items():
        if v not in (None, ""):
            merged[k] = v
    return merged


def parse_ts(v):
    if isinstance(v, datetime) or v is None:
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def fmt_r(v) -> str:
    return "R " + f"{int(v):,}".replace(",", " ") if v else "-"


def deals_html(deals: list[dict]) -> str:
    rows = "".join(
        f"<tr><td><a href='{html.escape(d['url'])}'>{html.escape(d.get('suburb') or '?')}, {html.escape(d.get('city') or '')}</a></td>"
        f"<td>{fmt_r(d['price'])}</td><td>{d.get('bedrooms') or '-'} bed</td>"
        f"<td><b>{d['deal_score']:.0f}% below</b> ({d['comps_count']} comps)</td></tr>"
        for d in deals
    )
    return (f"<p>{len(deals)} new listing(s) priced well below similar listings nearby:</p>"
            f"<table cellpadding='6' style='border-collapse:collapse'>{rows}</table>")


def write_job_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eml", help="folder of .eml files to parse offline (no database)")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    # 1) Collect listings ------------------------------------------------------
    parsed: list[dict] = []
    if args.eml:
        for p in sorted(Path(args.eml).glob("*.eml")):
            msg = email.message_from_bytes(p.read_bytes(), policy=policy.default)
            parsed += parse_message(msg)
        parsed += read_manual_csv()
        for r in parsed:
            print({k: r.get(k) for k in ("id", "suburb", "city", "price", "bedrooms", "bathrooms",
                                         "floor_m2", "erf_m2", "property_type")})
        print(f"{len(parsed)} listings parsed")
        return 0

    missing = [name for name, value in (("GMAIL_ADDRESS", config.GMAIL_ADDRESS),
                                        ("GMAIL_APP_PASSWORD", config.GMAIL_APP_PASSWORD),
                                        ("SUPABASE_SECRET_KEY", config.SUPABASE_SECRET_KEY)) if not value]
    if missing:
        write_job_summary([f"## ❌ Missing secret(s): {', '.join(missing)}",
                           "Add them under Settings → Secrets and variables → Actions → Repository secrets."])
        return 1

    resolver = RedirectResolver(config.MAX_REDIRECT_LOOKUPS)
    n_emails = 0
    try:
        for msg in fetch_recent_emails(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, config.LOOKBACK_DAYS):
            n_emails += 1
            parsed += parse_message(msg, resolver)
    except imaplib.IMAP4.error as e:
        write_job_summary([
            "## ❌ Gmail login failed",
            f"Gmail said: `{e}`",
            f"- Check GMAIL_ADDRESS is exactly the bot address (it starts with `{config.GMAIL_ADDRESS[:3]}`).",
            f"- GMAIL_APP_PASSWORD must be the 16-letter **app password**, not the normal Gmail password "
            f"(the saved one is {len(config.GMAIL_APP_PASSWORD)} characters long without spaces).",
            "- 2-Step Verification must be on for the bot Gmail, and the app password must be created after that.",
        ])
        return 1
    parsed += read_manual_csv()

    # 2) Merge with what's already in the database -----------------------------
    db = Supabase(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY)
    try:
        existing = {r["id"]: r for r in db.select_all("listings", order="id")}
    except Exception as e:  # noqa: BLE001
        write_job_summary([
            "## ❌ Could not reach the Supabase database",
            f"Error: `{str(e)[:300]}`",
            f"- SUPABASE_SECRET_KEY should start with `sb_secret_` (the saved one starts with `{config.SUPABASE_SECRET_KEY[:10]}`).",
            "- If the project was paused, open it in the Supabase dashboard and click Restore.",
        ])
        return 1
    for r in existing.values():
        r["last_seen"] = parse_ts(r.get("last_seen"))
        r["first_seen"] = parse_ts(r.get("first_seen"))

    all_rows = dict(existing)
    new_ids = set()
    for rec in parsed:
        rec["last_seen"] = now
        if rec["id"] in all_rows:
            all_rows[rec["id"]] = merge_into(all_rows[rec["id"]], rec)
        else:
            rec["first_seen"] = now
            all_rows[rec["id"]] = rec
            new_ids.add(rec["id"])

    # 3) Map coordinates + region ------------------------------------------------
    geo = Geocoder(db)
    for r in all_rows.values():
        if r.get("lat") is None:
            r["lat"], r["lng"] = geo.locate(r)
        r["region"] = assign_region(r)
    geo.save()

    # 4) Score everything and save -----------------------------------------------
    rows = list(all_rows.values())
    score_listings(rows, now)
    payload = []
    for r in rows:
        row = {c: r.get(c) for c in COLUMNS}
        row["last_seen"] = r["last_seen"].isoformat() if r.get("last_seen") else now.isoformat()
        payload.append(row)
    db.upsert("listings", payload)

    # 5) Report -------------------------------------------------------------------
    new_deals = sorted(
        (all_rows[i] for i in new_ids
         if all_rows[i].get("deal_score") is not None and all_rows[i]["deal_score"] >= config.NOTIFY_MIN_SCORE),
        key=lambda d: -d["deal_score"],
    )
    top = sorted((r for r in rows if r.get("deal_score") is not None), key=lambda d: -d["deal_score"])[:10]
    lines = [
        "## Daily listing check",
        f"- Emails read: {n_emails}",
        f"- Listings found in emails/CSV: {len(parsed)} ({len(new_ids)} new)",
        f"- Listings in database: {len(rows)} ({sum(r.get('deal_score') is not None for r in rows)} with a score)",
        f"- New suburbs located on the map: {len(geo.new_rows)}",
        "",
        "### Top 10 deals right now",
        "| Deal | Price | Beds | Suburb | Region |",
        "|---|---|---|---|---|",
    ] + [
        f"| {d['deal_score']:.0f}% below | {fmt_r(d['price'])} | {d.get('bedrooms') or '-'} | "
        f"[{d.get('suburb')}]({d['url']}) | {d.get('region')} |" for d in top
    ]
    write_job_summary(lines)

    if config.NOTIFY_EMAIL and new_deals:
        send_email(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, config.NOTIFY_EMAIL,
                   f"{len(new_deals)} new property deal(s) found", deals_html(new_deals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
