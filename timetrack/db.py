"""SQLite database layer for the time tracker."""
import sqlite3
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "data" / "timetrack.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL,
                activity TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_start
            ON entries(start_time)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_customer
            ON entries(customer)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        """)
        # Migrate existing customers/activities from entries on first run
        conn.execute("""
            INSERT OR IGNORE INTO customers (name)
            SELECT DISTINCT customer FROM entries WHERE customer != ''
        """)
        conn.execute("""
            INSERT OR IGNORE INTO activities (name)
            SELECT DISTINCT activity FROM entries WHERE activity != ''
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def add_entry(customer: str, activity: str, start_time: str, end_time: str, note: str = ""):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO entries (customer, activity, start_time, end_time, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (customer.strip(), activity.strip(), start_time, end_time, note.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid


def update_entry(entry_id: int, customer: str, activity: str, start_time: str, end_time: str, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE entries SET customer=?, activity=?, start_time=?, end_time=?, note=?
               WHERE id=?""",
            (customer.strip(), activity.strip(), start_time, end_time, note.strip(), entry_id),
        )
        conn.commit()


def delete_entry(entry_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        conn.commit()


def get_entry(entry_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return dict(row) if row else None


def list_entries(year: int = None, month: int = None, customer: str = None):
    query = "SELECT * FROM entries WHERE 1=1"
    params = []
    if year and month:
        prefix = f"{year:04d}-{month:02d}"
        query += " AND start_time LIKE ?"
        params.append(f"{prefix}%")
    if customer:
        query += " AND customer = ?"
        params.append(customer)
    query += " ORDER BY start_time DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def list_customers():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def add_customer(name: str):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO customers (name) VALUES (?)", (name.strip(),))
        conn.commit()


def delete_customer(customer_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        conn.commit()


def list_activities():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM activities ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def add_activity(name: str):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO activities (name) VALUES (?)", (name.strip(),))
        conn.commit()


def delete_activity(activity_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM activities WHERE id=?", (activity_id,))
        conn.commit()


def list_months():
    """Return distinct year-month strings present in the data, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(start_time, 1, 7) AS ym FROM entries ORDER BY ym DESC"
        ).fetchall()
        return [r["ym"] for r in rows]
