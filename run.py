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
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from pathlib import Path

from dealfinder import config
from dealfinder.parsers import P24_RE, PRICE_RE, extract_details, match_listing, parse_message
from dealfinder.scoring import assign_region, mark_duplicates, norm, score_listings
from dealfinder.services import Geocoder, RedirectResolver, Supabase, fetch_recent_emails, send_email

COLUMNS = ["id", "source", "url", "title", "property_type", "price", "bedrooms", "bathrooms",
           "floor_m2", "erf_m2", "suburb", "city", "province", "region", "lat", "lng", "last_seen",
           "suburb_median", "comps_count", "deal_score", "score_basis", "image_url",
           "wide_median", "wide_comps", "wide_score", "wide_basis", "trends_url",
           "address", "pin_level", "duplicate_of"]
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


def add_trend_links(rows: list[dict], limit: int) -> int:
    """Find each Property24 listing's suburb number (by following its link once) to link the
    suburb's area-trends page, which shows average asking and sold prices."""
    resolver = RedirectResolver(limit)
    done = 0
    for r in rows:
        if r.get("trends_url") or r.get("source") != "property24" or not r.get("url"):
            continue
        final = resolver(r["url"])
        m = P24_RE.search(final or "")
        if not m:
            continue
        sub, city, _prov, sid, _lid = m.groups()
        r["trends_url"] = f"https://www.property24.com/{city}/{sub}/property-trends/{sid}"
        done += 1
    return done


def looks_like_listing_email(row: dict) -> bool:
    """True for emails from a listing site, or anything mentioning prices and bedrooms."""
    sender = (row.get("sender") or "").lower()
    subject = (row.get("subject") or "").lower()
    if any(w in subject for w in ("activat", "verify", "welcome", "password", "security")):
        return False
    if any(d in sender for d in config.LISTING_SENDER_DOMAINS):
        return True
    text = (row.get("plain") or "") + " " + (row.get("html") or "")[:20000]
    return bool(PRICE_RE.search(text)) and "bedroom" in text.lower()


