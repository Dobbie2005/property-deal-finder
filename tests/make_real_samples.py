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

# --- Private Property (layout copied from real alerts, Oct 2026) ---------------------------------
PPL = "https://www.privateproperty.co.za/ld/{n}?utm_source&#x3D;{src}&amp;utm_medium&#x3D;email&amp;utm_campaign&#x3D;x"


def pp_daily(cards):
    lines, html = ["", "<http://privateproperty.co.za>", "", "                            Hello,",
                   "                            22 new listings that match your search criteria"], []
    for n, price, desc, addr in cards:
        link = PPL.format(n=n, src="daily-alert")
        lines += ["                        New", f"<{link}>", price, f"                                    {desc}"]
        if addr:
            lines += [f"                                    {addr}"]
        lines += [f"<{link}>", "View Property"]
        html.append(f'<a href="{link}"><img src="https://images.pp.co.za/listing/{n}/abc/320/200/contain/jpegorpng" '
                    f'alt="{desc}"></a><span>{price}</span><td>{desc}</td>')
    lines += ["<https://www.privateproperty.co.za/>", "Visit Private Property |", "Unsubscribe",
              "                Copyright © Private Property Listings (Pty) Ltd 1997 - 2026. All Rights Reserved"]
    m = EmailMessage()
    m["From"] = "Private Property <noreply@privateproperty.co.za>"
    m["Subject"] = "Latest Property Updates from Private Property"
    m.set_content("\r\n".join(lines))
    m.add_alternative("<html><body>" + "".join(html) + "</body></html>", subtype="html")
    return m


def pp_instant():
    link = PPL.format(n="T5400760", src="instant-alert")
    body = "\r\n".join(["", "<http://privateproperty.co.za>", "", "        Hello,", "        The price of this listing has dropped.",
                         "    Price reduced", f"<{link}>", "", f"<{link}>", "View Property",
                         "            R 1 230 000", "        ", "            Vacant land in Bendor", "        ",
                         "    Are you receiving too many emails?", " You can choose to receive a",
                         "<https://www.privateproperty.co.za/daily-alerts/sale/switchto?email&#x3D;x>",
                         "daily summary by adjusting your notifications.", "Unsubscribe"])
    m = EmailMessage()
    m["From"] = "Private Property Instant Alerts <noreply@privateproperty.co.za>"
    m["Subject"] = "Residential Land Price Reduced in Bendor - T5400760"
    m.set_content(body)
    m.add_alternative(f'<html><body><a href="{link}"><img src="https://images.pp.co.za/listing/T5400760/x/320/200/contain/jpegorpng"></a></body></html>',
                      subtype="html")
    return m


samples["pp_real_daily.eml"] = pp_daily([
    ("T5666748", "R 6 999 000", "Retail space in Fauna Park", None),
    ("T5666412", "R 3 500 000", "5 bed house in North View Estate", None),
    ("T5666495", "R 3 900 000", "4 bed house in Bendor", "27 Bendor Drive"),
    ("T5667020", "R 3 950 000", "4 bed house in Bendor", "30 Solomon Road, Bendor Woodhill Estate"),
    ("T5666529", "R 4 850 000", "3 bed house in Schoongezicht", None),
    ("T5666544", "R 3 000 000", "22.7 ha farm in Tzaneen", "JAFFRAY"),
])
samples["pp_real_instant.eml"] = pp_instant()

if __name__ == "__main__":
    for name, msg in samples.items():
        (HERE / name).write_bytes(bytes(msg))
    print("written", len(samples))
