import email
import sys
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dealfinder.parsers import extract_details, match_listing, parse_message  # noqa: E402
from dealfinder.scoring import assign_region, score_listings  # noqa: E402
import run  # noqa: E402

SAMPLES = ROOT / "tests" / "sample_emails"


def load(name):
    return email.message_from_bytes((SAMPLES / name).read_bytes(), policy=policy.default)


def test_match_property24_url():
    info = match_listing("https://www.property24.com/for-sale/sonstraal-heights/durbanville/western-cape/11014/115000001?x=1")
    assert info["id"] == "p24:115000001"
    assert (info["suburb"], info["city"], info["province"]) == ("Sonstraal Heights", "Durbanville", "Western Cape")


def test_match_privateproperty_url():
    info = match_listing("https://www.privateproperty.co.za/for-sale/limpopo/tzaneen/tzaneen/tzaneen-central/T4920123")
    assert info["id"] == "pp:T4920123" and info["suburb"] == "Tzaneen Central" and info["city"] == "Tzaneen"


def test_match_url_hidden_in_tracking_link():
    wrapped = "https://tracker.example.com/r?u=https%3A%2F%2Fwww.property24.com%2Ffor-sale%2Fbendor%2Fpolokwane%2Flimpopo%2F6450%2F999"
    assert match_listing(wrapped)["id"] == "p24:999"


def test_extract_details_reduced_price_and_sizes():
    d = extract_details("Reduced from R 1 950 000 to R 1 795 000 4 Bedrooms 3 Bathrooms 250 m² Erf size 1 100 m² levy R 1 200")
    assert d["price"] == 1_795_000
    assert (d["bedrooms"], d["bathrooms"], d["floor_m2"], d["erf_m2"]) == (4, 3, 250, 1100)


def test_property24_html_email_with_tracking_link():
    target = "https://www.property24.com/for-sale/bellville-central/bellville/western-cape/8790/115000002"
    resolver = lambda url: target if "click.property24.com" in url else None  # noqa: E731
    found = {r["id"]: r for r in parse_message(load("property24_alert.eml"), resolver)}
    assert set(found) == {"p24:115000001", "p24:115000002", "p24:115000003"}
    assert found["p24:115000002"]["price"] == 1_150_000
    assert found["p24:115000002"]["property_type"] == "apartment"
    assert found["p24:115000001"]["floor_m2"] == 180


def test_text_email_details_do_not_bleed_between_listings():
    found = {r["id"]: r for r in parse_message(load("privateproperty_alert.eml"))}
    assert found["pp:T4920123"]["price"] == 1_290_000
    assert found["pp:T4920124"]["property_type"] == "land"


def _row(i, price, floor=None, beds=3, suburb="Durbanville", days_ago=1, now=None):
    return {"id": str(i), "price": price, "floor_m2": floor, "bedrooms": beds, "suburb": suburb,
            "city": "Durbanville", "property_type": "house", "last_seen": now - timedelta(days=days_ago)}


def test_scoring_price_per_m2():
    now = datetime.now(timezone.utc)
    rows = [_row(i, 2_000_000, 100, now=now) for i in range(4)] + [_row("deal", 1_600_000, 100, now=now)]
    score_listings(rows, now)
    deal = rows[-1]
    assert deal["score_basis"] == "price_per_m2" and deal["comps_count"] == 4
    assert deal["deal_score"] == 20.0
    assert rows[0]["deal_score"] == 0.0  # median ignores the one cheap outlier


def test_scoring_falls_back_to_bedrooms_and_ignores_old_listings():
    now = datetime.now(timezone.utc)
    rows = [_row(i, 1_000_000, now=now) for i in range(3)]
    rows += [_row("old", 5_000_000, days_ago=400, now=now), _row("x", 900_000, now=now)]
    score_listings(rows, now)
    assert rows[-1]["score_basis"] == "price_same_beds" and rows[-1]["deal_score"] == 10.0


def test_scoring_needs_enough_comps():
    now = datetime.now(timezone.utc)
    rows = [_row(1, 1_000_000, now=now), _row(2, 900_000, now=now)]
    score_listings(rows, now)
    assert all(r["deal_score"] is None for r in rows)


