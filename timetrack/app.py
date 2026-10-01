"""TimeTrack - jednoduchý lokální time tracker.

Spuštění:  python app.py
Otevři:    http://localhost:8731
"""
import re
import sys
import unicodedata
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from pathlib import Path
from urllib.parse import quote
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # office_auth (local run)
import office_auth
import db
from pdf_export import generate_monthly_pdf, _duration_hours, _fmt_hours, MONTH_NAMES_CZ

app = FastAPI(title="TimeTrack")
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
office_auth.setup(app, "timetrack", jinja_env)


def _uid(request: Request) -> int:
    return request.state.user["id"]


def render(template_name: str, **context) -> HTMLResponse:
    template = jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context))


@app.get("/static/style.css")
def style_css():
    return FileResponse(STATIC_DIR / "style.css", media_type="text/css")

db.init_db()


def _annotate(entries: list[dict]) -> list[dict]:
    """Add computed duration string to each entry for display."""
    out = []
    for e in entries:
        e = dict(e)
        try:
            h = _duration_hours(e["start_time"], e["end_time"])
            e["duration"] = _fmt_hours(h)
            e["duration_hours"] = h
        except Exception:
            e["duration"] = "?"
            e["duration_hours"] = 0
        out.append(e)
    return out


@app.get("/", response_class=HTMLResponse)
def index(request: Request, year: int = None, month: int = None, customer: str = None):
    today = date.today()
    year = year or today.year
    month = month or today.month

    entries = db.list_entries(_uid(request), year=year, month=month, customer=customer or None)
    entries = _annotate(entries)
    total_hours = sum(e["duration_hours"] for e in entries)

    customers = db.list_customers()
    activities = db.list_activities()
    months = db.list_months(_uid(request))
    current_ym = f"{year:04d}-{month:02d}"
    if current_ym not in months:
        months = [current_ym] + months

    return render("index.html",
        entries=entries,
        customers=customers,
        activities=activities,
        months=months,
        year=year,
        month=month,
        month_name=MONTH_NAMES_CZ[month],
        selected_customer=customer or "",
        total_hours=_fmt_hours(total_hours),
        today_str=today.isoformat(),
        now_h=datetime.now().strftime("%H"),
        now_m=datetime.now().strftime("%M"),
    )


def _list_redirect(year: int, month: int, filter_customer: str = "") -> RedirectResponse:
    suffix = f"&customer={quote(filter_customer)}" if filter_customer else ""
    return RedirectResponse(url=f"/?year={year}&month={month}{suffix}", status_code=303)


@app.post("/entries/add")
def add_entry(
    request: Request,
    customer: str = Form(...),
    activity: str = Form(...),
    date_: str = Form(..., alias="date"),
    start_h: str = Form(...),
    start_m: str = Form(...),
    end_h: str = Form(...),
    end_m: str = Form(...),
    note: str = Form(""),
    year: int = Form(...),
    month: int = Form(...),
    filter_customer: str = Form(""),
):
    start_iso = f"{date_}T{start_h}:{start_m}:00"
    end_date = date_ if f"{end_h}:{end_m}" > f"{start_h}:{start_m}" else (date.fromisoformat(date_) + timedelta(days=1)).isoformat()
    end_iso = f"{end_date}T{end_h}:{end_m}:00"
    db.add_entry(_uid(request), customer, activity, start_iso, end_iso, note)
    return _list_redirect(year, month, filter_customer)


@app.post("/entries/{entry_id}/delete")
def delete_entry(request: Request, entry_id: int, year: int = Form(...), month: int = Form(...), filter_customer: str = Form("")):
    db.delete_entry(_uid(request), entry_id)
    return _list_redirect(year, month, filter_customer)


@app.get("/entries/{entry_id}/edit", response_class=HTMLResponse)
def edit_entry_form(request: Request, entry_id: int, customer: str = None):
    entry = db.get_entry(_uid(request), entry_id)
    if not entry:
        return RedirectResponse(url="/", status_code=303)
    start_dt = datetime.fromisoformat(entry["start_time"])
    end_dt = datetime.fromisoformat(entry["end_time"])
    return render("edit.html",
        entry=entry,
        date_val=start_dt.date().isoformat(),
        start_h=start_dt.strftime("%H"),
        start_m=start_dt.strftime("%M"),
        end_h=end_dt.strftime("%H"),
        end_m=end_dt.strftime("%M"),
        year=start_dt.year,
        month=start_dt.month,
        customers=db.list_customers(),
        activities=db.list_activities(),
        filter_customer=customer or "",
    )


@app.post("/entries/{entry_id}/duplicate")
def duplicate_entry(request: Request, entry_id: int, year: int = Form(...), month: int = Form(...), filter_customer: str = Form("")):
    entry = db.get_entry(_uid(request), entry_id)
    if entry:
        db.add_entry(_uid(request), entry["customer"], entry["activity"], entry["start_time"], entry["end_time"], entry["note"])
    return _list_redirect(year, month, filter_customer)


