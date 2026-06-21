"""ISDOC 6.0.2 export — český formát pro elektronické faktury."""
import re
import uuid
import zipfile
from io import BytesIO
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

NS = "http://isdoc.cz/namespace/2013"
VERSION = "6.0.2"

UNIT_CODES = {
    "hod": "HUR", "h": "HUR",
    "ks": "C62", "kus": "C62",
    "den": "DAY", "dny": "DAY",
    "měs": "MON", "měsíc": "MON",
}


def _e(parent, tag, text=None, **attrs):
    el = SubElement(parent, f"{{{NS}}}{tag}", **attrs)
    if text is not None:
        el.text = str(text)
    return el


def _m(v: float) -> str:
    return f"{v:.2f}"


def _split_street(address: str) -> tuple[str, str]:
    """'Thámova 137/16' → ('Thámova', '137/16'). Fallback: (address, '')."""
    m = re.search(r'\s+(\d[\d/a-zA-Z]*)$', address.strip())
    if m:
        return address[:m.start()].strip(), m.group(1)
    return address.strip(), ""


def _split_account(account: str) -> tuple[str, str]:
    """'2377096048/3030' → ('2377096048', '3030'). Fallback: (account, '')."""
    if "/" in account:
        parts = account.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    return account.strip(), ""


def _build_address(party_el, street, city, zip_):
    addr = _e(party_el, "PostalAddress")
    street_name, building_number = _split_street(street or "")
    _e(addr, "StreetName", street_name)
    _e(addr, "BuildingNumber", building_number)
    _e(addr, "CityName", city or "")
    _e(addr, "PostalZone", str(zip_ or "").replace(" ", ""))
    country_el = _e(addr, "Country")
    _e(country_el, "IdentificationCode", "CZ")
    _e(country_el, "Name", "Česká republika")  # CountryType requires both elements


