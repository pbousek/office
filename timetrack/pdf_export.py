"""Monthly PDF report generation using reportlab."""
from datetime import datetime
from io import BytesIO
from collections import defaultdict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register DejaVu fonts for full Czech diacritics support (Helvetica's
# built-in encoding mangles capital letters with diacritics like Č, Š, Ř).
pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily(
    "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
    italic="DejaVuSans", boldItalic="DejaVuSans-Bold",
)

MONTH_NAMES_CZ = [
    "", "leden", "únor", "březen", "duben", "květen", "červen",
    "červenec", "srpen", "září", "říjen", "listopad", "prosinec"
]


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _duration_hours(start: str, end: str) -> float:
    delta = _parse_dt(end) - _parse_dt(start)
    return delta.total_seconds() / 3600


def _fmt_hours(hours: float) -> str:
    h = int(hours)
    m = round((hours - h) * 60)
    if m == 60:
        h += 1
        m = 0
    return f"{h}:{m:02d}"


def generate_monthly_pdf(entries: list[dict], year: int, month: int) -> bytes:
    """Build a monthly summary PDF grouped by customer, with a detail table.

    Returns raw PDF bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=15 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCustom", parent=styles["Title"], fontName="DejaVuSans-Bold",
        fontSize=16, spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "SubCustom", parent=styles["Normal"], fontName="DejaVuSans",
        fontSize=10, textColor=colors.grey, spaceAfter=14,
    )
    h2_style = ParagraphStyle(
        "H2Custom", parent=styles["Heading2"], fontName="DejaVuSans-Bold",
        fontSize=12, spaceBefore=14, spaceAfter=6,
    )
    total_style = ParagraphStyle(
        "TotalCustom", parent=styles["Normal"], fontName="DejaVuSans",
        fontSize=11, alignment=TA_RIGHT, spaceBefore=4,
    )

    month_label = f"{MONTH_NAMES_CZ[month]} {year}"
    elements = [
        Paragraph("Výkaz odpracovaného času", title_style),
        Paragraph(month_label, sub_style),
    ]

    # Group entries by customer
    by_customer = defaultdict(list)
    for e in entries:
        by_customer[e["customer"]].append(e)

    grand_total = 0.0

    for customer in sorted(by_customer.keys(), key=str.lower):
        rows = by_customer[customer]
        rows.sort(key=lambda r: r["start_time"])

        table_data = [["Datum", "Od", "Do", "Trvání", "Činnost"]]
        customer_total = 0.0
        for r in rows:
            start_dt = _parse_dt(r["start_time"])
            end_dt = _parse_dt(r["end_time"])
            hours = _duration_hours(r["start_time"], r["end_time"])
            customer_total += hours
            table_data.append([
                start_dt.strftime("%d.%m.%Y"),
                start_dt.strftime("%H:%M"),
                end_dt.strftime("%H:%M"),
                _fmt_hours(hours),
                r["activity"] + (f" — {r['note']}" if r.get("note") else ""),
            ])

        grand_total += customer_total

        elements.append(Paragraph(customer, h2_style))
        t = Table(table_data, colWidths=[22*mm, 16*mm, 16*mm, 18*mm, 88*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3142")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f6")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ]))
        elements.append(t)
        elements.append(Paragraph(
            f"Mezisoučet za {customer}: <b>{_fmt_hours(customer_total)}</b> h",
            total_style,
        ))
        elements.append(Spacer(1, 4 * mm))

    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(
        f"Celkem za {month_label}: <b>{_fmt_hours(grand_total)}</b> h",
        ParagraphStyle("GrandTotal", parent=styles["Normal"], fontName="DejaVuSans",
                        fontSize=13, alignment=TA_RIGHT, textColor=colors.HexColor("#2d3142")),
    ))

    if not entries:
        elements.append(Paragraph("Žádné záznamy za toto období.",
            ParagraphStyle("Empty", parent=styles["Normal"], fontName="DejaVuSans")))

    doc.build(elements)
    return buf.getvalue()
