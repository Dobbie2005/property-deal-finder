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


# --- Suburb outlines (Areas button) -------------------------------------------------------
from dealfinder import areas as areas_mod


def _sq(lat, lng, d=0.01):
    return {"type": "Polygon", "coordinates": [[[lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat]]]}


def test_collect_areas_groups_by_suburb_and_town():
    rows = [
        {"suburb": "Sonstraal", "city": "Durbanville", "region": "durbanville", "lat": -33.83, "lng": 18.66, "pin_level": "suburb"},
        {"suburb": "Sonstraal", "city": "Durbanville", "region": "durbanville", "lat": -33.80, "lng": 18.60, "pin_level": "address"},
        {"suburb": "Sonstraal", "city": "Durbanville", "region": "durbanville", "lat": -33.83, "lng": 18.66, "duplicate_of": "x"},
        {"suburb": "Bendor", "city": "Polokwane", "region": "polokwane", "lat": -23.89, "lng": 29.49, "pin_level": "suburb"},
        {"suburb": "Somewhere", "city": "Cape Town", "region": "other", "lat": -33.9, "lng": 18.4, "pin_level": "suburb"},
    ]
    got = areas_mod.collect_areas(rows)
    assert set(got) == {"durbanville/sonstraal", "polokwane/bendor"}
    assert got["durbanville/sonstraal"]["lat"] == -33.83  # suburb-level pins win over street addresses


def test_pick_outline_rejects_wrong_shapes():
    centre = (-33.83, 18.66)
    town = {"class": "boundary", "geojson": _sq(-33.9, 18.6, 0.3), "boundingbox": ["-33.9", "-33.6", "18.6", "18.9"]}
    school = {"class": "amenity", "geojson": _sq(-33.835, 18.655), "boundingbox": ["-33.835", "-33.825", "18.655", "18.665"]}
    street = {"class": "highway", "geojson": {"type": "LineString", "coordinates": []}, "boundingbox": ["-33.84", "-33.82", "18.65", "18.67"]}
    far = {"class": "place", "geojson": _sq(-23.9, 29.4), "boundingbox": ["-23.9", "-23.89", "29.4", "29.41"]}
    good = {"class": "place", "geojson": _sq(-33.835, 18.655), "boundingbox": ["-33.835", "-33.825", "18.655", "18.665"]}
    assert areas_mod.pick_outline([town, school, street, far], centre) is None
    assert areas_mod.pick_outline([town, school, good], centre) == good["geojson"]


class _AreasDB:
    def __init__(self, existing):
        self.existing, self.upserts = existing, []

    def select_all(self, table, order, select="*", filters=None):
        return self.existing

    def upsert(self, table, rows):
        self.upserts.append(rows)


class _Finder:
    def __init__(self, hits, limit=10):
        self.hits, self.calls, self.limit, self.asked = hits, 0, limit, []

    def can_search(self):
        return self.calls < self.limit

    def search(self, name, city):
        self.calls += 1
        self.asked.append(name)
        return self.hits.get(name, [])


def test_update_areas_never_overwrites_drawn_outlines_and_rechecks_later():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    rows = [{"suburb": s, "city": "Durbanville", "region": "durbanville", "lat": -33.83, "lng": 18.66, "pin_level": "suburb"}
            for s in ("Sonstraal", "Aurora", "Vierlanden", "Kenridge")]
    db = _AreasDB([
        {"key": "durbanville/aurora", "boundary_source": "drawn", "boundary_checked_at": None},
        {"key": "durbanville/vierlanden", "boundary_source": "none", "boundary_checked_at": "2026-10-20T00:00:00+00:00"},
        {"key": "durbanville/kenridge", "boundary_source": "none", "boundary_checked_at": "2026-08-01T00:00:00+00:00"},
    ])
    good = {"class": "place", "geojson": _sq(-33.835, 18.655), "boundingbox": ["-33.835", "-33.825", "18.655", "18.665"]}
    finder = _Finder({"Sonstraal": [good]})
    info = areas_mod.update_areas(db, rows, finder, now, official=_NoOfficial())
    assert sorted(finder.asked) == ["Kenridge", "Sonstraal"]  # drawn one skipped, recent "none" skipped
    assert (info["areas"], info["checked"], info["found"], info["official"]) == (4, 2, 1, 0)
    checked = {r["key"]: r for r in db.upserts[-1]}
    assert checked["durbanville/sonstraal"]["boundary_source"] == "osm"
    assert checked["durbanville/kenridge"]["boundary_source"] == "none"
    assert all("boundary" not in r for r in db.upserts[0])  # the first save never touches outlines


