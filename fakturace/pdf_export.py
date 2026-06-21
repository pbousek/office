"""PDF invoice generation with QR payment code."""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                               italic="DejaVuSans", boldItalic="DejaVuSans-Bold")

DARK = colors.HexColor("#2d3142")
MUTED = colors.HexColor("#6b7280")
LIGHT = colors.HexColor("#f4f4f6")


def _style(name, **kw):
    base = getSampleStyleSheet()["Normal"]
    kw.setdefault("fontName", "DejaVuSans")
    return ParagraphStyle(name, parent=base, **kw)


def _qr_image(iban: str, amount: float, vs: str, msg: str, currency: str = "CZK",
              bic: str = "", supplier_name: str = "") -> Image | None:
    if not iban:
        return None
    try:
        import qrcode as qr_lib
        iban_clean = iban.replace(" ", "")
        if currency == "CZK":
            payload = f"SPD*1.0*ACC:{iban_clean}*AM:{amount:.2f}*CC:CZK*X-VS:{vs}*MSG:{msg[:35]}"
        else:
            # SEPA EPC QR (EPC069-12) for EUR and other SEPA currencies
            name = (supplier_name or "Dodavatel")[:70]
            ref = msg[:35]
            payload = (
                f"BCD\n002\n1\nSCT\n{bic}\n{name}\n{iban_clean}\n"
                f"{currency}{amount:.2f}\n\n{ref}\n"
            )
        img = qr_lib.make(payload, error_correction=qr_lib.constants.ERROR_CORRECT_M)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Image(buf, width=32*mm, height=32*mm)
    except Exception:
        return None


