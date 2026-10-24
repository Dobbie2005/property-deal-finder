"""Motivated-seller signals: price history, price drops, time on the market, auctions, bank sales.

The alert emails don't include the ad's description (and Property24 blocks reading the ads), so words
like "deceased estate" can't be picked up automatically. These come from what we can see; tags you
tick on the map ("deceased estate", "urgent sale"...) are added on top of them there."""
import re
from datetime import datetime, timedelta
from typing import Optional

LONG_LISTED_DAYS = 60   # on our radar this long and still advertised
BIG_DROP = 0.10         # 10%+ below the first price we saw
AUCTION_RE = re.compile(r"auction|bidx1|on auction", re.I)
KEYWORDS = [
    ("deceased_estate", re.compile(r"deceased|late estate|estate late|executor", re.I)),
    ("urgent_sale", re.compile(r"urgent|distressed|must (?:be )?sell|motivated seller|quick sale|priced to sell|reduced for", re.I)),
    ("fixer_upper", re.compile(r"fixer|tlc|handyman|renovat|needs (?:some )?work|restor", re.I)),
]
BANK_RE = re.compile(r"reposs|bank[\s-]*(sale|owned|assisted|repo)|in possession|\bpip\b", re.I)


def _day(d) -> str:
    return (d if isinstance(d, datetime) else datetime.fromisoformat(str(d).replace("Z", "+00:00"))).date().isoformat()


def update_price_history(r: dict, now: datetime) -> None:
    """Keep [[date, price], ...] for a listing and count how often the price went down."""
    hist = r.get("price_history")
    if not isinstance(hist, list) or not hist:  # older listings: rebuild from what we know
        hist = []
        first = r.get("first_price") or r.get("price")
        if first:
            hist.append([_day(r.get("first_seen") or now), int(first)])
    price = r.get("price")
    if price and (not hist or int(price) != hist[-1][1]):
        hist.append([_day(now), int(price)])
    r["price_history"] = hist[-30:]
    r["price_drops"] = sum(1 for a, b in zip(hist, hist[1:]) if b[1] < a[1])


def seller_flags(r: dict, now: datetime) -> list[str]:
    flags = []
    hist = r.get("price_history") or []
    if r.get("price_drops", 0) >= 2:
        flags.append("dropped_twice")
    if hist and r.get("price") and hist[0][1] and (hist[0][1] - r["price"]) / hist[0][1] >= BIG_DROP:
        flags.append("big_drop")
    first = r.get("first_seen")
    if first and _day(first) <= _day(now - timedelta(days=LONG_LISTED_DAYS)):
        flags.append("long_listed")
    text = " ".join(str(r.get(k) or "") for k in ("agency", "title", "description"))
    if AUCTION_RE.search(text):
        flags.append("auction")
    if r.get("bank") or BANK_RE.search(text):
        flags.append("bank_sale")
    desc = r.get("description") or ""  # only bank-sales listings come with a description
    for flag, rx in KEYWORDS:
        if rx.search(desc):
            flags.append(flag)
    if r.get("status") == "under_offer":
        flags.append("under_offer")
    return flags


def drops_since(r: dict, since: datetime) -> Optional[tuple[int, int]]:
    """(price before, price now) if the price went down on or after `since`."""
    hist = r.get("price_history") or []
    cut = _day(since)
    before = None
    for d, p in hist:
        if d < cut:
            before = p
        elif before is not None and p < before:
            return before, int(r.get("price") or p)
        else:
            before = p
    return None