class _NoOfficial(areas_mod.OfficialOutlines):
    def __init__(self, sources=None):
        super().__init__()
        self.sources, self.asked = sources or [], []

    def shapes(self, region):
        self.asked.append(region)
        return self.sources


def _feat(name, lat, lng, d=0.005):
    g = _sq(lat, lng, d)
    return {"name": name, "geometry": g, "centre": areas_mod._bbox_centre(g)}


def test_match_official_by_name_distance_and_suffix():
    feats = [("City of Cape Town official suburbs", [
        _feat("SONSTRAAL HEIGHTS", -33.845, 18.675), _feat("DURBANVILLE", -33.835, 18.645),
        _feat("AURORA", -33.83, 18.63), _feat("AURORA", -33.826, 18.63),       # one suburb in two pieces
        _feat("PROTEA HEIGHTS", -34.05, 18.47),                               # same name, far away
    ])]
    m = areas_mod.match_official({"name": "Sonstraal", "city": "Durbanville", "lat": -33.843, "lng": 18.678}, feats)
    assert m and m[1] == "SONSTRAAL HEIGHTS"
    m = areas_mod.match_official({"name": "Durbanville Central", "city": "Durbanville", "lat": None, "lng": None}, feats)
    assert m and m[1] == "DURBANVILLE"                                          # "Central" dropped, town centre used
    m = areas_mod.match_official({"name": "Aurora", "city": "Durbanville", "lat": -33.83, "lng": 18.632}, feats)
    assert m and m[0]["type"] == "MultiPolygon"
    assert areas_mod.match_official({"name": "Protea Heights", "city": "Brackenfell", "lat": -33.882, "lng": 18.714}, feats) is None


def test_official_outlines_win_and_drawn_ones_stay():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    rows = [{"suburb": s, "city": "Durbanville", "region": "durbanville", "lat": -33.843, "lng": 18.678, "pin_level": "suburb"}
            for s in ("Sonstraal", "Vierlanden")]
    db = _AreasDB([
        {"key": "durbanville/vierlanden", "name": "Vierlanden", "region": "durbanville", "boundary_source": "drawn"},
        {"key": "durbanville/sonstraal", "name": "Sonstraal", "region": "durbanville", "boundary_source": "osm"},
        {"key": "durbanville/handadded", "name": "Hand Added", "city": "Durbanville", "region": "durbanville",
         "lat": None, "lng": None, "boundary_source": None},
    ])
    official = _NoOfficial([("City of Cape Town official suburbs", [_feat("SONSTRAAL HEIGHTS", -33.845, 18.675)])])
    finder = _Finder({})
    info = areas_mod.update_areas(db, rows, finder, now, official=official)
    got = [r for batch in db.upserts for r in batch if r.get("boundary_source") == "official"]
    assert [r["key"] for r in got] == ["durbanville/sonstraal"]               # replaces the OpenStreetMap one
    assert info["official"] == 1 and info["official_missed"] == 1             # hand-added one checked, not found
    assert not any(r["key"] == "durbanville/vierlanden" for batch in db.upserts[1:] for r in batch)
    assert "Sonstraal" not in finder.asked


def test_deleted_areas_are_left_alone():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    rows = [{"suburb": "Sonstraal", "city": "Durbanville", "region": "durbanville", "lat": -33.843, "lng": 18.678, "pin_level": "suburb"}]
    db = _AreasDB([{"key": "durbanville/sonstraal", "name": "Sonstraal", "region": "durbanville", "boundary_source": None, "hidden": True}])
    official = _NoOfficial([("City of Cape Town official suburbs", [_feat("SONSTRAAL", -33.843, 18.678)])])
    finder = _Finder({})
    info = areas_mod.update_areas(db, rows, finder, now, official=official)
    assert info["official"] == 0 and finder.asked == [] and official.asked == []
    assert all("hidden" not in r for batch in db.upserts for r in batch)  # never un-deletes it


