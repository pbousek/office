"""SQLite database layer for fakturace."""
import sqlite3
from pathlib import Path
from datetime import date, timedelta
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "data" / "fakturace.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ico TEXT DEFAULT '',
                dic TEXT DEFAULT '',
                street TEXT DEFAULT '',
                city TEXT DEFAULT '',
                zip TEXT DEFAULT '',
                email TEXT DEFAULT '',
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                issue_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                vat_payer INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'CZK',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit TEXT NOT NULL DEFAULT 'hod',
                unit_price REAL NOT NULL DEFAULT 0,
                vat_rate INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            );
        """)
        # Default settings
        defaults = {
            "company_name": "",
            "company_street": "",
            "company_city": "",
            "company_zip": "",
            "company_ico": "",
            "company_dic": "",
            "company_iban": "",
            "company_bank": "",
            "company_phone": "",
            "company_email": "",
            "company_website": "",
            "company_account": "",
            "company_swift": "",
            "company_registration": "",
            "invoice_prefix": "",
            "invoice_next": "1",
            "due_days": "14",
            "vat_payer": "0",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_pass": "",
            "smtp_from": "",
            "email_signature": "",
            "email_template": "Dobrý den,\n\nv příloze zasílám fakturu č. {number} na částku {total} {currency} se splatností {due_date}.\n\nS pozdravem",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        # Migrations
        for migration in [
            "ALTER TABLE invoices ADD COLUMN paid_date TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN duzp TEXT DEFAULT ''",
            "ALTER TABLE customers ADD COLUMN tariff TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_name TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_ico TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_dic TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_street TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_city TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_zip TEXT DEFAULT ''",
            "ALTER TABLE invoices ADD COLUMN snap_email TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# --- Settings ---

def get_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def save_settings(data: dict):
    with get_conn() as conn:
        for k, v in data.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()


# --- Customers ---

def list_customers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


def get_customer(customer_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        return dict(row) if row else None


def add_customer(name, ico, dic, street, city, zip_, email, note) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO customers (name,ico,dic,street,city,zip,email,note) VALUES (?,?,?,?,?,?,?,?)",
            (name.strip(), ico.strip(), dic.strip(), street.strip(), city.strip(), zip_.strip(), email.strip(), note.strip()),
        )
        conn.commit()
        return cur.lastrowid


def update_customer(customer_id, name, ico, dic, street, city, zip_, email, note):
    with get_conn() as conn:
        conn.execute(
            "UPDATE customers SET name=?,ico=?,dic=?,street=?,city=?,zip=?,email=?,note=? WHERE id=?",
            (name.strip(), ico.strip(), dic.strip(), street.strip(), city.strip(), zip_.strip(), email.strip(), note.strip(), customer_id),
        )
        conn.commit()


def save_customer_tariff(customer_id: int, tariff_json: str):
    with get_conn() as conn:
        conn.execute("UPDATE customers SET tariff=? WHERE id=?", (tariff_json, customer_id))
        conn.commit()


def delete_customer(customer_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        conn.commit()


# --- Invoices ---

def next_invoice_number(settings: dict) -> str:
    prefix = settings.get("invoice_prefix", "")
    n = int(settings.get("invoice_next", "1"))
    year = date.today().year
    with get_conn() as conn:
        while True:
            number = f"{prefix}{year}{n:03d}"
            exists = conn.execute("SELECT 1 FROM invoices WHERE number=?", (number,)).fetchone()
            if not exists:
                break
            n += 1
    save_settings({"invoice_next": str(n + 1)})
    return number


def list_invoices() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT i.*, c.name AS customer_name,
                   COALESCE(SUM(ii.quantity * ii.unit_price * (1 + CASE WHEN i.vat_payer THEN ii.vat_rate / 100.0 ELSE 0 END)), 0) AS total
            FROM invoices i
            JOIN customers c ON c.id = i.customer_id
            LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            GROUP BY i.id
            ORDER BY i.issue_date DESC, i.number DESC
        """).fetchall()
        return [dict(r) for r in rows]


def stats_by_month(year: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT substr(i.issue_date, 1, 7) AS ym,
                   i.status,
                   COALESCE(SUM(ii.quantity * ii.unit_price * (1 + CASE WHEN i.vat_payer THEN ii.vat_rate / 100.0 ELSE 0 END)), 0) AS total
            FROM invoices i
            LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            WHERE substr(i.issue_date, 1, 4) = ? AND i.status != 'cancelled'
            GROUP BY ym, i.status
            ORDER BY ym
        """, (str(year),)).fetchall()
        return [dict(r) for r in rows]


def stats_by_customer(year: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.name AS customer_name,
                   COUNT(DISTINCT i.id) AS invoice_count,
                   COALESCE(SUM(ii.quantity * ii.unit_price * (1 + CASE WHEN i.vat_payer THEN ii.vat_rate / 100.0 ELSE 0 END)), 0) AS total,
                   COALESCE(SUM(CASE WHEN i.status='paid' THEN ii.quantity * ii.unit_price * (1 + CASE WHEN i.vat_payer THEN ii.vat_rate / 100.0 ELSE 0 END) ELSE 0 END), 0) AS paid
            FROM invoices i
            JOIN customers c ON c.id = i.customer_id
            LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            WHERE substr(i.issue_date, 1, 4) = ? AND i.status != 'cancelled'
            GROUP BY c.id
            ORDER BY total DESC
        """, (str(year),)).fetchall()
        return [dict(r) for r in rows]


