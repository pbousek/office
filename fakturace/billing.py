"""Tariff calculation and billing report generation."""
import json
import math
import sys
from pathlib import Path
from datetime import date

_TT_DIR = Path(__file__).parent.parent / "timetrack"


def apply_tariff(total_hours: float, tariff: dict, year: int, month: int, vat_payer: bool) -> list[dict]:
    """Return invoice items list from hours + tariff config."""
    items = []
    month_label = f"{month}/{year}"
    included = float(tariff.get("included_hours", 0))

    # Fixed monthly items (retainer, SLA...)
    for fi in tariff.get("fixed_items", []):
        if not fi.get("description"):
            continue
        items.append({
            "description": f"{fi['description']} {month_label}",
            "quantity": 1.0,
            "unit": fi.get("unit", "měs"),
            "unit_price": float(fi.get("price", 0)),
            "vat_rate": int(fi.get("vat_rate", 21)) if vat_payer else 0,
        })

    # Tiered hourly billing
    tiers = tariff.get("tiers", [])
    consumed = 0.0
    remaining = total_hours

    for tier in tiers:
        if remaining <= 0.001:
            break
        up_to = tier.get("up_to")  # None = unlimited
        tier_hours = min(remaining, float(up_to) - consumed) if up_to is not None else remaining
        # Hours covered by included_hours are not billed
        billable = max(0.0, tier_hours - max(0.0, included - consumed))
        # Any part of a started hour above the included package is billed as a full hour
        billable = math.ceil(billable - 1e-9) if billable > 1e-9 else 0.0
        if billable > 0.001:
            items.append({
                "description": f"{tier['description']} {month_label}",
                "quantity": billable,
                "unit": "hod",
                "unit_price": float(tier.get("rate", 0)),
                "vat_rate": int(tier.get("vat_rate", 21)) if vat_payer else 0,
            })
        consumed += tier_hours
        remaining -= tier_hours

    return items


def generate_billing_report(entries: list[dict], year: int, month: int) -> bytes:
    """Timetracker-style PDF report for a single customer (billing attachment)."""
    tt_dir = str(_TT_DIR)
    inserted = False
    if tt_dir not in sys.path:
        sys.path.append(tt_dir)
        inserted = True
    try:
        import importlib
        tt_pdf = importlib.import_module("pdf_export") if "pdf_export" not in sys.modules else None
        # Load timetrack's pdf_export from its path directly
        import importlib.util
        spec = importlib.util.spec_from_file_location("tt_pdf_export", _TT_DIR / "pdf_export.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.generate_monthly_pdf(entries, year, month)
    except Exception as e:
        raise RuntimeError(f"Timetracker PDF export není dostupný: {e}")
    finally:
        if inserted and tt_dir in sys.path:
            sys.path.remove(tt_dir)


def get_tariff(customer: dict) -> dict:
    raw = customer.get("tariff") or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {}


def save_tariff(tariff_dict: dict) -> str:
    return json.dumps(tariff_dict, ensure_ascii=False)


def parse_tariff_form(form) -> dict:
    """Parse tariff form POST data into tariff dict."""
    fixed_items = []
    fi_count = int(form.get("fi_count", "0") or 0)
    for i in range(fi_count):
        desc = form.get(f"fi_desc_{i}", "").strip()
        if not desc:
            continue
        price = form.get(f"fi_price_{i}", "0").strip()
        unit = form.get(f"fi_unit_{i}", "měs").strip()
        vat = form.get(f"fi_vat_{i}", "21").strip()
        fixed_items.append({
            "description": desc,
            "price": float(price or 0),
            "unit": unit or "měs",
            "vat_rate": int(vat or 21),
        })

    tiers = []
    tier_count = int(form.get("tier_count", "0") or 0)
    for i in range(tier_count):
        desc = form.get(f"tier_desc_{i}", "").strip()
        if not desc:
            continue
        rate = form.get(f"tier_rate_{i}", "0").strip()
        up_to = form.get(f"tier_up_to_{i}", "").strip()
        vat = form.get(f"tier_vat_{i}", "21").strip()
        tiers.append({
            "description": desc,
            "rate": float(rate or 0),
            "up_to": float(up_to) if up_to else None,
            "vat_rate": int(vat or 21),
        })

    return {
        "timetrack_customer": form.get("timetrack_customer", "").strip(),
        "included_hours": float(form.get("included_hours", "0") or 0),
        "fixed_items": fixed_items,
        "tiers": tiers,
    }
