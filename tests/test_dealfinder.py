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


def test_trend_link_from_resolved_listing_url():
    rows = [{"id": "p24:117748166", "source": "property24",
             "url": "https://www.property24.com/General/RedirectToListing?listingNumber=P24-117748166"}]
    import run as r
    orig = r.RedirectResolver
    r.RedirectResolver = lambda limit: (lambda url: "https://www.property24.com/for-sale/vierlanden/durbanville/western-cape/9559/117748166")
    try:
        assert r.add_trend_links(rows, 10) == 1
    finally:
        r.RedirectResolver = orig
    assert rows[0]["trends_url"] == "https://www.property24.com/durbanville/vierlanden/property-trends/9559"
