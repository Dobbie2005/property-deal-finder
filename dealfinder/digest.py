"""The weekly summary email: best new deals, price drops and motivated sellers per area, plus your
deal pipeline. Built by the listing check once per week; the Gmail script sends it."""
import html
from datetime import datetime, timedelta, timezone

from . import config
from .signals import drops_since

SAST = timezone(timedelta(hours=2))
MAP_URL = "https://dobbie2005.github.io/property-deal-finder/"
FLAG_TEXT = {"dropped_twice": "price dropped more than once", "big_drop": "10%+ below its first price",
             "long_listed": "listed 60+ days", "auction": "auction", "bank_sale": "bank sale"}
TAG_TEXT = {"deceased": "deceased estate", "urgent": "urgent sale", "fixer": "fixer-upper",
            "auction": "auction", "bank": "bank sale", "divorce": "divorce / emigration"}
STAGES = [("interested", "Interested"), ("viewing", "Viewing"), ("offer", "Offer made"), ("bought", "Bought"),
          ("renovating", "Renovating"), ("for_sale", "On the market"), ("sold", "Sold")]
E = html.escape


def week_key(now: datetime) -> str:
    y, w, _ = now.astimezone(SAST).isocalendar()
    return f"{y}-W{w:02d}"


def _r(v) -> str:
    return "R " + f"{int(round(v)):,}".replace(",", " ") if v else "POA"


def _best(r):
    s = [x for x in (r.get("deal_score"), r.get("wide_score")) if x is not None]
    return max(s) if s else None


def _card(r, extra: str) -> str:
    img = (f'<img src="{E(r["image_url"])}" width="120" style="border-radius:6px;display:block" alt="">'
           if r.get("image_url") else "")
    what = r.get("title") or f"{r.get('property_type') or 'Property'} in {r.get('suburb')}"
    return (f'<tr><td style="padding:8px 10px 8px 0;vertical-align:top;width:120px">{img}</td>'
            f'<td style="padding:8px 0;vertical-align:top;font:14px Arial,sans-serif">'
            f'<b style="font-size:15px">{_r(r.get("price"))}</b> · {E(what)}<br>'
            f'<span style="color:#666">{E(r.get("suburb") or "")}, {E(r.get("city") or "")}</span><br>{extra}<br>'
            f'<a href="{E(r.get("url") or MAP_URL)}">Open listing</a></td></tr>')


def _section(title: str, cards: list[str]) -> str:
    if not cards:
        return ""
    return (f'<h3 style="font:600 15px Arial,sans-serif;margin:16px 0 4px">{E(title)}</h3>'
            f'<table cellspacing="0" cellpadding="0">{"".join(cards)}</table>')


def build_digest(rows: list[dict], notes: list[dict], now: datetime) -> tuple[str, str]:
    week_ago = now - timedelta(days=7)
    live = [r for r in rows if not r.get("duplicate_of") and r.get("region") in config.REGIONS]
    tags = {n["listing_id"]: n.get("tags") or [] for n in notes}

    def seen_recently(r):
        ls = r.get("last_seen")
        return ls and (ls if isinstance(ls, datetime) else datetime.fromisoformat(str(ls).replace("Z", "+00:00"))) >= week_ago

    def is_new(r):
        fs = r.get("first_seen")
        return fs and (fs if isinstance(fs, datetime) else datetime.fromisoformat(str(fs).replace("Z", "+00:00"))) >= week_ago

    body, n_new, n_drops = [], 0, 0
    for key, reg in config.REGIONS.items():
        rs = [r for r in live if r["region"] == key]
        new = [r for r in rs if is_new(r)]
        n_new += len(new)
        deals = sorted((r for r in new if (_best(r) or -99) >= 5), key=lambda r: -_best(r))[:5]
        drops = [(r, d) for r in rs if (d := drops_since(r, week_ago))]
        n_drops += len(drops)
        drops = sorted(drops, key=lambda x: -(x[1][0] - x[1][1]) / x[1][0])[:5]
        motivated = sorted((r for r in rs if seen_recently(r) and (r.get("flags") or tags.get(r["id"]))),
                           key=lambda r: -(len(r.get("flags") or []) + len(tags.get(r["id"]) or [])))[:5]
        parts = [
            _section("Best new deals", [_card(r, f'<b style="color:#1a9850">{_best(r):.0f}% below</b> similar homes') for r in deals]),
            _section("Price drops", [_card(r, f'<b style="color:#1a9850">dropped from {_r(d[0])}</b> '
                                              f'({(d[0] - d[1]) / d[0] * 100:.0f}% less)') for r, d in drops]),
            _section("Possibly motivated sellers", [_card(r, "⚑ " + E(", ".join(
                [FLAG_TEXT.get(f, f) for f in r.get("flags") or []] + [TAG_TEXT.get(t, t) for t in tags.get(r["id"]) or []])))
                for r in motivated]),
        ]
        content = "".join(parts) or '<p style="font:14px Arial,sans-serif;color:#666">Nothing stood out this week.</p>'
        body.append(f'<h2 style="font:700 17px Arial,sans-serif;margin:22px 0 2px;border-bottom:2px solid #1f6f4a;padding-bottom:4px">'
                    f'{E(reg["label"])}</h2><p style="font:13px Arial,sans-serif;color:#666;margin:2px 0">'
                    f'{len(new)} new listing{"" if len(new) == 1 else "s"} this week</p>{content}')

    pipe = [n for n in notes if n.get("stage") and n["stage"] != "passed"]
    if pipe:
        by_id = {r["id"]: r for r in rows}
        lines = []
        for code, label in STAGES:
            for n in (x for x in pipe if x["stage"] == code):
                r = by_id.get(n["listing_id"]) or n.get("snapshot") or {}
                lines.append(f'<li><b>{E(label)}</b>: {E(r.get("title") or r.get("suburb") or n["listing_id"])}'
                             f' ({_r(r.get("price"))})</li>')
        body.append(f'<h2 style="font:700 17px Arial,sans-serif;margin:22px 0 2px">Your deals</h2>'
                    f'<ul style="font:14px Arial,sans-serif">{"".join(lines)}</ul>')

    day = now.astimezone(SAST)
    subject = f"Weekly property summary: {n_new} new listing{'' if n_new == 1 else 's'}, {n_drops} price drop{'' if n_drops == 1 else 's'}"
    html_out = (f'<div style="max-width:640px;font:14px Arial,sans-serif">'
                f'<p>Your weekly summary for the week to {day.strftime("%-d %B %Y")}. '
                f'<a href="{MAP_URL}">Open the map</a> for everything.</p>{"".join(body)}'
                f'<p style="color:#888;font-size:12px;margin-top:20px">Scores compare asking prices with similar listings '
                f'(or the sold prices you saved). Change who gets this email on the map under Settings.</p></div>')
    return subject, html_out


def due(existing_weeks: set[str], now: datetime) -> bool:
    """One summary per week. The daily 05:17 run on Monday creates it; the first one is made straight away."""
    local = now.astimezone(SAST)
    return week_key(now) not in existing_weeks and not (local.weekday() == 0 and local.hour < 5)
