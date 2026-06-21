"""Fakturace — jednoduchá lokální fakturace.

Spuštění:  python app.py
Otevři:    http://localhost:8732
"""
import calendar as _calendar
from datetime import date, timedelta
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from typing import Annotated

import db
import ares as ares_mod
import timetrack as tt
import billing as bil
from pdf_export import generate_invoice_pdf
from isdoc_export import generate_isdocx
from pdf_import import parse_invoice_pdf
from email_utils import send_invoice_email

app = FastAPI(title="Fakturace")
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
jinja_env.filters["money"] = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")
jinja_env.filters["enumerate"] = enumerate

def _fmt_hours(h: float) -> str:
    h = float(h or 0)
    hours = int(h)
    minutes = round((h - hours) * 60)
    if minutes == 60:
        hours += 1
        minutes = 0
    return f"{hours}:{minutes:02d}"

jinja_env.filters["fmt_hours"] = _fmt_hours

import json as _json
jinja_env.filters["fromjson"] = lambda s: _json.loads(s or "{}")

db.init_db()


def render(template_name: str, **ctx) -> HTMLResponse:
    t = jinja_env.get_template(template_name)
    return HTMLResponse(t.render(**ctx))


@app.get("/static/style.css")
def style_css():
    return FileResponse(STATIC_DIR / "style.css", media_type="text/css")


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", sort: str = "issue_date", dir: str = "desc"):
    invoices = db.list_invoices()
    today = date.today()

    # Filter
    if q:
        ql = q.lower()
        invoices = [inv for inv in invoices if ql in inv["customer_name"].lower() or ql in inv["number"].lower()]

    # Sort
    allowed = {"number", "issue_date", "due_date", "customer_name", "total", "status"}
    if sort not in allowed:
        sort = "issue_date"
    reverse = (dir != "asc")
    invoices = sorted(invoices, key=lambda inv: (inv.get(sort) or ""), reverse=reverse)

    stats = _build_stats(db.list_invoices())
    return render("index.html", invoices=invoices, today=today.isoformat(), stats=stats,
                  q=q, sort=sort, dir=dir)


def _build_stats(invoices: list[dict]) -> dict:
    today = date.today()
    year = today.year
    month = today.month

    def total(inv):
        items = db.get_items(inv["id"])
        return db.calc_totals(items, bool(inv["vat_payer"]))["total"]

    by_month, by_year, paid_year, paid_month, overdue = 0.0, 0.0, 0.0, 0.0, 0.0
    for inv in invoices:
        t = total(inv)
        iy = inv["issue_date"][:4]
        im = inv["issue_date"][5:7]
        if iy == str(year):
            by_year += t
            if im == f"{month:02d}":
                by_month += t
            if inv["status"] == "paid":
                paid_year += t
                if im == f"{month:02d}":
                    paid_month += t
            elif inv["status"] != "cancelled" and inv["due_date"] < today.isoformat():
                overdue += t

    return {
        "by_month": by_month,
        "by_year": by_year,
        "paid_year": paid_year,
        "paid_month": paid_month,
        "overdue": overdue,
        "year": year,
        "month": month,
    }


# ---------- Invoices ----------

@app.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(request: Request):
    settings = db.get_settings()
    today = date.today()
    due = today + timedelta(days=int(settings.get("due_days", 14)))
    number = db.next_invoice_number(settings)
    customers = db.list_customers()
    return render("invoice_form.html",
        invoice=None,
        items=[],
        customers=customers,
        number=number,
        issue_date=today.isoformat(),
        due_date=due.isoformat(),
        vat_payer=settings.get("vat_payer", "0") == "1",
        tt_available=tt.is_available(),
        tt_customers=tt.list_customers(),
        customer_tariffs={c["id"]: bil.get_tariff(c) for c in customers},
    )


@app.post("/invoices/add")
async def add_invoice(request: Request):
    form = await request.form()
    customer = db.get_customer(int(form["customer_id"]))
    inv_id = db.add_invoice(
        number=form["number"],
        customer_id=int(form["customer_id"]),
        issue_date=form["issue_date"],
        due_date=form["due_date"],
        vat_payer=form.get("vat_payer") == "1",
        currency=form.get("currency", "CZK"),
        note=form.get("note", ""),
        duzp=form.get("duzp", ""),
        snap=customer,
    )
    db.replace_items(inv_id, _parse_items(form))
    return RedirectResponse(url=f"/invoices/{inv_id}", status_code=303)