def generate_isdoc(invoice: dict, items: list[dict], totals: dict, settings: dict) -> bytes:
    vat = bool(invoice["vat_payer"])
    currency = invoice.get("currency", "CZK")
    duzp = invoice.get("duzp") or invoice["issue_date"]
    vs = invoice["number"].replace("/", "").replace("-", "")

    root = Element(f"{{{NS}}}Invoice")
    root.set("version", VERSION)
    root.set("xmlns", NS)

    # === Hlavička ===
    _e(root, "DocumentType", "1")
    _e(root, "SubDocumentType", "0")
    _e(root, "SubDocumentTypeOrigin", "0")
    _e(root, "ID", invoice["number"])
    _e(root, "UUID", str(uuid.uuid4()))
    _e(root, "IssuingSystem", "Fakturace")
    _e(root, "IssueDate", invoice["issue_date"])
    _e(root, "TaxPointDate", duzp)
    _e(root, "VATApplicable", "true" if vat else "false")
    _e(root, "ElectronicPossibilityAgreementReference", "")
    _e(root, "LocalCurrencyCode", currency)
    _e(root, "CurrRate", "1")
    _e(root, "RefCurrRate", "1")

    # === Dodavatel ===
    sup_party = _e(_e(root, "AccountingSupplierParty"), "Party")
    if settings.get("company_ico"):
        _e(_e(sup_party, "PartyIdentification"), "ID", settings["company_ico"])
    _e(_e(sup_party, "PartyName"), "Name", settings.get("company_name", ""))
    _build_address(sup_party,
        street=settings.get("company_street", ""),
        city=settings.get("company_city", ""),
        zip_=settings.get("company_zip", ""),
    )
    if settings.get("company_dic"):
        pts = _e(sup_party, "PartyTaxScheme")
        _e(pts, "CompanyID", settings["company_dic"])
        _e(pts, "TaxScheme", "VAT")  # simpleType — plain text, no child elements

    # === Odběratel ===
    cust_party = _e(_e(root, "AccountingCustomerParty"), "Party")
    if invoice.get("customer_ico"):
        _e(_e(cust_party, "PartyIdentification"), "ID", invoice["customer_ico"])
    _e(_e(cust_party, "PartyName"), "Name", invoice["customer_name"])
    _build_address(cust_party,
        street=invoice.get("customer_street", ""),
        city=invoice.get("customer_city", ""),
        zip_=invoice.get("customer_zip", ""),
    )
    if invoice.get("customer_dic"):
        cpts = _e(cust_party, "PartyTaxScheme")
        _e(cpts, "CompanyID", invoice["customer_dic"])
        _e(cpts, "TaxScheme", "VAT")

    # === Položky ===
    invoice_lines = _e(root, "InvoiceLines")
    for i, it in enumerate(items, 1):
        base = round(it["quantity"] * it["unit_price"], 2)
        vat_rate = it["vat_rate"] if vat else 0
        vat_amount = round(base * vat_rate / 100, 2)
        unit_code = UNIT_CODES.get(it["unit"].lower(), "ZZ")

        line = _e(invoice_lines, "InvoiceLine")
        _e(line, "ID", str(i))
        _e(line, "InvoicedQuantity", f"{it['quantity']:g}", unitCode=unit_code)
        # AmountType = xs:decimal, no currencyID attribute
        _e(line, "LineExtensionAmount", _m(base))
        _e(line, "LineExtensionAmountTaxInclusive", _m(base + vat_amount))
        _e(line, "LineExtensionTaxAmount", _m(vat_amount))
        _e(line, "UnitPrice", _m(it["unit_price"]))
        _e(line, "UnitPriceTaxInclusive", _m(it["unit_price"] * (1 + vat_rate / 100)))
        clf = _e(line, "ClassifiedTaxCategory")
        _e(clf, "Percent", str(vat_rate))
        _e(clf, "VATCalculationMethod", "0")
        # ClassifiedTaxCategory has no TaxScheme child per XSD
        item_el = _e(line, "Item")
        _e(item_el, "Description", it["description"])
        _e(_e(item_el, "SellersItemIdentification"), "ID", str(i))

    def _tax_subtotal(parent, base, vat_amt, rate_str):
        # TaxSubTotalType requires all 8 amount fields + TaxCategory (in that order)
        inclusive = _m(base + vat_amt)
        sub = _e(parent, "TaxSubTotal")
        _e(sub, "TaxableAmount", _m(base))
        _e(sub, "TaxAmount", _m(vat_amt))
        _e(sub, "TaxInclusiveAmount", inclusive)
        _e(sub, "AlreadyClaimedTaxableAmount", "0.00")
        _e(sub, "AlreadyClaimedTaxAmount", "0.00")
        _e(sub, "AlreadyClaimedTaxInclusiveAmount", "0.00")
        _e(sub, "DifferenceTaxableAmount", _m(base))
        _e(sub, "DifferenceTaxAmount", _m(vat_amt))
        _e(sub, "DifferenceTaxInclusiveAmount", inclusive)
        # TaxCategoryType: only Percent (+ optional TaxScheme) — no VATCalculationMethod
        cat = _e(sub, "TaxCategory")
        _e(cat, "Percent", rate_str)
        _e(cat, "TaxScheme", "VAT")

    # === DPH ===
    tax_total = _e(root, "TaxTotal")
    if vat and totals["vat_lines"]:
        for vl in totals["vat_lines"]:
            _tax_subtotal(tax_total, vl["base"], vl["vat"], str(vl["rate"]))
    else:
        _tax_subtotal(tax_total, totals["subtotal"], 0.0, "0")
    _e(tax_total, "TaxAmount", _m(totals["vat"]))

    # === LegalMonetaryTotal ===
    lmt = _e(root, "LegalMonetaryTotal")
    _e(lmt, "TaxExclusiveAmount", _m(totals["subtotal"]))
    _e(lmt, "TaxInclusiveAmount", _m(totals["total"]))
    _e(lmt, "AlreadyClaimedTaxExclusiveAmount", "0.00")
    _e(lmt, "AlreadyClaimedTaxInclusiveAmount", "0.00")
    _e(lmt, "DifferenceTaxExclusiveAmount", _m(totals["subtotal"]))
    _e(lmt, "DifferenceTaxInclusiveAmount", _m(totals["total"]))
    _e(lmt, "PayableRoundingAmount", "0.00")
    _e(lmt, "PaidDepositsAmount", "0.00")  # required in LegalMonetaryTotalType
    _e(lmt, "PayableAmount", _m(totals["total"]))

    # === Způsob úhrady (po LegalMonetaryTotal) ===
    payment = _e(_e(root, "PaymentMeans"), "Payment")
    _e(payment, "PaidAmount", _m(totals["total"]))  # not PaidDepositsAmount
    _e(payment, "PaymentMeansCode", "42" if currency == "CZK" else "31")
    account_raw = settings.get("company_account", "")
    iban = settings.get("company_iban", "")
    bic = settings.get("company_swift", "")
    if iban or account_raw:
        det = _e(payment, "Details")
        _e(det, "PaymentDueDate", invoice["due_date"])
        acct_num, bank_code = _split_account(account_raw)
        _e(det, "ID", acct_num)      # číslo účtu bez kódu banky
        _e(det, "BankCode", bank_code)
        _e(det, "Name", settings.get("company_bank", ""))
        _e(det, "IBAN", iban)
        _e(det, "BIC", bic)
        _e(det, "VariableSymbol", vs)
        if currency == "CZK":
            _e(det, "ConstantSymbol", "0308")

    raw = tostring(root, encoding="unicode")
    pretty = parseString(f'<?xml version="1.0" encoding="UTF-8"?>{raw}').toprettyxml(
        indent="  ", encoding="UTF-8"
    )
    return pretty


def generate_isdocx(invoice: dict, items: list[dict], totals: dict, settings: dict, pdf_bytes: bytes = None) -> bytes:
    """ISDOCX = ZIP s ISDOC XML a volitelně PDF."""
    xml_bytes = generate_isdoc(invoice, items, totals, settings)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{invoice['number']}.isdoc", xml_bytes)
        if pdf_bytes:
            zf.writestr(f"{invoice['number']}.pdf", pdf_bytes)
    return buf.getvalue()