def raw_row_to_message(row: dict) -> EmailMessage:
    """Rebuild an email from a raw_emails row so the normal parser can read it."""
    msg = EmailMessage()
    msg["From"] = (row.get("sender") or "").replace("\n", " ")
    msg["Subject"] = (row.get("subject") or "").replace("\n", " ")
    plain, body_html = row.get("plain") or "", row.get("html") or ""
    if plain:
        msg.set_content(plain)
        if body_html:
            msg.add_alternative(body_html, subtype="html")
    elif body_html:
        msg.set_content(body_html, subtype="html")
    return msg


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

    if not config.SUPABASE_SECRET_KEY:
        write_job_summary(["## ❌ Missing secret: SUPABASE_SECRET_KEY",
                           "Add it under Settings → Secrets and variables → Actions → Repository secrets."])
        return 1

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

    notes: list[str] = []
    resolver = RedirectResolver(config.MAX_REDIRECT_LOOKUPS)

    # Emails copied in by the Google Apps Script (the normal route)
    raw = db.select_all("raw_emails", order="received_at", select="id,sender,subject,html,plain",
                        filters={"processed": "is.false"})
    unread: list[str] = []  # emails that look like listings but we couldn't read
    for row in raw:
        found = parse_message(raw_row_to_message(row), resolver)
        parsed += found
        if not found and looks_like_listing_email(row):
            unread.append(f"{(row.get('sender') or '?').split('<')[0].strip()}: {row.get('subject') or ''}"[:120])
    n_emails = len(raw)
    if unread:
        notes.append(f"- ⚠️ {len(unread)} email(s) looked like listings but couldn't be read "
                     "(a site may have changed its layout or be new): " + "; ".join(f"`{u}`" for u in unread[:5]))

    # Optional: read Gmail directly with an app password (only if one is set up)
    imap_ok = False
    if config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD:
        try:
            for msg in fetch_recent_emails(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, config.LOOKBACK_DAYS):
                n_emails += 1
                parsed += parse_message(msg, resolver)
            imap_ok = True
        except imaplib.IMAP4.error as e:
            notes.append(f"- ⚠️ Direct Gmail login skipped (`{e}`). That's fine if the Apps Script is set up; "
                         "delete the GMAIL_APP_PASSWORD secret to hide this message.")
    parsed += read_manual_csv()
    if not raw and not imap_ok:
        notes.append("- No new emails waiting. If alerts have arrived in the bot Gmail, check the Apps Script "
                     "(script.google.com → Executions).")
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

    # Forget listings that haven't appeared in any alert for a long time
    keep_cutoff = now - timedelta(days=config.KEEP_LISTINGS_DAYS)
    stale = [i for i, r in all_rows.items() if r.get("last_seen") and r["last_seen"] < keep_cutoff]
    for i in stale:
        del all_rows[i]
    if stale:
        db.delete_where("listings", {"last_seen": f"lt.{keep_cutoff.isoformat()}"})

    # 3) Map coordinates + region ------------------------------------------------
    geo = Geocoder(db)
    known_towns = {norm(r["suburb"]): (r["city"], r.get("province"))
                   for r in sorted(all_rows.values(), key=lambda r: r.get("source") != "property24", reverse=True)
                   if r.get("suburb") and r.get("city")}  # Property24's answer wins
    for r in all_rows.values():
        geo.resolve_town(r, known_towns)
    for r in all_rows.values():
        if r.get("lat") is None:
            r["lat"], r["lng"] = geo.locate(r)
        r["region"] = assign_region(r)
    geo.save()

    # 4) Score everything and save -----------------------------------------------
    rows = list(all_rows.values())
    n_trends = add_trend_links(rows, config.MAX_TREND_LOOKUPS_PER_RUN)
    n_dupes = mark_duplicates(rows)
    score_listings(rows, now)
    payload = []
    for r in rows:
        row = {c: r.get(c) for c in COLUMNS}
        row["last_seen"] = r["last_seen"].isoformat() if r.get("last_seen") else now.isoformat()
        payload.append(row)
    db.upsert("listings", payload)

    # Mark the copied emails as done, and clear out old ones to keep the database small
    db.mark_processed("raw_emails", [r["id"] for r in raw])
    db.delete_where("raw_emails", {"processed": "is.true",
                                   "created_at": f"lt.{(now - timedelta(days=config.KEEP_EMAILS_DAYS)).isoformat()}"})
    sync_line = "- Gmail sync: no heartbeat yet (update the Apps Script to the latest Code.gs)"
    try:
        status = db.select_all("sync_status", order="id")
        if status and status[0].get("last_run"):
            mins = (now - parse_ts(status[0]["last_run"])).total_seconds() / 60
            sync_line = f"- Gmail sync last ran {mins:.0f} min ago"
            if status[0].get("error"):
                sync_line += f" ⚠️ with an error: `{status[0]['error'][:200]}`"
            elif mins > 45:
                sync_line += " ⚠️ (it should run every 15 min; check script.google.com → Triggers)"
    except Exception:  # noqa: BLE001
        pass
    notes.insert(0, sync_line)
    try:
        size_mb = float(db.rpc("db_size_mb"))
    except Exception:  # noqa: BLE001  (the size report is nice-to-have)
        size_mb = None

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
        (f"- Database size: {size_mb:.0f} MB of the free 500 MB"
         + (" ⚠️ getting full: lower KEEP_LISTINGS_DAYS in dealfinder/config.py" if size_mb >= config.DB_WARN_MB else "")
         if size_mb is not None else "- Database size: unknown"),
        f"- Old listings removed: {len(stale)}",
        f"- Area-trend links added: {n_trends}",
        f"- Homes listed on both sites (shown once): {n_dupes}",
        *notes,
        "",
        "### Top 10 deals right now",
        "| Deal | Price | Beds | Suburb | Region |",
        "|---|---|---|---|---|",
    ] + [
        f"| {d['deal_score']:.0f}% below | {fmt_r(d['price'])} | {d.get('bedrooms') or '-'} | "
        f"[{d.get('suburb')}]({d['url']}) | {d.get('region')} |" for d in top
    ]
    write_job_summary(lines)

    if config.NOTIFY_EMAIL and new_deals and imap_ok:  # sending needs a working app password
        send_email(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD, config.NOTIFY_EMAIL,
                   f"{len(new_deals)} new property deal(s) found", deals_html(new_deals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