def test_inside_point_for_normal_and_odd_shapes():
    sq = _sq(-23.94, 29.93, 0.02)
    lat, lng = areas_mod.inside_point(sq)
    assert abs(lat - (-23.93)) < 1e-6 and abs(lng - 29.94) < 1e-6
    # a "C" shape whose centre is outside it (like Clearwaters Cove along the dam)
    c = {"type": "Polygon", "coordinates": [[[0, 0], [3, 0], [3, 1], [1, 1], [1, 2], [3, 2], [3, 3], [0, 3], [0, 0]]]}
    lat, lng = areas_mod.inside_point(c)
    assert areas_mod._inside([(p[0], p[1]) for p in c["coordinates"][0]], lng, lat)


def test_listings_go_inside_their_suburb_outline_and_keep_its_region():
    pins = areas_mod.area_pins([
        {"key": "tzaneen/haenertsburg", "region": "tzaneen", "boundary": _sq(-23.95, 29.93, 0.02)},
        {"key": "tzaneen/gone", "region": "tzaneen", "boundary": _sq(-23.9, 30.1, 0.01), "hidden": True},
    ])
    rows = [
        {"suburb": "Haenertsburg", "city": "Tzaneen", "lat": -23.83, "lng": 30.16, "pin_level": "town", "region": "other"},
        {"suburb": "Haenertsburg", "city": "Tzaneen", "lat": -23.941, "lng": 29.941, "pin_level": "address", "region": "other"},
        {"suburb": "Gone", "city": "Tzaneen", "lat": -23.83, "lng": 30.16, "pin_level": "town", "region": "tzaneen"},
    ]
    assert areas_mod.place_pins_in_areas(rows, pins) == 1
    assert rows[0]["pin_level"] == "area" and rows[0]["region"] == "tzaneen" and abs(rows[0]["lat"] - (-23.94)) < 1e-6
    assert rows[1]["pin_level"] == "address" and rows[2]["pin_level"] == "town"


# --- Motivated sellers, weekly summary ------------------------------------------------------
from dealfinder import signals, digest as digest_mod


def test_price_history_counts_drops_and_flags():
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    r = {"price": 2_000_000, "first_price": 2_000_000, "first_seen": t0, "title": "3 Bedroom House in Bendor"}
    signals.update_price_history(r, t0)
    for day, price in ((20, 1_900_000), (40, 1_900_000), (50, 1_750_000)):
        r["price"] = price
        signals.update_price_history(r, t0 + timedelta(days=day))
    assert [p for _, p in r["price_history"]] == [2_000_000, 1_900_000, 1_750_000]
    assert r["price_drops"] == 2
    flags = signals.seller_flags(r, t0 + timedelta(days=70))
    assert flags == ["dropped_twice", "big_drop", "long_listed"]
    assert signals.drops_since(r, t0 + timedelta(days=45)) == (1_900_000, 1_750_000)
    assert signals.drops_since(r, t0 + timedelta(days=55)) is None


def test_auction_and_bank_sale_flags_from_agency_or_title():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert "auction" in signals.seller_flags({"agency": "High Street Auctions", "first_seen": now}, now)
    assert "bank_sale" in signals.seller_flags({"title": "Bank repossessed 3 bed house", "first_seen": now}, now)
    assert signals.seller_flags({"agency": "Keller Williams North", "first_seen": now}, now) == []