def test_regions():
    assert assign_region({"lat": -33.86, "lng": 18.70}) == "durbanville"     # Kraaifontein-ish
    assert assign_region({"lat": -23.88, "lng": 29.45}) == "polokwane"
    assert assign_region({"lat": -23.83, "lng": 30.16}) == "tzaneen"
    assert assign_region({"lat": -26.20, "lng": 28.04}) == "other"           # Johannesburg
    assert assign_region({"suburb": "Bendor", "city": "Polokwane"}) == "polokwane"


def test_manual_csv(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text(
        "url,price,property_type,bedrooms,bathrooms,floor_m2,erf_m2,suburb,city,province,title\n"
        "https://www.facebook.com/marketplace/item/123,R 950 000,house,3,1,110,,Kraaifontein,Cape Town,Western Cape,FB house\n",
        encoding="utf-8")
    rows = run.read_manual_csv(p)
    assert rows[0]["source"] == "facebook" and rows[0]["price"] == 950_000 and rows[0]["suburb"] == "Kraaifontein"


def test_merge_keeps_old_values_when_email_omits_them():
    merged = run.merge_into({"price": 1, "bedrooms": 3}, {"price": 2, "bedrooms": None})
    assert merged == {"price": 2, "bedrooms": 3}


def test_raw_email_row_is_parsed_like_a_real_email():
    msg = load("property24_alert.eml")
    html_body = msg.get_body(preferencelist=("html",)).get_content()
    row = {"id": "x", "sender": "Property24 <alerts@property24.com>", "subject": "New",
           "html": html_body, "plain": "View in HTML"}
    found = {r["id"] for r in parse_message(run.raw_row_to_message(row))}
    assert {"p24:115000001", "p24:115000003"} <= found
    html_only = run.raw_row_to_message({**row, "plain": ""})
    assert {r["id"] for r in parse_message(html_only)} == found


def test_real_property24_new_listing_alert():
    (r,) = parse_message(load("p24_real_new.eml"))
    assert r["id"] == "p24:117748166" and r["price"] == 1_999_000
    assert (r["suburb"], r["city"], r["province"]) == ("Vierlanden", "Durbanville", "Western Cape")
    assert (r["bedrooms"], r["bathrooms"], r["property_type"]) == (1, 1, "townhouse")
    assert r["url"] == "https://www.property24.com/General/RedirectToListing?listingNumber=P24-117748166"
    assert r["title"] == "1 Bedroom Townhouse in Vierlanden"


def test_real_property24_price_reduced_alert():
    (r,) = parse_message(load("p24_real_reduced.eml"))
    assert (r["price"], r["suburb"], r["city"], r["property_type"], r["bedrooms"]) == \
        (1_400_000, "Loevenstein", "Bellville", "apartment", 2)


def test_real_property24_on_show_alert_with_several_cards():
    found = {r["id"]: r for r in parse_message(load("p24_real_onshow.eml"))}
    assert len(found) == 5
    assert found["p24:117703912"]["suburb"] == "Brackenfell South" and found["p24:117703912"]["city"] == "Brackenfell"
    assert found["p24:117703912"]["bathrooms"] == 2 and found["p24:117703912"]["price"] == 3_899_000
    house_no_beds = found["p24:117585844"]
    assert house_no_beds["property_type"] == "house" and house_no_beds["bedrooms"] is None and house_no_beds["bathrooms"] == 8
    assert found["p24:117508534"]["property_type"] == "land" and found["p24:117508534"]["price"] == 890_000
    assert found["p24:116749541"]["property_type"] == "commercial"


def test_suburb_named_after_another_town_uses_that_town():
    found = {r["id"]: r for r in parse_message(load("p24_real_onshow.eml"))}
    assert (found["p24:116749541"]["city"], found["p24:116749541"]["province"]) == ("Polokwane", "Limpopo")


def test_geocoder_rejects_a_same_named_suburb_far_from_its_town():
    import dealfinder.services as svc

    class NoDB:
        def select_all(self, *a, **k): return []
    answers = {"Brackenfell, Western Cape, South Africa": (-33.883, 18.70),
               "Normandie, Western Cape, South Africa": (-34.033, 18.788),   # the wrong Normandie
               "Kleinbron, Western Cape, South Africa": (-33.852, 18.687)}   # the right Kleinbron
    geo = svc.Geocoder(NoDB())
    geo._lookup = lambda q: answers.get(q, (None, None))
    assert geo.locate({"suburb": "Normandie", "city": "Brackenfell", "province": "Western Cape"}) == (-33.883, 18.70)
    assert geo.locate({"suburb": "Kleinbron", "city": "Brackenfell", "province": "Western Cape"}) == (-33.852, 18.687)


def test_flags_listing_looking_emails_that_could_not_be_read():
    assert run.looks_like_listing_email({"sender": "Private Property <alerts@privateproperty.co.za>", "subject": "New"})
    assert run.looks_like_listing_email({"sender": "Seeff <x@seeff.com>", "subject": "Just listed",
                                         "plain": "3 Bedroom house R 2 450 000"})
    assert not run.looks_like_listing_email({"sender": "Property24 <no-reply@property24.com>",
                                             "subject": "Property24 Account Activation"})
    assert not run.looks_like_listing_email({"sender": "Google <no-reply@accounts.google.com>",
                                             "subject": "Security alert", "plain": "hi"})


def test_property24_alert_listing_photos():
    found = {r["id"]: r for r in parse_message(load("p24_real_onshow.eml"))}
    assert found["p24:117703912"]["image_url"] == "https://images.prop24.com/1177039120/Crop318x212"
    assert all(r["image_url"] for r in found.values())


def test_nearby_suburbs_fallback_score():
    now = datetime.now(timezone.utc)

    def row(i, suburb, lat, price, beds=4):
        return {"id": str(i), "price": price, "bedrooms": beds, "suburb": suburb, "city": "Polokwane",
                "property_type": "house", "lat": lat, "lng": 29.48, "last_seen": now}
    rows = [row("x", "Bendor", -23.888, 2_000_000), row(1, "Bendor", -23.888, 3_250_000),
            row(2, "Fauna Park", -23.898, 2_500_000), row(3, "Flora Park", -23.900, 2_600_000),
            row(4, "Seshego", -23.849, 600_000)]  # ~4.5 km away: too far to count
    score_listings(rows, now)
    x = rows[0]
    assert x["deal_score"] is None                      # only 1 other 4-bed in Bendor itself
    assert x["wide_basis"] == "nearby_price_same_beds"
    assert x["wide_comps"] == 3 and x["wide_median"] == 2_600_000
    assert x["wide_score"] == round((2_600_000 - 2_000_000) / 2_600_000 * 100, 1)


def test_property24_listing_page_facts_and_trend_link():
    from dealfinder.details import parse_property24_page
    html = """<html><body><h1>1 Bedroom Townhouse for Sale in Vierlanden</h1>
      <ul><li>Bedrooms: 1</li><li>Bathrooms: 1</li><li>Parking: 1</li></ul>
      <div><span>Floor Size</span><span>50 m²</span></div><div><span>Erf Size</span><span>1 250 m²</span></div>
      <div><span>Levies</span><span>R 1 400</span></div><div><span>Rates and Taxes</span><span>R 580</span></div>
      <h2>Similar listings</h2><div>Floor Size 999 m²</div></body></html>"""
    facts = parse_property24_page(html)
    assert facts == {"floor_m2": 50, "erf_m2": 1250, "levies": 1400, "rates": 580, "bedrooms": 1, "bathrooms": 1}
    assert parse_property24_page("<p>Erf Size 1,5 ha</p>")["erf_m2"] == 15_000

    class FakeReader:
        visits, limit, blocked = 0, 10, None
        def read(self, url):
            self.visits += 1
            return ("https://www.property24.com/for-sale/vierlanden/durbanville/western-cape/9559/117748166", html)
    rows = [{"id": "p24:117748166", "source": "property24", "bedrooms": 1,
             "url": "https://www.property24.com/General/RedirectToListing?listingNumber=P24-117748166"},
            {"id": "pp:T1", "source": "privateproperty", "url": "https://www.privateproperty.co.za/ld/T1"}]
    assert run.add_listing_details(rows, FakeReader(), "now") == 1          # Private Property is never visited
    assert rows[0]["trends_url"] == "https://www.property24.com/durbanville/vierlanden/property-trends/9559"
    assert (rows[0]["floor_m2"], rows[0]["levies"], rows[0]["details_checked"]) == (50, 1400, "now")
    assert "floor_m2" not in rows[1]


def test_listing_page_reader_respects_robots_and_blocks():
    from dealfinder.details import ListingPageReader
    import dealfinder.details as d

    class Resp:
        def __init__(self, url, status=200, text="ok"): self.url, self.status_code, self.text = url, status, text
    calls = []
    robots = "User-agent: *\nDisallow: /ld/\n"
    def fake_get(url, **kw):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(url, 200, robots)
        return Resp(url, 429 if "slow" in url else 200, "<html></html>")
    orig = d.requests.get
    d.requests.get = fake_get
    try:
        r = ListingPageReader(limit=5, pause=0)
        assert r.read("https://www.privateproperty.co.za/ld/T5666495") is None   # robots.txt says no
        assert r.read("https://www.property24.com/for-sale/x/1") is not None
        assert r.read("https://www.property24.com/slow/2") is None and r.blocked
        assert r.read("https://www.property24.com/for-sale/x/3") is None         # stopped after being told to slow down
    finally:
        d.requests.get = orig
    assert "https://www.privateproperty.co.za/ld/T5666495" not in calls


def test_real_privateproperty_daily_summary():
    found = {r["id"]: r for r in parse_message(load("pp_real_daily.eml"))}
    assert len(found) == 6
    b = found["pp:T5666495"]
    assert (b["price"], b["bedrooms"], b["property_type"], b["suburb"], b["address"]) == \
        (3_900_000, 4, "house", "Bendor", "27 Bendor Drive")
    assert b["url"] == "https://www.privateproperty.co.za/ld/T5666495"
    assert b["image_url"].startswith("https://images.pp.co.za/listing/T5666495/")
    assert found["pp:T5666748"]["property_type"] == "commercial"          # "Retail space in Fauna Park"
    farm = found["pp:T5666544"]
    assert farm["property_type"] == "farm" and farm["erf_m2"] == 227_000 and farm["address"] is None  # "JAFFRAY"


def test_real_privateproperty_price_drop_instant_alert():
    (r,) = parse_message(load("pp_real_instant.eml"))
    assert (r["id"], r["price"], r["property_type"], r["suburb"]) == ("pp:T5400760", 1_230_000, "land", "Bendor")


def test_same_home_on_both_sites_counts_once():
    from dealfinder.scoring import mark_duplicates
    rows = [
        {"id": "p24:1", "source": "property24", "suburb": "Bendor", "property_type": "house", "bedrooms": 4, "price": 3_899_000},
        {"id": "pp:T1", "source": "privateproperty", "suburb": "Bendor", "property_type": "house", "bedrooms": 4, "price": 3_900_000},
        {"id": "pp:T2", "source": "privateproperty", "suburb": "Bendor", "property_type": "house", "bedrooms": 4, "price": 3_950_000},
        {"id": "p24:2", "source": "property24", "suburb": "De Bron", "property_type": "land", "bedrooms": None, "price": 1_230_000},
        {"id": "pp:T3", "source": "privateproperty", "suburb": "Debron", "property_type": "land", "bedrooms": None, "price": 1_230_000},
    ]
    assert mark_duplicates(rows) == 2
    dup = {r["id"]: r["duplicate_of"] for r in rows}
    assert dup == {"p24:1": None, "pp:T1": "p24:1", "pp:T2": None, "p24:2": None, "pp:T3": "p24:2"}


def test_town_for_private_property_listings():
    import dealfinder.services as svc

    class NoDB:
        def select_all(self, *a, **k): return []
    geo = svc.Geocoder(NoDB())
    geo._search = lambda q, limit=1: {"Schoongezicht, South Africa": [(-26.2, 28.0), (-33.817, 18.648)],
                                      "Atlantis, South Africa": [(-33.57, 18.49)]}.get(q, [])
    known = {"bendor": ("Polokwane", "Limpopo")}
    recs = [{"suburb": "Bendor"}, {"suburb": "Tzaneen"}, {"suburb": "Schoongezicht"}, {"suburb": "Atlantis"}]
    for r in recs:
        geo.resolve_town(r, known)
    assert [r.get("city") for r in recs] == ["Polokwane", "Tzaneen", "Durbanville", None]  # Atlantis is outside our areas


def test_street_address_gives_an_exact_pin_only_if_near_the_suburb():
    import dealfinder.services as svc

    class NoDB:
        def select_all(self, *a, **k): return []
    answers = {"Polokwane, Limpopo, South Africa": (-23.906, 29.461),
               "Bendor, Polokwane, Limpopo, South Africa": (-23.888, 29.485),
               "27 Bendor Drive, Bendor, Polokwane, South Africa": (-23.884, 29.489),
               "1 Far Road, Bendor, Polokwane, South Africa": (-23.70, 29.30)}
    geo = svc.Geocoder(NoDB())
    geo._lookup = lambda q: answers.get(q, (None, None))
    r = {"suburb": "Bendor", "city": "Polokwane", "province": "Limpopo", "address": "27 Bendor Drive"}
    assert geo.locate(r) == (-23.884, 29.489) and r["pin_level"] == "address"
    r2 = {"suburb": "Bendor", "city": "Polokwane", "province": "Limpopo", "address": "1 Far Road"}
    assert geo.locate(r2) == (-23.888, 29.485) and r2["pin_level"] == "suburb"


def test_private_property_metro_is_not_used_as_the_town():
    info = match_listing("https://www.privateproperty.co.za/for-sale/western-cape/cape-town/durbanville/kenridge/T5637671")
    assert (info["suburb"], info["city"]) == ("Kenridge", "Durbanville")
    info = match_listing("https://www.privateproperty.co.za/for-sale/western-cape/cape-town/northern-suburbs/oakglen/T5536663")
    assert (info["suburb"], info["city"]) == ("Oakglen", None)   # worked out later from the suburb


def test_one_bedroom_more_or_less_counts_with_an_adjustment():
    now = datetime.now(timezone.utc)
    rows = [_row("x", 2_000_000, beds=3, now=now), _row(1, 2_400_000, beds=3, now=now),
            _row(2, 3_000_000, beds=4, now=now), _row(3, 1_800_000, beds=2, now=now),
            _row(4, 2_100_000, beds=3, now=now)]
    score_listings(rows, now)
    x = rows[0]
    assert x["score_basis"] == "price_similar_beds" and x["comps_count"] == 4
    # 2.4m, 2.1m as-is; 3.0m 4-bed -> 2.64m; 1.8m 2-bed -> 2.016m; median of those = 2.25m
    assert x["suburb_median"] == 2_250_000.0


def test_outliers_and_very_expensive_listings_are_left_out():
    now = datetime.now(timezone.utc)
    rows = [_row("x", 2_000_000, now=now)] + [_row(i, p, now=now) for i, p in
            enumerate([2_200_000, 2_300_000, 2_400_000, 9_500_000, 25_000_000])]
    score_listings(rows, now)
    assert rows[0]["comps_count"] == 3 and rows[0]["suburb_median"] == 2_300_000


def test_deal_alerts_are_queued_for_new_listings_and_price_drops():
    rows = [
        {"id": "a", "region": "polokwane", "deal_score": 18.0, "wide_score": 18.0},
        {"id": "b", "region": "polokwane", "deal_score": None, "wide_score": 9.0},
        {"id": "c", "region": "polokwane", "deal_score": 2.0, "wide_score": 2.0},      # not a deal
        {"id": "d", "region": "durbanville", "deal_score": 12.0, "duplicate_of": "a"},  # second copy
        {"id": "e", "region": "other", "deal_score": 30.0},                             # outside our areas
        {"id": "f", "region": "durbanville", "deal_score": 7.0},                        # old listing, no change
    ]
    q = run.queue_deal_alerts(rows, new_ids={"a", "c", "d", "e"}, drops={"b": 2_500_000})
    assert q == [{"listing_id": "a", "reason": "new", "old_price": None},
                 {"listing_id": "b", "reason": "price_drop", "old_price": 2_500_000}]


def test_same_house_listed_by_two_agents_on_one_site_counts_once():
    from dealfinder.scoring import mark_duplicates
    rows = [{"id": "p24:a", "source": "property24", "suburb": "Bendor", "property_type": "house", "bedrooms": 3, "price": 1_780_000, "first_seen": "1"},
            {"id": "p24:b", "source": "property24", "suburb": "Bendor", "property_type": "house", "bedrooms": 3, "price": 1_780_000, "first_seen": "2"},
            {"id": "p24:c", "source": "property24", "suburb": "Bendor", "property_type": "house", "bedrooms": 3, "price": 1_850_000, "first_seen": "3"}]
    assert mark_duplicates(rows) == 1
    assert [r["duplicate_of"] for r in rows] == [None, "p24:a", None]   # 4% apart: a different house


def test_townhouses_are_not_compared_with_houses():
    now = datetime.now(timezone.utc)
    rows = [_row("th", 750_000, beds=2, now=now)] + [_row(i, 3_000_000, beds=2, now=now) for i in range(4)]
    rows[0]["property_type"] = "townhouse"
    score_listings(rows, now)
    assert rows[0]["deal_score"] is None   # only houses to compare with: no score rather than a fake "75% below"