@app.get("/invoices/{inv_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, inv_id: int, sent: int = 0):
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    items = db.get_items(inv_id)
    totals = db.calc_totals(items, bool(invoice["vat_payer"]))
    settings = db.get_settings()
    return render("invoice_detail.html", invoice=invoice, items=items, totals=totals,
                  settings=settings, sent=sent)


@app.get("/invoices/{inv_id}/edit", response_class=HTMLResponse)
def edit_invoice_form(request: Request, inv_id: int):
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    items = db.get_items(inv_id)
    customers = db.list_customers()
    return render("invoice_form.html",
        invoice=invoice,
        items=items,
        customers=customers,
        number=invoice["number"],
        issue_date=invoice["issue_date"],
        due_date=invoice["due_date"],
        vat_payer=bool(invoice["vat_payer"]),
        tt_available=tt.is_available(),
        tt_customers=tt.list_customers(),
        customer_tariffs={c["id"]: bil.get_tariff(c) for c in customers},
    )


@app.post("/invoices/{inv_id}/edit")
async def edit_invoice_submit(inv_id: int, request: Request):
    form = await request.form()
    db.update_invoice(
        invoice_id=inv_id,
        customer_id=int(form["customer_id"]),
        issue_date=form["issue_date"],
        due_date=form["due_date"],
        vat_payer=form.get("vat_payer") == "1",
        currency=form.get("currency", "CZK"),
        note=form.get("note", ""),
        duzp=form.get("duzp", ""),
    )
    db.replace_items(inv_id, _parse_items(form))
    return RedirectResponse(url=f"/invoices/{inv_id}", status_code=303)


@app.post("/invoices/{inv_id}/status")
def set_status(inv_id: int, status: str = Form(...)):
    db.update_invoice_status(inv_id, status)
    return RedirectResponse(url=f"/invoices/{inv_id}", status_code=303)


@app.post("/invoices/{inv_id}/delete")
def delete_invoice(inv_id: int):
    db.delete_invoice(inv_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/invoices/{inv_id}/clone")
def clone_invoice(inv_id: int):
    settings = db.get_settings()
    new_id = db.clone_invoice(inv_id, settings)
    return RedirectResponse(url=f"/invoices/{new_id}", status_code=303)


@app.get("/invoices/{inv_id}/send", response_class=HTMLResponse)
def send_invoice_form(inv_id: int, request: Request):
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    settings = db.get_settings()
    items = db.get_items(inv_id)
    totals = db.calc_totals(items, bool(invoice["vat_payer"]))
    default_body = (
        f"Dobrý den,\n\n"
        f"v příloze zasílám fakturu č. {invoice['number']} "
        f"na částku {totals['total']:,.2f} {invoice['currency']} "
        f"se splatností {invoice['due_date']}.\n\n"
        f"S pozdravem"
    ).replace(",", " ")
    return render("invoice_send.html", invoice=invoice, settings=settings,
                  to=invoice.get("customer_email", ""),
                  subject=f"Faktura {invoice['number']}",
                  body=default_body, error=None)


@app.post("/invoices/{inv_id}/send")
async def send_invoice_submit(inv_id: int, request: Request):
    form = await request.form()
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    settings = db.get_settings()
    items = db.get_items(inv_id)
    totals = db.calc_totals(items, bool(invoice["vat_payer"]))
    pdf_bytes = generate_invoice_pdf(invoice, items, totals, settings)
    try:
        send_invoice_email(
            to=form["to"],
            subject=form["subject"],
            body=form["body"],
            pdf_bytes=pdf_bytes,
            filename=f"faktura_{invoice['number']}.pdf",
            settings=settings,
        )
        db.update_invoice_status(inv_id, "sent")
        return RedirectResponse(url=f"/invoices/{inv_id}?sent=1", status_code=303)
    except Exception as e:
        return render("invoice_send.html", invoice=invoice, settings=settings,
                      to=form.get("to", ""), subject=form.get("subject", ""),
                      body=form.get("body", ""), error=str(e))


@app.get("/invoices/{inv_id}/isdocx")
def invoice_isdocx(inv_id: int):
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    items = db.get_items(inv_id)
    totals = db.calc_totals(items, bool(invoice["vat_payer"]))
    settings = db.get_settings()
    pdf_bytes = generate_invoice_pdf(invoice, items, totals, settings)
    isdocx_bytes = generate_isdocx(invoice, items, totals, settings, pdf_bytes)
    filename = f"faktura_{invoice['number']}.isdocx"
    return Response(content=isdocx_bytes, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/invoices/{inv_id}/pdf")
def invoice_pdf(inv_id: int):
    invoice = db.get_invoice(inv_id)
    if not invoice:
        return RedirectResponse(url="/")
    items = db.get_items(inv_id)
    totals = db.calc_totals(items, bool(invoice["vat_payer"]))
    settings = db.get_settings()
    pdf_bytes = generate_invoice_pdf(invoice, items, totals, settings)
    filename = f"faktura_{invoice['number']}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _parse_items(form) -> list[dict]:
    items = []
    i = 0
    while f"desc_{i}" in form:
        desc = form.get(f"desc_{i}", "").strip()
        if desc:
            items.append({
                "description": desc,
                "quantity": float(form.get(f"qty_{i}", 1) or 1),
                "unit": form.get(f"unit_{i}", "hod"),
                "unit_price": float(form.get(f"price_{i}", 0) or 0),
                "vat_rate": int(form.get(f"vat_{i}", 0) or 0),
            })
        i += 1
    return items


# ---------- Customers ----------

@app.get("/customers", response_class=HTMLResponse)
def customers_list(request: Request):
    return render("customers.html", customers=db.list_customers())


@app.get("/customers/json")
def customers_json():
    rows = db.list_customers()
    return JSONResponse([{"id": r["id"], "name": r["name"], "ico": r["ico"]} for r in rows])


@app.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(request: Request):
    return render("customer_form.html", customer=None)


@app.post("/customers/add")
def add_customer(
    name: str = Form(...), ico: str = Form(""), dic: str = Form(""),
    street: str = Form(""), city: str = Form(""), zip_: str = Form("", alias="zip"),
    email: str = Form(""), note: str = Form(""),
):
    db.add_customer(name, ico, dic, street, city, zip_, email, note)
    return RedirectResponse(url="/customers", status_code=303)


@app.get("/customers/{cust_id}/edit", response_class=HTMLResponse)
def edit_customer_form(request: Request, cust_id: int):
    customer = db.get_customer(cust_id)
    if not customer:
        return RedirectResponse(url="/customers")
    return render("customer_form.html", customer=customer)


@app.post("/customers/{cust_id}/edit")
def edit_customer_submit(
    cust_id: int,
    name: str = Form(...), ico: str = Form(""), dic: str = Form(""),
    street: str = Form(""), city: str = Form(""), zip_: str = Form("", alias="zip"),
    email: str = Form(""), note: str = Form(""),
):
    db.update_customer(cust_id, name, ico, dic, street, city, zip_, email, note)
    return RedirectResponse(url="/customers", status_code=303)


@app.post("/customers/{cust_id}/delete")
def delete_customer(cust_id: int):
    db.delete_customer(cust_id)
    return RedirectResponse(url="/customers", status_code=303)


# ---------- Tariffs & Billing ----------

_MONTH_NAMES = ["", "leden", "únor", "březen", "duben", "květen", "červen",
                "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]


@app.get("/customers/{cust_id}/tariff", response_class=HTMLResponse)
def tariff_form(request: Request, cust_id: int):
    customer = db.get_customer(cust_id)
    if not customer:
        return RedirectResponse(url="/customers")
    tariff = bil.get_tariff(customer)
    tt_customers = tt.list_customers()
    return render("tariff_form.html", customer=customer, tariff=tariff, tt_customers=tt_customers)


@app.post("/customers/{cust_id}/tariff")
async def tariff_save(cust_id: int, request: Request):
    form = await request.form()
    tariff = bil.parse_tariff_form(form)
    db.save_customer_tariff(cust_id, bil.save_tariff(tariff))
    return RedirectResponse(url="/customers", status_code=303)


@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, customer_id: int = 0):
    today = date.today()
    customers = db.list_customers()
    years = list(range(today.year, today.year - 3, -1))
    months = [(i, _MONTH_NAMES[i]) for i in range(1, 13)]
    return render("billing.html", customers=customers, years=years, months=months,
                  today=today, selected_customer_id=customer_id)


@app.get("/billing/preview", response_class=HTMLResponse)
def billing_preview(request: Request, customer_id: int, year: int, month: int):
    customer = db.get_customer(customer_id)
    if not customer:
        return RedirectResponse(url="/billing")
    tariff = bil.get_tariff(customer)

    tt_customer = tariff.get("timetrack_customer") or customer["name"]
    date_from = f"{year}-{month:02d}-01"
    date_to = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    entries = tt.list_entries(customer=tt_customer, date_from=date_from, date_to=date_to)
    total_hours = sum(e.get("hours", 0) for e in entries)

    settings = db.get_settings()
    vat_payer = bool(int(settings.get("vat_payer", "0")))
    items = bil.apply_tariff(total_hours, tariff, year, month, vat_payer)
    totals = db.calc_totals(items, vat_payer)

    return render("billing_preview.html",
        customer=customer, tariff=tariff, year=year, month=month,
        month_name=_MONTH_NAMES[month],
        entries=entries, total_hours=total_hours,
        items=items, totals=totals, vat_payer=vat_payer,
        settings=settings, tt_available=tt.is_available(),
    )


@app.post("/billing/create")
async def billing_create(request: Request):
    form = await request.form()
    customer_id = int(form["customer_id"])
    year = int(form["year"])
    month = int(form["month"])

    customer = db.get_customer(customer_id)
    tariff = bil.get_tariff(customer)
    settings = db.get_settings()
    vat_payer = bool(int(settings.get("vat_payer", "0")))

    tt_customer = tariff.get("timetrack_customer") or customer["name"]
    date_from = f"{year}-{month:02d}-01"
    date_to = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    entries = tt.list_entries(customer=tt_customer, date_from=date_from, date_to=date_to)
    total_hours = sum(e.get("hours", 0) for e in entries)
    items = bil.apply_tariff(total_hours, tariff, year, month, vat_payer)

    number = db.next_invoice_number(settings)
    today = date.today()
    due = today + timedelta(days=int(settings.get("due_days", 14)))
    last_day = _calendar.monthrange(year, month)[1]

    inv_id = db.add_invoice(
        number=number,
        customer_id=customer_id,
        issue_date=today.isoformat(),
        due_date=due.isoformat(),
        vat_payer=vat_payer,
        currency="CZK",
        note="",
        duzp=f"{year}-{month:02d}-{last_day:02d}",
        snap=customer,
    )
    db.replace_items(inv_id, items)
    return RedirectResponse(url=f"/invoices/{inv_id}", status_code=303)


@app.get("/billing/report")
def billing_report_pdf(customer_id: int, year: int, month: int):
    customer = db.get_customer(customer_id)
    if not customer:
        return Response(status_code=404)
    tariff = bil.get_tariff(customer)
    tt_customer = tariff.get("timetrack_customer") or customer["name"]
    date_from = f"{year}-{month:02d}-01"
    date_to = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
    entries = tt.list_entries(customer=tt_customer, date_from=date_from, date_to=date_to)
    try:
        pdf_bytes = bil.generate_billing_report(entries, year, month)
    except RuntimeError as e:
        return Response(content=str(e), status_code=500)
    safe_name = customer["name"].replace(" ", "_")
    filename = f"report_{safe_name}_{year}_{month:02d}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- ARES ----------

@app.get("/ares/ico/{ico}")
def ares_ico(ico: str):
    result = ares_mod.lookup_ico(ico)
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "nenalezeno"}, status_code=404)


@app.get("/ares/search")
def ares_search(q: str):
    results = ares_mod.search_name(q)
    return JSONResponse(results)


# ---------- TimeTrack ----------

@app.get("/timetrack/entries")
def tt_entries(customer: str = "", date_from: str = "", date_to: str = ""):
    entries = tt.list_entries(
        customer=customer or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )
    return JSONResponse(entries)


# ---------- Settings ----------

IMPORT_DIR = Path(__file__).parent / "import"


def _find_customer(ico: str) -> dict | None:
    if not ico:
        return None
    for c in db.list_customers():
        if c.get("ico") == ico:
            return c
    return None


def _existing_invoice_numbers() -> set:
    return {inv["number"] for inv in db.list_invoices()}


@app.get("/import", response_class=HTMLResponse)
def import_form(request: Request):
    # Scan import folder for PDFs
    folder_pdfs = []
    if IMPORT_DIR.exists():
        existing_numbers = _existing_invoice_numbers()
        for pdf in sorted(IMPORT_DIR.glob("*.pdf")):
            folder_pdfs.append({"filename": pdf.name, "already_imported": False})
        # Quick-check already imported by trying to match filename to number pattern
        # We just show the list; detailed status shown after scan
    return render("import.html", folder_pdfs=folder_pdfs)


@app.get("/import/folder", response_class=HTMLResponse)
def import_folder_scan(request: Request):
    """Scan import folder, parse all PDFs, show results table."""
    if not IMPORT_DIR.exists():
        return render("import.html", folder_pdfs=[], error="Složka import/ neexistuje.")

    existing_numbers = _existing_invoice_numbers()
    all_customers = {c["ico"]: c for c in db.list_customers() if c.get("ico")}

    results = []
    for pdf in sorted(IMPORT_DIR.glob("*.pdf")):
        entry = {"filename": pdf.name, "error": None, "already_imported": False,
                 "number": "", "issue_date": "", "total": 0.0,
                 "customer_ico": "", "customer_name": "", "customer_id": None,
                 "warnings": []}
        try:
            parsed = parse_invoice_pdf(str(pdf))
            entry["number"] = parsed["number"]
            entry["issue_date"] = parsed["issue_date"]
            entry["total"] = parsed["total"]
            entry["customer_ico"] = parsed["customer_ico"]
            entry["warnings"] = parsed["warnings"]
            entry["already_imported"] = parsed["number"] in existing_numbers

            cust = all_customers.get(parsed["customer_ico"])
            if cust:
                entry["customer_name"] = cust["name"]
                entry["customer_id"] = cust["id"]
            elif parsed["customer_name"]:
                entry["customer_name"] = parsed["customer_name"]
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    return render("import_folder.html", results=results)


@app.post("/import/batch")
async def import_batch(request: Request):
    """Auto-import selected PDFs where customer is known."""
    form = await request.form()
    filenames = form.getlist("files")
    import_status = form.get("import_status", "paid")
    if import_status not in ("draft", "sent", "paid"):
        import_status = "paid"
    all_customers = {c["ico"]: c for c in db.list_customers() if c.get("ico")}
    imported, skipped, errors = [], [], []

    for fname in filenames:
        pdf_path = IMPORT_DIR / fname
        if not pdf_path.exists():
            errors.append(f"{fname}: soubor nenalezen")
            continue
        try:
            parsed = parse_invoice_pdf(str(pdf_path))
            cust = all_customers.get(parsed["customer_ico"])
            if not cust:
                skipped.append({"filename": fname, "reason": "Zákazník nenalezen v databázi"})
                continue
            if not parsed["number"] or not parsed["issue_date"]:
                skipped.append({"filename": fname, "reason": "Chybí číslo nebo datum faktury"})
                continue

            items = parsed.pop("items", [])
            inv_id = db.add_invoice(
                number=parsed["number"],
                customer_id=cust["id"],
                issue_date=parsed["issue_date"],
                due_date=parsed["due_date"] or parsed["issue_date"],
                vat_payer=parsed["vat_payer"],
                currency="CZK",
                note="",
            )
            db.replace_items(inv_id, items)
            db.update_invoice_status(inv_id, import_status)
            imported.append({"filename": fname, "number": parsed["number"], "inv_id": inv_id})
        except Exception as e:
            errors.append(f"{fname}: {e}")

    return render("import_batch_result.html",
        imported=imported, skipped=skipped, errors=errors)


@app.get("/import/file/{filename}", response_class=HTMLResponse)
def import_file_from_folder(filename: str, request: Request):
    """Parse one PDF from the import folder and show review page."""
    # Sanitize: no path traversal
    if "/" in filename or "\\" in filename or not filename.endswith(".pdf"):
        return RedirectResponse(url="/import")
    pdf_path = IMPORT_DIR / filename
    if not pdf_path.exists():
        return render("import.html", error=f"Soubor {filename} nenalezen v import/.")

    try:
        parsed = parse_invoice_pdf(str(pdf_path))
    except Exception as e:
        return render("import.html", error=f"Chyba při čtení PDF: {e}")

    ares_customer = None
    if parsed["customer_ico"]:
        ares_customer = ares_mod.lookup_ico(parsed["customer_ico"])

    existing_customer = _find_customer(parsed["customer_ico"])
    parsed_items = parsed.pop("items", [])
    return render("import_review.html",
        parsed=parsed,
        parsed_items=parsed_items,
        ares_customer=ares_customer,
        existing_customer=existing_customer,
        customers=db.list_customers(),
    )


@app.post("/import/parse", response_class=HTMLResponse)
async def import_parse(request: Request):
    form = await request.form()
    upload = form.get("pdf_file")
    if not upload or not upload.filename:
        return render("import.html", error="Vyber PDF soubor.")

    tmp = Path("/tmp") / upload.filename
    tmp.write_bytes(await upload.read())

    try:
        parsed = parse_invoice_pdf(str(tmp))
    except Exception as e:
        return render("import.html", error=f"Chyba při čtení PDF: {e}")
    finally:
        tmp.unlink(missing_ok=True)

    # ARES lookup zákazníka podle IČO
    ares_customer = None
    if parsed["customer_ico"]:
        ares_customer = ares_mod.lookup_ico(parsed["customer_ico"])

    # Hledáme zákazníka v DB
    existing_customer = None
    if parsed["customer_ico"]:
        for c in db.list_customers():
            if c.get("ico") == parsed["customer_ico"]:
                existing_customer = c
                break

    parsed_items = parsed.pop("items", [])
    return render("import_review.html",
        parsed=parsed,
        parsed_items=parsed_items,
        ares_customer=ares_customer,
        existing_customer=existing_customer,
        customers=db.list_customers(),
    )


@app.post("/import/save")
async def import_save(request: Request):
    form = await request.form()

    # Zákazník — buď existující nebo vytvoř nového
    customer_id = form.get("customer_id", "")
    if customer_id == "__new__":
        customer_id = db.add_customer(
            name=form.get("new_name", ""),
            ico=form.get("new_ico", ""),
            dic=form.get("new_dic", ""),
            street=form.get("new_street", ""),
            city=form.get("new_city", ""),
            zip_=form.get("new_zip", ""),
            email="",
            note="",
        )
    else:
        customer_id = int(customer_id)

    import sqlite3 as _sqlite3
    try:
        inv_id = db.add_invoice(
            number=form["number"],
            customer_id=customer_id,
            issue_date=form["issue_date"],
            due_date=form["due_date"],
            vat_payer=form.get("vat_payer") == "1",
            currency=form.get("currency", "CZK"),
            note=form.get("note", ""),
        )
    except _sqlite3.IntegrityError:
        return HTMLResponse(
            f'<p>Faktura č. <b>{form["number"]}</b> už v databázi existuje. '
            f'<a href="/">Zpět na přehled</a></p>', status_code=409)

    db.replace_items(inv_id, _parse_items(form))
    import_status = form.get("import_status", "paid")
    if import_status not in ("draft", "sent", "paid"):
        import_status = "paid"
    db.update_invoice_status(inv_id, import_status)

    return RedirectResponse(url=f"/invoices/{inv_id}", status_code=303)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, year: int = None):
    today = date.today()
    year = year or today.year
    available_years = list(range(today.year, today.year - 5, -1))

    by_month_raw = db.stats_by_month(year)
    months = {}
    for r in by_month_raw:
        m = r["ym"]
        if m not in months:
            months[m] = {"invoiced": 0.0, "paid": 0.0}
        months[m]["invoiced"] += r["total"]
        if r["status"] == "paid":
            months[m]["paid"] += r["total"]

    month_rows = []
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        d = months.get(ym, {"invoiced": 0.0, "paid": 0.0})
        month_rows.append({"month": m, "ym": ym, "invoiced": d["invoiced"], "paid": d["paid"]})

    by_customer = db.stats_by_customer(year)
    max_total = max((r["total"] for r in by_customer), default=1) or 1

    year_total_inv = sum(r["invoiced"] for r in month_rows)
    year_total_paid = sum(r["paid"] for r in month_rows)

    return render("stats.html",
        year=year,
        available_years=available_years,
        month_rows=month_rows,
        by_customer=by_customer,
        max_total=max_total,
        year_total_inv=year_total_inv,
        year_total_paid=year_total_paid,
    )


