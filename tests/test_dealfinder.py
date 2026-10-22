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