def test_weekly_digest_content_and_timing():
    now = datetime(2026, 10, 26, 3, 20, tzinfo=timezone.utc)  # Monday 05:20 in South Africa
    fresh = now - timedelta(days=2)
    rows = [
        {"id": "a", "region": "polokwane", "suburb": "Bendor", "city": "Polokwane", "price": 1_500_000, "deal_score": 22,
         "first_seen": fresh, "last_seen": fresh, "url": "https://x/a", "flags": []},
        {"id": "b", "region": "durbanville", "suburb": "Sonstraal", "city": "Durbanville", "price": 2_700_000,
         "first_seen": now - timedelta(days=40), "last_seen": fresh, "url": "https://x/b", "flags": ["dropped_twice"],
         "price_history": [["2026-09-17", 3_000_000], ["2026-10-01", 2_900_000], ["2026-10-24", 2_700_000]]},
        {"id": "c", "region": "durbanville", "suburb": "Aurora", "city": "Durbanville", "price": 3_000_000,
         "first_seen": fresh, "last_seen": fresh, "url": "https://x/c", "duplicate_of": "b"},
    ]
    notes = [{"listing_id": "b", "stage": "viewing", "tags": ["deceased"]}]
    subject, html_out = digest_mod.build_digest(rows, notes, now)
    assert subject == "Weekly property summary: 1 new listing, 1 price drop"
    assert "22% below" in html_out and "dropped from R 2 900 000" in html_out
    assert "deceased estate" in html_out and "<b>Viewing</b>" in html_out and "Aurora" not in html_out
    assert digest_mod.week_key(now) == "2026-W44"
    assert digest_mod.due(set(), now) and not digest_mod.due({"2026-W44"}, now)
    assert not digest_mod.due(set(), datetime(2026, 10, 25, 22, 30, tzinfo=timezone.utc))  # Mon 00:30 SAST: wait


# --- Bank sales (Private Property's public bank-sales pages) -----------------------------------
from dealfinder import banksales


def test_parse_bank_sales_page():
    recs, nxt = banksales.parse_bank_sales_page((SAMPLES.parent / "bank_sales_sample.html").read_text())
    assert nxt == "https://www.privateproperty.co.za/bank-sales/western-cape/cape-town/55?page=2"
    a, b = recs
    assert a["id"] == "pp:T5424140" and a["price"] == 2_100_000 and a["property_type"] == "apartment"
    assert (a["bedrooms"], a["bathrooms"], a["floor_m2"]) == (2, 1, 45)
    assert (a["suburb"], a["city"], a["address"]) == ("Zonnebloem", "Cape Town City Bowl", "78 Chapel Towers, 45 chapel street")
    assert a["pin_level"] == "address" and abs(a["lat"] + 33.9286) < 1e-3
    assert a["bank"] == "Standard Bank" and a["agency"] == "Standard Bank · EasySell" and a["status"] == "for_sale"
    assert a["url"].endswith("/T5424140") and a["image_url"].startswith("https://images.pp.co.za/")
    assert b["status"] == "under_offer" and b["city"] == "Kraaifontein" and "lat" not in b
    assert b["erf_m2"] == 29_000 and b["bank"] == "ABSA"
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    b["first_seen"] = now
    assert signals.seller_flags(b, now) == ["bank_sale", "deceased_estate", "fixer_upper", "under_offer"]


class _FakeReader:
    def __init__(self, pages, block_after=None):
        self.pages, self.visits, self.limit, self.blocked, self.block_after = pages, 0, 8, None, block_after

    def read(self, url):
        if self.block_after is not None and self.visits >= self.block_after:
            self.blocked = "www.privateproperty.co.za answered 503; stopped for this run"
            return None
        self.visits += 1
        return (url, self.pages[url]) if url in self.pages else None


def test_read_bank_sales_follows_next_pages_and_stops_when_blocked():
    html = (SAMPLES.parent / "bank_sales_sample.html").read_text()
    page2 = html.replace('<link rel="next" href="https://www.privateproperty.co.za/bank-sales/western-cape/cape-town/55?page=2" />', "")
    html = html.replace("western-cape/cape-town/55?page=2", "limpopo/8?page=2")
    pg = dict(banksales.pages())
    assert set(pg) == {"Limpopo", "Western Cape"}
    pages = {pg["Limpopo"]: html, pg["Limpopo"] + "?page=2": page2, pg["Western Cape"]: page2}
    found, notes = banksales.read_bank_sales(_FakeReader(pages))
    assert len(found) == 6 and notes == ["Limpopo: 4", "Western Cape: 2"]
    found, notes = banksales.read_bank_sales(_FakeReader(pages, block_after=1))
    assert len(found) == 2 and notes[-1].startswith("⚠️")


