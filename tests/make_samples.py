"""Creates sample alert emails used by the tests (fake listings, realistic layout)."""
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent / "sample_emails"

def card(href, title, price, beds, baths, floor, erf=None):
    erf_html = f"<span>Erf size {erf} m²</span>" if erf else ""
    return f"""
    <tr><td style="padding:10px">
      <a href="{href}"><img src="https://example.com/x.jpg" alt=""></a>
      <table><tr><td>
        <a href="{href}" style="font-weight:bold">{title}</a><br>
        <span style="font-size:18px">{price}</span><br>
        <span>{beds} Bedrooms</span> <span>{baths} Bathrooms</span>
        <span>{floor} m²</span> {erf_html}
      </td></tr></table>
    </td></tr>"""

def p24_email():
    cards = "".join([
        card("https://www.property24.com/for-sale/sonstraal-heights/durbanville/western-cape/11014/115000001?utm_source=alert",
             "3 Bedroom House in Sonstraal Heights", "R 2 450 000", 3, 2, 180, "650"),
        card("https://click.property24.com/ls/click?upn=abc%2F123",
             "2 Bedroom Apartment in Bellville", "R 1 150 000", 2, 1, 75),
        card("https://www.property24.com/for-sale/bendor/polokwane/limpopo/6450/115000003",
             "4 Bedroom House in Bendor", "Reduced from R 1 950 000 to R 1 795 000", 4, 3, "250", "1 100"),
    ])
    html = f"""<html><body><table>
      <tr><td><h2>New listings matching your alert</h2></td></tr>{cards}
      <tr><td><a href="https://www.property24.com/unsubscribe?id=1">Unsubscribe</a> Home loans from R 5 000 pm</td></tr>
    </table></body></html>"""
    m = EmailMessage()
    m["From"] = "Property24 <alerts@property24.com>"
    m["To"] = "bot@example.com"
    m["Subject"] = "3 new properties match your search"
    m.set_content("View in HTML")
    m.add_alternative(html, subtype="html")
    return m

def pp_text_email():
    body = """Your Private Property alert

3 bedroom townhouse for sale in Tzaneen Central
R 1 290 000 | 3 Beds | 2 Baths | 140 m2
https://www.privateproperty.co.za/for-sale/limpopo/tzaneen/tzaneen/tzaneen-central/T4920123

Vacant land for sale in Durbanville Hills
R 890 000 | Land 600 m2
https://www.privateproperty.co.za/for-sale/western-cape/cape-town/durbanville/durbanville-hills/T4920124
"""
    m = EmailMessage()
    m["From"] = "Private Property <noreply@privateproperty.co.za>"
    m["To"] = "bot@example.com"
    m["Subject"] = "New listings"
    m.set_content(body)
    return m

if __name__ == "__main__":
    HERE.mkdir(exist_ok=True)
    (HERE / "property24_alert.eml").write_bytes(bytes(p24_email()))
    (HERE / "privateproperty_alert.eml").write_bytes(bytes(pp_text_email()))
    print("written")