from bank_import import parse_airbank_csv, match_transactions as bank_match

BANK_DIR = Path(__file__).parent / "banka"


@app.get("/bank", response_class=HTMLResponse)
def bank_form(request: Request):
    folder_files = sorted(BANK_DIR.glob("*.csv")) if BANK_DIR.exists() else []
    return render("bank.html", folder_files=[f.name for f in folder_files])


@app.post("/bank/parse", response_class=HTMLResponse)
async def bank_parse(request: Request):
    form = await request.form()
    upload = form.get("csv_file")
    folder_file = form.get("folder_file", "")

    if folder_file:
        csv_path = BANK_DIR / folder_file
        if not csv_path.exists():
            return render("bank.html", folder_files=[], error="Soubor nenalezen.")
    elif upload and upload.filename:
        tmp = Path("/tmp") / upload.filename
        tmp.write_bytes(await upload.read())
        csv_path = tmp
    else:
        return render("bank.html", folder_files=[], error="Vyber soubor.")

    try:
        txs = parse_airbank_csv(str(csv_path))
    except Exception as e:
        return render("bank.html", folder_files=[], error=f"Chyba: {e}")
    finally:
        if upload and upload.filename:
            Path("/tmp" + "/" + upload.filename).unlink(missing_ok=True)

    invoices = db.list_invoices()
    results = bank_match(txs, invoices)

    matched = [r for r in results if r["match"]]
    unmatched = [r for r in results if not r["match"]]
    already_paid = [r for r in matched if r["match"]["status"] == "paid"]
    to_confirm = [r for r in matched if r["match"]["status"] != "paid"]

    return render("bank_review.html",
        to_confirm=to_confirm,
        already_paid=already_paid,
        unmatched=unmatched,
        source=folder_file or upload.filename,
    )