def generate_invoice_pdf(invoice: dict, items: list[dict], totals: dict, settings: dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)

    h1 = _style("h1", fontSize=18, fontName="DejaVuSans-Bold", textColor=DARK, spaceAfter=2)
    h2 = _style("h2", fontSize=11, fontName="DejaVuSans-Bold", textColor=DARK, spaceBefore=10, spaceAfter=4)
    normal = _style("normal", fontSize=9, leading=13)
    small = _style("small", fontSize=8, textColor=MUTED)
    right = _style("right", fontSize=9, alignment=TA_RIGHT)
    bold_right = _style("boldright", fontSize=10, fontName="DejaVuSans-Bold", alignment=TA_RIGHT, textColor=DARK)
    muted = _style("muted", fontSize=8, textColor=MUTED)

    elems = []

    # Header row: title left, invoice number right
    header_data = [[
        Paragraph("FAKTURA", h1),
        Paragraph(f"<b>{invoice['number']}</b>", _style("nr", fontSize=14, fontName="DejaVuSans-Bold",
                  alignment=TA_RIGHT, textColor=DARK)),
    ]]
    header_table = Table(header_data, colWidths=[130*mm, 50*mm])
    header_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "BOTTOM")]))
    elems.append(header_table)
    elems.append(Spacer(1, 8*mm))

    # Supplier / Customer block
    sup = settings
    cust = invoice
    parties_data = [[
        Paragraph("<b>Dodavatel</b>", _style("ph", fontSize=8, textColor=MUTED)),
        Paragraph("<b>Odběratel</b>", _style("ph", fontSize=8, textColor=MUTED)),
    ],[
        Paragraph(f"<b>{sup.get('company_name','')}</b><br/>"
                  f"{sup.get('company_street','')}<br/>"
                  f"{sup.get('company_zip','')} {sup.get('company_city','')}<br/>"
                  f"IČO: {sup.get('company_ico','')}"
                  + (f"<br/>DIČ: {sup.get('company_dic','')}" if sup.get('company_dic') else "")
                  + (f"<br/>Tel.: {sup.get('company_phone','')}" if sup.get('company_phone') else "")
                  + (f"<br/>E-mail: {sup.get('company_email','')}" if sup.get('company_email') else "")
                  + (f"<br/>Web: {sup.get('company_website','')}" if sup.get('company_website') else ""),
                  normal),
        Paragraph(f"<b>{cust['customer_name']}</b><br/>"
                  f"{cust.get('customer_street','')}<br/>"
                  f"{cust.get('customer_zip','')} {cust.get('customer_city','')}<br/>"
                  + (f"IČO: {cust.get('customer_ico','')}<br/>" if cust.get('customer_ico') else "")
                  + (f"DIČ: {cust.get('customer_dic','')}" if cust.get('customer_dic') else ""),
                  normal),
    ]]
    parties = Table(parties_data, colWidths=[90*mm, 90*mm])
    parties.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LINEBELOW", (0,0), (-1,0), 0.5, LIGHT),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elems.append(parties)
    elems.append(Spacer(1, 6*mm))

    duzp = invoice.get("duzp") or invoice["issue_date"]
    vs = invoice["number"].replace("/", "").replace("-", "")

    # Dates row (2 columns)
    dates_data = [
        ["Datum vystavení:", invoice["issue_date"],
         "Datum splatnosti:", invoice["due_date"]],
        ["DUZP:", duzp,
         "Variabilní symbol:", vs],
        ["Forma úhrady:", "převodem", "", ""],
    ]
    dates_tbl = Table(dates_data, colWidths=[38*mm, 52*mm, 38*mm, 52*mm])
    dates_tbl.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVuSans"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("FONTNAME", (0,0), (0,-1), "DejaVuSans-Bold"),
        ("FONTNAME", (2,0), (2,-1), "DejaVuSans-Bold"),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))
    elems.append(dates_tbl)

    # Bank details stacked on the left
    bank_lines = []
    if sup.get("company_account"):
        bank_lines.append(["Číslo účtu:", sup.get("company_account", "")])
    if sup.get("company_iban"):
        bank_lines.append(["IBAN:", sup.get("company_iban", "")])
    if sup.get("company_swift"):
        bank_lines.append(["SWIFT:", sup.get("company_swift", "")])
    if sup.get("company_bank"):
        bank_lines.append(["Banka:", sup.get("company_bank", "")])
    if bank_lines:
        elems.append(Spacer(1, 3*mm))
        bank_tbl = Table(bank_lines, colWidths=[38*mm, 142*mm])
        bank_tbl.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "DejaVuSans"),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("FONTNAME", (0,0), (0,-1), "DejaVuSans-Bold"),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        elems.append(bank_tbl)
    elems.append(Spacer(1, 6*mm))

    # Items table
    vat_payer = bool(invoice["vat_payer"])
    if vat_payer:
        col_headers = ["Popis", "Množství", "Jednotka", "Cena/j.", "Sazba DPH", "Základ", "DPH", "Celkem"]
        col_widths = [62*mm, 18*mm, 16*mm, 18*mm, 18*mm, 18*mm, 14*mm, 16*mm]
    else:
        col_headers = ["Popis", "Množství", "Jednotka", "Cena/jednotku", "Celkem"]
        col_widths = [90*mm, 20*mm, 18*mm, 28*mm, 24*mm]

    rows = [col_headers]
    for it in items:
        base = it["quantity"] * it["unit_price"]
        if vat_payer:
            vat = round(base * it["vat_rate"] / 100, 2)
            rows.append([
                it["description"],
                f"{it['quantity']:g}",
                it["unit"],
                f"{it['unit_price']:,.2f}".replace(",", " ").replace(".", ","),
                f"{it['vat_rate']} %",
                f"{base:,.2f}".replace(",", " ").replace(".", ","),
                f"{vat:,.2f}".replace(",", " ").replace(".", ","),
                f"{base+vat:,.2f}".replace(",", " ").replace(".", ","),
            ])
        else:
            rows.append([
                it["description"],
                f"{it['quantity']:g}",
                it["unit"],
                f"{it['unit_price']:,.2f}".replace(",", " ").replace(".", ","),
                f"{base:,.2f}".replace(",", " ").replace(".", ","),
            ])

    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), DARK),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,-1), "DejaVuSans"),
        ("FONTNAME", (0,0), (-1,0), "DejaVuSans-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, LIGHT]),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#cccccc")),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("ALIGN", (0,0), (0,-1), "LEFT"),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elems.append(tbl)
    elems.append(Spacer(1, 4*mm))

    # Totals + QR
    currency = invoice.get("currency", "CZK")
    qr = _qr_image(
        iban=sup.get("company_iban", ""),
        amount=totals["total"],
        vs=invoice["number"].replace("/", "").replace("-", ""),
        msg=f"Faktura {invoice['number']}",
        currency=currency,
        bic=sup.get("company_swift", ""),
        supplier_name=sup.get("company_name", ""),
    )

    total_lines = []
    if vat_payer:
        for vl in totals["vat_lines"]:
            total_lines.append(
                f"Základ {vl['rate']} %: {vl['base']:,.2f} Kč  DPH: {vl['vat']:,.2f} Kč".replace(",", " ")
            )
        total_lines.append(f"DPH celkem: {totals['vat']:,.2f} Kč".replace(",", " "))
    total_lines.append(f"Celkem k úhradě: {totals['total']:,.2f} Kč".replace(",", " "))

    total_paras = [Paragraph(l, right if "Celkem k úhradě" not in l else bold_right) for l in total_lines]

    if qr:
        totals_row = [[qr, [Spacer(1, 1)] + total_paras]]
        totals_tbl = Table(totals_row, colWidths=[36*mm, 144*mm])
        totals_tbl.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "BOTTOM")]))
    else:
        totals_tbl = Table([[Spacer(1,1), total_paras]], colWidths=[36*mm, 144*mm])
        totals_tbl.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "BOTTOM")]))

    elems.append(totals_tbl)

    if invoice.get("note"):
        elems.append(Spacer(1, 6*mm))
        elems.append(Paragraph(f"Poznámka: {invoice['note']}", muted))

    if not vat_payer:
        elems.append(Spacer(1, 4*mm))
        elems.append(Paragraph("Nejsem plátce DPH.", muted))

    if sup.get("company_registration"):
        elems.append(Spacer(1, 3*mm))
        elems.append(Paragraph(sup["company_registration"], muted))

    # Stamp / signature image
    stamp_img = None
    try:
        from pathlib import Path as _Path
        stamp_file = _Path(__file__).parent / "data" / "stamp.png"
        if stamp_file.exists():
            stamp_img = Image(str(stamp_file), width=44*mm, height=44*mm, kind="proportional")
    except Exception:
        pass

    if stamp_img:
        elems.append(Spacer(1, 8*mm))
        stamp_row = Table([[Spacer(1,1), stamp_img]], colWidths=[136*mm, 44*mm])
        stamp_row.setStyle(TableStyle([("ALIGN", (1,0), (1,0), "RIGHT"),
                                       ("VALIGN", (0,0), (-1,-1), "BOTTOM")]))
        elems.append(stamp_row)

    doc.build(elems)
    return buf.getvalue()