def test_merged_suburbs_move_listings_and_undo_restores_them():
    areas_rows = [
        {"key": "kuilsriver/kuilsriversouth", "name": "Kuils River South", "city": "Kuils River", "merged_into": "kuilsriver/kuilsriver"},
        {"key": "kuilsriver/kuilsriver", "name": "Kuils River", "city": "Kuils River", "boundary": _sq(-33.93, 18.68, 0.02)},
    ]
    r = {"suburb": "Kuils River South", "city": "Kuils River", "lat": -33.95, "lng": 18.70, "pin_level": "suburb"}
    addr = {"suburb": "Kuils River South", "city": "Kuils River", "lat": -33.951, "lng": 18.701, "pin_level": "address"}
    assert areas_mod.apply_merges([r, addr], areas_rows) == 2
    assert (r["suburb"], r["listed_suburb"], r["lat"], r["pin_level"]) == ("Kuils River", "Kuils River South", None, None)
    assert addr["suburb"] == "Kuils River" and addr["lat"] == -33.951  # a street address stays put
    # next run: a new alert email says "Kuils River South" again -> still merged, nothing re-geocoded
    r.update(suburb="Kuils River South", lat=-33.93, lng=18.69, pin_level="area")
    areas_mod.apply_merges([r], areas_rows)
    assert r["suburb"] == "Kuils River"
    # undo the merge
    areas_rows[0]["merged_into"] = None
    areas_mod.apply_merges([r], areas_rows)
    assert (r["suburb"], r["listed_suburb"], r["lat"]) == ("Kuils River South", None, None)
    # merged areas never give pins
    assert set(areas_mod.area_pins(areas_rows + [{**areas_rows[1], "key": "x/y", "merged_into": "kuilsriver/kuilsriver"}])) == {"kuilsriver/kuilsriver"}



def test_regions_from_the_database_and_town_aliases():
    from dealfinder import config
    saved = [dict(r) for r in config.DEFAULT_REGIONS]
    try:
        config.set_regions(saved + [{"key": "mookgophong", "label": "Mookgophong", "lat": -24.517, "lng": 28.717,
                                     "radius_km": 20, "province": "Limpopo",
                                     "towns": [{"name": "Mookgophong", "lat": -24.517, "lng": 28.717,
                                                "aliases": ["Mookgopong", "Naboomspruit"]}]}])
        assert "mookgophong" in config.REGIONS and "Mookgophong" in config.TOWNS
        assert config.canonical_town("Naboomspruit") == "Mookgophong"
        assert config.canonical_town("Polokwane (Pietersburg)") == "Polokwane"
        assert config.canonical_town("Pietersburg") == "Polokwane" and config.canonical_town("Kraaifontein") is None
        assert assign_region({"lat": -24.52, "lng": 28.70}) == "mookgophong"
        assert config.P24_TOWN_IDS["703"] == ("Polokwane", "Limpopo")
        config.set_regions([])  # an empty table never wipes your areas
        assert "mookgophong" in config.REGIONS
    finally:
        config.set_regions(config.DEFAULT_REGIONS)


def test_robots_standard_longest_match():
    from dealfinder.robots import allowed
    propdata = "User-Agent: *\nDisallow: /secure/\nDisallow: /results/\nDisallow: /results/*/None\nAllow: /results/*/\nUser-agent: trovitBot\n"
    assert allowed(propdata, "/results/residential/for-sale/polokwane/")
    assert not allowed(propdata, "/results/")
    assert not allowed(propdata, "/results/residential/None")
    assert not allowed(propdata, "/secure/x")
    pam = "User-agent: *\nDisallow: /property-search/*-bedroom-*/\nDisallow: /property-search/*?\nUser-agent: AhrefsBot\nDisallow: /\n"
    assert allowed(pam, "/property-search/properties-for-sale-polokwane/123")
    assert not allowed(pam, "/property-search/properties-for-sale-polokwane/123?page=2")
    assert not allowed(pam, "/property-search/3-bedroom-houses/")
    assert allowed("User-agent: *\nDisallow: /ld/*\n", "/for-sale/x/T1") and not allowed("User-agent: *\nDisallow: /ld/*\n", "/ld/T1")
    assert not allowed("User-agent: property-deal-finder\nDisallow: /\nUser-agent: *\nAllow: /\n", "/anything")
