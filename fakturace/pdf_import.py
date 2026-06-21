"""Parser pro import faktur z PDF."""
import re
from pathlib import Path


def _parse_date(s: str) -> str:
    """'2. 6. 2026' → '2026-06-02'"""
    m = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _parse_amount(s: str) -> float:
    """'9 100,00 Kč' → 9100.0"""
    s = re.sub(r'[^\d,]', '', s)
    s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_invoice_pdf(path: str) -> dict:
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber není nainstalován: pip install pdfplumber")

    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    result = {
        "number": "",
        "issue_date": "",
        "due_date": "",
        "variable_symbol": "",
        "total": 0.0,
        "vat_payer": False,
        "supplier_ico": "",
        "customer_ico": "",
        "customer_dic": "",
        "customer_name": "",
        "items": [],
        "raw_text": text,
        "warnings": [],
    }

    # Číslo faktury
    m = re.search(r'Faktura\s+([^\n\r]+)', text)
    if m:
        result["number"] = m.group(1).strip()

    # Variabilní symbol
    m = re.search(r'Variabilní symbol[:\s]+(\S+)', text)
    if m:
        result["variable_symbol"] = m.group(1).strip()
        if not result["number"]:
            result["number"] = result["variable_symbol"]

    # Datum vystavení
    m = re.search(r'Datum vystavení[:\s]+(.+)', text)
    if m:
        result["issue_date"] = _parse_date(m.group(1))

    # Datum splatnosti
    m = re.search(r'Datum splatnosti[:\s]+(.+)', text)
    if m:
        result["due_date"] = _parse_date(m.group(1))

    # IČO — sbíráme všechna (první = dodavatel, druhé = odběratel)
    icos = re.findall(r'IČO[:\s]+(\d{6,8})', text)
    if len(icos) >= 1:
        result["supplier_ico"] = icos[0]
    if len(icos) >= 2:
        result["customer_ico"] = icos[1]
    elif len(icos) == 1:
        result["warnings"].append("Nalezeno jen jedno IČO — nelze rozlišit dodavatele a odběratele.")

    # DIČ odběratele (první DIČ za odběratelem)
    m = re.search(r'IČO[:\s]+\d+.*?DIČ[:\s]+(CZ\d+)', text, re.DOTALL)
    if m:
        result["customer_dic"] = m.group(1).strip()

    # Jméno zákazníka — hledáme řádek za "Odběratel:"
    m = re.search(r'Odběratel:\s*\n.*?(\b[A-ZÁÉÍÓÚŮÝČĎĚŇŘŠŤŽ][^\n]{3,})', text)
    if m:
        name = m.group(1).strip()
        # Vyhodit telefonní čísla a emaily z řádku
        name = re.sub(r'Tel\.?:.*', '', name).strip()
        name = re.sub(r'E-mail:.*', '', name).strip()
        if name:
            result["customer_name"] = name

    # Plátce DPH
    if re.search(r'[Nn]ejsme?\s+plátc', text) or re.search(r'[Nn]eplátce?\s+DPH', text):
        result["vat_payer"] = False
    elif re.search(r'[Pp]látce?\s+DPH', text):
        result["vat_payer"] = True

    # Celková částka
    m = re.search(r'Celkem k úhradě[:\s]+([\d\s]+,\d{2})', text)
    if m:
        result["total"] = _parse_amount(m.group(1))

    # Položky — hledáme řádky mezi záhlavím tabulky a "Celkem"
    items = _parse_items(text)
    result["items"] = items

    if not result["issue_date"]:
        result["warnings"].append("Nepodařilo se najít datum vystavení.")
    if not result["customer_ico"] and not result["customer_name"]:
        result["warnings"].append("Nepodařilo se identifikovat zákazníka.")

    return result


def _parse_items(text: str) -> list[dict]:
    """Hledá řádky položek — popis + množství + jednotka + cena/j + celkem."""
    items = []

    # Vzor: text, číslo, jednotka, cena Kč, celkem Kč
    # Např.: "správa serverů 14,00 ks 650,00 Kč 9 100,00 Kč"
    pattern = re.compile(
        r'^(.+?)\s+'                          # popis
        r'(\d[\d\s]*,\d{2})\s+'              # množství
        r'([a-zA-Záčďéěíňóřšťúůýž]{1,8})\s+' # jednotka
        r'([\d\s]+,\d{2})\s+Kč\s+'           # cena/j
        r'([\d\s]+,\d{2})\s+Kč',             # celkem
        re.MULTILINE
    )

    for m in pattern.finditer(text):
        desc = m.group(1).strip()
        # Přeskočit řádky které jsou záhlaví
        if any(w in desc.lower() for w in ['označení', 'popis', 'položka', 'název']):
            continue
        items.append({
            "description": desc,
            "quantity": _parse_amount(m.group(2)),
            "unit": m.group(3).strip(),
            "unit_price": _parse_amount(m.group(4)),
            "vat_rate": 0,
        })

    # Fallback: pokud pattern nic nenašel, zkus jednodušší přístup
    if not items:
        lines = text.splitlines()
        in_table = False
        for line in lines:
            if re.search(r'(Označení|Popis|MJ|Cena/MJ)', line):
                in_table = True
                continue
            if in_table and re.search(r'(Celkem|Součet|DPH)', line):
                break
            if in_table and line.strip():
                # Vytáhni alespoň popis a celkovou částku
                amt = re.search(r'([\d\s]+,\d{2})\s*Kč\s*$', line)
                if amt:
                    desc = line[:amt.start()].strip()
                    if desc:
                        items.append({
                            "description": desc,
                            "quantity": 1.0,
                            "unit": "ks",
                            "unit_price": _parse_amount(amt.group(1)),
                            "vat_rate": 0,
                        })

    return items