@app.post("/bank/confirm")
async def bank_confirm(request: Request):
    form = await request.form()
    inv_ids = form.getlist("inv_id")
    paid_dates = form.getlist("paid_date")
    count = 0
    for inv_id, paid_date in zip(inv_ids, paid_dates):
        if form.get(f"confirm_{inv_id}") == "1":
            db.update_invoice_status(int(inv_id), "paid", paid_date)
            count += 1
    return render("bank_result.html", count=count)


@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request):
    return render("settings.html", settings=db.get_settings(), stamp_exists=STAMP_PATH.exists())


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    db.save_settings(dict(form))
    return RedirectResponse(url="/settings", status_code=303)


STAMP_PATH = Path(__file__).parent / "data" / "stamp.png"


@app.post("/settings/stamp")
async def settings_stamp(request: Request):
    form = await request.form()
    upload = form.get("stamp_file")
    if upload and upload.filename:
        data = await upload.read()
        STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        STAMP_PATH.write_bytes(data)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/stamp/delete")
def settings_stamp_delete():
    STAMP_PATH.unlink(missing_ok=True)
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/settings/stamp")
def settings_stamp_view():
    if not STAMP_PATH.exists():
        return Response(status_code=404)
    return FileResponse(STAMP_PATH, media_type="image/png")


if __name__ == "__main__":
    import uvicorn
    print("\nFakturace běží na http://localhost:8732\n")
    uvicorn.run(app, host="127.0.0.1", port=8732)
