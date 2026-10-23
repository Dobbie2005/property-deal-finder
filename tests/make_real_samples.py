"""Scrubbed copies of real Property24 alert emails (Oct 2026): personal ids and tokens removed."""
import re
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent / "sample_emails"
L = "https://www.property24.com/General/RedirectToListing?listingNumber=P24-{n}&source={src}&id=1&uaid=2"
CRIT = {"durbanville": "c3A9c3QlM2RGb3JTYWxlJTI2Y2lkJTNkNDM5JTI2cyUzZDk1MTAlMmM5NTExJTJjOTUyMCUyYzk1MjE%3d",
        "bellville": "c3A9c3QlM2RGb3JTYWxlJTI2Y2lkJTNkNDQxJTI2cyUzZDgyMTY%3d",
        "brackenfell": "c3A9c3QlM2RGb3JTYWxlJTI2Y2lkJTNkNDU0JTI2cyUzZDEyODYzJTJjMTY2NzA%3d"}


def card(n, src, price, type_line, suburb, agency, icons, show=None):
    link = L.format(n=n, src=src)
    lines = [f"[image: P24-{n}] ", f"<{link}> ", "  ", "  ", f"{price} ", f"<{link}> ", f"{type_line} ",
             f"{suburb} ", f"{agency} "]
    if icons:
        lines.append(icons + "     ")
    lines += ["View Property ", f"<{link}> ", "  ", "  "]
    if show:
        lines += [f"{show}   ", "  "]
    return "\n".join(lines)


def email(subject, heading, cards, crit, intro="we have results matching your alert."):
    body = "\n".join([" Property24 ", "  ", "  ", "[image: Property24] <https://www.property24.com/> ", "  ",
                      f"Hi there, {intro} ", "    ", f"{heading} ", *cards, "  ",
                      "View matching properties ",
                      "<https://www.property24.com/Results/ShowSearchResultsForAlert?alertId=2&isShowDay=False&Token=x> ",
                      "  ", "[image: Email Advert] ",
                      f"<https://www.property24.com/advertimageurlredirect?token=x&u=1&l=205&criteriaString={crit}> ",
                      "  ", "*Property Reports* ", "Find *sold prices* and *buy property reports* ", "  ",
                      "Unsubscribe ", "<https://www.property24.com/user/removealert?token=x> ",
                      "Manage alerts <https://www.property24.com/Alerts> "])
    m = EmailMessage()
    m["From"] = "Property24 Alert <no-reply@property24.com>"
    m["Subject"] = subject
    m.set_content(body)
    imgs = "".join(f'<img src="https://images.prop24.com/{n}0/Crop318x212" width="317"\r\n   alt="P24-{n}" border="0" />'
                   for n in re.findall(r"\[image: P24-(\d+)\]", body))
    m.add_alternative(f"<html><body>{imgs}<img src='https://www.property24.com/logo.png' alt='Property24'></body></html>",
                      subtype="html")
    return m


B = "[image: bedroom] {}     [image: bathroom] {}     [image: garage] {}"
samples = {
    "p24_real_new.eml": email("Townhouse for sale in Vierlanden P24-117748166", "New Properties",
        [card(117748166, "alert", "R 1 999 000", "1 Bedroom Townhouse", "Vierlanden", "Spectrum Realty", B.format(1, 1, 1))],
        CRIT["durbanville"]),
    "p24_real_reduced.eml": email("Apartment for sale in Loevenstein P24-117674245", "Price Reduced",
        [card(117674245, "reduced", "R 1 400 000", "2 Bedroom Apartment", "Loevenstein", "Landlords", B.format(2, 1, 1))],
        CRIT["bellville"], intro="we have new results matching your alert."),
    "p24_real_onshow.eml": email("Properties on show in multiple areas", "Properties on Show", [
        card(117725607, "showdayalert", "R 1 749 000", "3 Bedroom Apartment", "Normandie",
             "Wykland Properties - Riebeek Kasteel", B.format(3, 2, 3), "On Show Tomorrow 11:00 - 13:00"),
        card(117703912, "showdayalert", "R 3 899 000", "3 Bedroom House", "Brackenfell South",
             "Greeff Christie's International Real Estat...", B.format(3, 2, 4), "On Show Tomorrow 10:00 - 12:00"),
        card(117585844, "showdayalert", "R 4 650 000", "House", "Springbok Park", "Rawson Properties Durbanville",
             "[image: bathroom] 8     [image: garage] 8", "On Show Tomorrow 09:00 - 17:00  •  Sun 09:00 - 17:00"),
        card(117508534, "showdayalert", "R 890 000", "Vacant Land", "Northpine", "Quay 1 International Realty",
             None, "On Show Mon 18:00 - 18:30"),
        card(116749541, "showdayalert", "R 10 500 000", "Commercial Property", "Polokwane Central", "OVO Realty",
             "[image: bedroom] 32     [image: bathroom] 18", "On Show Tomorrow 14:00 - 17:00"),
    ], CRIT["brackenfell"]),
}

if __name__ == "__main__":
    for name, msg in samples.items():
        (HERE / name).write_bytes(bytes(msg))
    print("written", len(samples))