@app.post("/entries/{entry_id}/edit")
def edit_entry_submit(
    request: Request,
    entry_id: int,
    customer: str = Form(...),
    activity: str = Form(...),
    date_: str = Form(..., alias="date"),
    start_h: str = Form(...),
    start_m: str = Form(...),
    end_h: str = Form(...),
    end_m: str = Form(...),
    note: str = Form(""),
    year: int = Form(...),
    month: int = Form(...),
    filter_customer: str = Form(""),
):
    start_iso = f"{date_}T{start_h}:{start_m}:00"
    end_date = date_ if f"{end_h}:{end_m}" > f"{start_h}:{start_m}" else (date.fromisoformat(date_) + timedelta(days=1)).isoformat()
    end_iso = f"{end_date}T{end_h}:{end_m}:00"
    db.update_entry(_uid(request), entry_id, customer, activity, start_iso, end_iso, note)
    return _list_redirect(year, month, filter_customer)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render("settings.html",
        customers=db.list_customers(),
        activities=db.list_activities(),
    )


@app.post("/customers/add")
def add_customer(name: str = Form(...)):
    if name.strip():
        db.add_customer(name)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/customers/{customer_id}/delete")
def delete_customer(customer_id: int):
    db.delete_customer(customer_id)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/activities/add")
def add_activity(name: str = Form(...)):
    if name.strip():
        db.add_activity(name)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/activities/{activity_id}/delete")
def delete_activity(activity_id: int):
    db.delete_activity(activity_id)
    return RedirectResponse(url="/settings", status_code=303)


# ---------- Widget: stopky ----------

def _widget_state(user_id: int, saved: dict | None = None) -> dict:
    timers = db.list_timers(user_id)
    return {
        "timers": timers,
        "running": sum(1 for t in timers if t["state"] == "running"),
        "saved": saved,
        "now": datetime.now().replace(microsecond=0).isoformat(),
    }


@app.get("/widget", response_class=HTMLResponse)
def widget_page(request: Request):
    return render("widget.html",
        customers=db.list_customers(),
        activities=db.list_activities(),
        state=_widget_state(_uid(request)),
    )


@app.get("/widget/state")
def widget_state(request: Request):
    return JSONResponse(_widget_state(_uid(request)))


@app.post("/widget/start")
def widget_start(request: Request, customer: str = Form(...), activity: str = Form(""), note: str = Form("")):
    db.start_timer(_uid(request), customer, activity, note)
    return JSONResponse(_widget_state(_uid(request)))


@app.post("/widget/pause")
def widget_pause(request: Request, timer_id: int = Form(..., alias="id")):
    db.pause_timer(_uid(request), timer_id)
    return JSONResponse(_widget_state(_uid(request)))


@app.post("/widget/resume")
def widget_resume(request: Request, timer_id: int = Form(..., alias="id")):
    db.resume_timer(_uid(request), timer_id)
    return JSONResponse(_widget_state(_uid(request)))


@app.post("/widget/meta")
def widget_meta(request: Request, timer_id: int = Form(..., alias="id"), customer: str = Form(""),
                activity: str = Form(""), note: str = Form("")):
    db.update_timer_meta(_uid(request), timer_id, customer, activity, note)
    return JSONResponse(_widget_state(_uid(request)))


@app.post("/widget/stop")
def widget_stop(request: Request, timer_id: int = Form(..., alias="id")):
    saved = db.stop_timer(_uid(request), timer_id)
    return JSONResponse(_widget_state(_uid(request), saved))


@app.post("/widget/discard")
def widget_discard(request: Request, timer_id: int = Form(..., alias="id")):
    db.discard_timer(_uid(request), timer_id)
    return JSONResponse(_widget_state(_uid(request)))


@app.get("/api/day")
def api_day(request: Request, day: str = None):
    """Entries for one day (default today), grouped by customer — for reconcile.py."""
    day = day or date.today().isoformat()
    entries = db.entries_for_date(_uid(request), day)
    by_customer: dict[str, float] = {}
    for e in entries:
        by_customer[e["customer"]] = round(by_customer.get(e["customer"], 0.0) + e["hours"], 4)
    return JSONResponse({"date": day, "entries": entries, "by_customer": by_customer})


@app.get("/export/pdf")
def export_pdf(request: Request, year: int, month: int, customer: str = None):
    entries = db.list_entries(_uid(request), year=year, month=month, customer=customer or None)
    pdf_bytes = generate_monthly_pdf(entries, year, month)
    suffix = f"_{_slugify(customer)}" if customer else ""
    filename = f"vykaz_{year:04d}-{month:02d}{suffix}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
    )


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_")


if __name__ == "__main__":
    import uvicorn
    print("\nTimeTrack běží na http://localhost:8731\n")
    uvicorn.run(app, host="127.0.0.1", port=8731)