def get_invoice(invoice_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT i.*,
                   COALESCE(NULLIF(i.snap_name,''), c.name)     AS customer_name,
                   COALESCE(NULLIF(i.snap_ico,''), c.ico)       AS customer_ico,
                   COALESCE(NULLIF(i.snap_dic,''), c.dic)       AS customer_dic,
                   COALESCE(NULLIF(i.snap_street,''), c.street) AS customer_street,
                   COALESCE(NULLIF(i.snap_city,''), c.city)     AS customer_city,
                   COALESCE(NULLIF(i.snap_zip,''), c.zip)       AS customer_zip,
                   COALESCE(NULLIF(i.snap_email,''), c.email)   AS customer_email
            FROM invoices i
            JOIN customers c ON c.id = i.customer_id
            WHERE i.id=?
        """, (invoice_id,)).fetchone()
        return dict(row) if row else None


def add_invoice(number, customer_id, issue_date, due_date, vat_payer, currency, note,
                duzp="", snap: dict | None = None) -> int:
    from datetime import datetime
    s = snap or {}
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO invoices
               (number,customer_id,issue_date,due_date,vat_payer,currency,note,duzp,created_at,
                snap_name,snap_ico,snap_dic,snap_street,snap_city,snap_zip,snap_email)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (number, customer_id, issue_date, due_date, int(vat_payer), currency, note,
             duzp or issue_date, datetime.now().isoformat(timespec="seconds"),
             s.get("name",""), s.get("ico",""), s.get("dic",""),
             s.get("street",""), s.get("city",""), s.get("zip",""), s.get("email","")),
        )
        conn.commit()
        return cur.lastrowid


def update_invoice(invoice_id, customer_id, issue_date, due_date, vat_payer, currency, note, duzp=""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE invoices SET customer_id=?,issue_date=?,due_date=?,vat_payer=?,currency=?,note=?,duzp=? WHERE id=?",
            (customer_id, issue_date, due_date, int(vat_payer), currency, note,
             duzp or issue_date, invoice_id),
        )
        conn.commit()


def update_invoice_status(invoice_id: int, status: str, paid_date: str = ""):
    with get_conn() as conn:
        conn.execute("UPDATE invoices SET status=?, paid_date=? WHERE id=?",
                     (status, paid_date, invoice_id))
        conn.commit()


def delete_invoice(invoice_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
        conn.commit()


# --- Invoice items ---

def get_items(invoice_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY position",
            (invoice_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def replace_items(invoice_id: int, items: list[dict]):
    with get_conn() as conn:
        conn.execute("DELETE FROM invoice_items WHERE invoice_id=?", (invoice_id,))
        for i, item in enumerate(items):
            conn.execute(
                """INSERT INTO invoice_items (invoice_id,description,quantity,unit,unit_price,vat_rate,position)
                   VALUES (?,?,?,?,?,?,?)""",
                (invoice_id, item["description"], item["quantity"], item["unit"],
                 item["unit_price"], item.get("vat_rate", 0), i),
            )
        conn.commit()


# --- Totals ---

def calc_totals(items: list[dict], vat_payer: bool) -> dict:
    """Return subtotal, vat_breakdown, total."""
    subtotal = sum(it["quantity"] * it["unit_price"] for it in items)
    if not vat_payer:
        return {"subtotal": subtotal, "vat": 0.0, "total": subtotal, "vat_lines": []}

    from collections import defaultdict
    by_rate = defaultdict(float)
    for it in items:
        base = it["quantity"] * it["unit_price"]
        by_rate[it["vat_rate"]] += base

    vat_lines = []
    total_vat = 0.0
    for rate in sorted(by_rate):
        base = by_rate[rate]
        vat = round(base * rate / 100, 2)
        total_vat += vat
        vat_lines.append({"rate": rate, "base": base, "vat": vat})

    return {"subtotal": subtotal, "vat": total_vat, "total": subtotal + total_vat, "vat_lines": vat_lines}


def clone_invoice(invoice_id: int, settings: dict) -> int:
    import re
    inv = get_invoice(invoice_id)
    items = get_items(invoice_id)
    today = date.today()
    due = today + timedelta(days=int(settings.get("due_days", 14)))
    number = next_invoice_number(settings)

    def bump_month(desc: str) -> str:
        def replace(m):
            month, year = int(m.group(1)), int(m.group(2))
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
            return f"{month}/{year}"
        return re.sub(r'\b(\d{1,2})/(\d{4})\b', replace, desc)

    customer = get_customer(inv["customer_id"])
    new_id = add_invoice(
        number=number,
        customer_id=inv["customer_id"],
        issue_date=today.isoformat(),
        due_date=due.isoformat(),
        vat_payer=bool(inv["vat_payer"]),
        currency=inv["currency"],
        note=inv.get("note", ""),
        duzp=today.isoformat(),
        snap=customer,
    )
    replace_items(new_id, [{**it, "description": bump_month(it["description"])} for it in items])
    return new_id
