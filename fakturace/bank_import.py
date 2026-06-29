"""Parser výpisů Air Bank CSV."""
import csv
import io
from datetime import datetime


def _parse_amount(s: str) -> float:
    return float(s.strip().replace(" ", "").replace(",", ".") or 0)


def _parse_date(s: str) -> str:
    """DD/MM/YYYY → YYYY-MM-DD"""
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def parse_airbank_csv(path: str) -> list[dict]:
    """Vrátí seznam příchozích zaúčtovaných transakcí z Air Bank CSV."""
    for enc in ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2"):
        try:
            raw = open(path, encoding=enc).read()
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("Nepodařilo se přečíst soubor (neznámé kódování).")

    reader = csv.DictReader(io.StringIO(raw), delimiter=";")
    rows = list(reader)
    if not rows:
        return []

    # Normalizujeme názvy sloupců (strip whitespace)
    def get(row, *keys):
        for k in keys:
            for rk in row:
                if rk.strip() == k:
                    return row[rk].strip()
        return ""

    transactions = []
    for row in rows:
        direction = get(row, "Směr úhrady")
        posted = get(row, "Zaúčtováno")

        # Bereme jen příchozí zaúčtované platby
        if direction != "Příchozí":
            continue
        if posted and posted != "Ano":
            continue

        amount_str = get(row, "Částka v měně účtu", "Původní částka úhrady")
        amount = _parse_amount(amount_str)
        if amount <= 0:
            continue

        date_str = get(row, "Datum zaúčtování", "Datum provedení")
        vs = get(row, "Variabilní symbol")
        transactions.append({
            "date": _parse_date(date_str),
            "amount": amount,
            "currency": get(row, "Měna účtu") or "CZK",
            "vs": vs,
            "counterparty": get(row, "Název protistrany"),
            "account": get(row, "Číslo účtu protistrany"),
            "note": get(row, "Zpráva pro příjemce", "Poznámka k úhradě", "Poznámka pro mne"),
            "ref": get(row, "Referenční číslo"),
        })

    return transactions


def match_transactions(transactions: list[dict], invoices: list[dict]) -> list[dict]:
    """Páruje transakce s fakturami podle VS. Vrátí seznam s přiřazenými fakturami."""
    inv_by_vs = {}
    for inv in invoices:
        if inv["status"] in ("paid", "cancelled"):
            continue
        num = str(inv["number"]).strip()
        inv_by_vs[num] = inv

    results = []
    for tx in transactions:
        match = inv_by_vs.get(tx["vs"]) if tx["vs"] else None
        results.append({**tx, "match": match, "match_type": "vs" if match else None})

    return results
