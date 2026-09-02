"""SQLite database layer for the time tracker."""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
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
        # Timers — several may exist at once (parked), at most one 'running'.
        # A row lives only while a timer is active; STOP turns it into an entry.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL DEFAULT '',
                activity TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                banked_seconds INTEGER NOT NULL DEFAULT 0,
                segment_started_at TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL DEFAULT ''
            )
        """)
        # One-off migration from the earlier single-row timer_state table.
        try:
            old = conn.execute("SELECT * FROM timer_state WHERE id = 1").fetchone()
            if old:
                conn.execute(
                    """INSERT INTO timers
                       (customer, activity, note, started_at, banked_seconds,
                        segment_started_at, state, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (old["customer"], old["activity"], old["note"], old["started_at"],
                     old["banked_seconds"], old["segment_started_at"], old["state"],
                     old["started_at"]),
                )
            conn.execute("DROP TABLE timer_state")
        except sqlite3.OperationalError:
            pass
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


def entries_for_date(day: str) -> list[dict]:
    """Entries whose start_time falls on the given YYYY-MM-DD, with duration hours."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE start_time LIKE ? ORDER BY start_time",
            (f"{day}%",),
        ).fetchall()
    out = []
    for r in rows:
        e = dict(r)
        try:
            delta = datetime.fromisoformat(e["end_time"]) - datetime.fromisoformat(e["start_time"])
            e["hours"] = round(delta.total_seconds() / 3600, 4)
        except Exception:
            e["hours"] = 0.0
        out.append(e)
    return out


# --- Timers ---------------------------------------------------------------
#
# Several timers can exist and RUN at the same time — e.g. babysitting a deploy
# for one client while doing real work for another; both are billable. Nothing
# is auto-parked. PAUSE is an explicit choice when you genuinely stop a task.
#
# PAUSE banks the worked seconds so far; RESUME opens a new running segment.
# STOP writes one entry whose end_time is start_time + worked_seconds — the
# pauses are squeezed out so the duration stays honest for the PDF export
# and monthly totals. Concurrent timers therefore produce entries that overlap
# in wall-clock time, which is intentional.

def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _timer_worked_seconds(row, now: datetime) -> int:
    worked = int(row["banked_seconds"])
    if row["state"] == "running" and row["segment_started_at"]:
        worked += int((now - datetime.fromisoformat(row["segment_started_at"])).total_seconds())
    return max(0, worked)


def _timer_dict(row, now: datetime) -> dict:
    return {
        "id": row["id"],
        "customer": row["customer"],
        "activity": row["activity"],
        "note": row["note"],
        "started_at": row["started_at"],
        "state": row["state"],
        "worked_seconds": _timer_worked_seconds(row, now),
    }


def list_timers() -> list[dict]:
    """All active timers, running ones first, then by creation order."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM timers ORDER BY (state = 'running') DESC, id"
        ).fetchall()
    now = _now()
    return [_timer_dict(r, now) for r in rows]


def running_timer_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM timers WHERE state = 'running'").fetchone()[0]


def start_timer(customer: str, activity: str = "", note: str = "") -> int:
    """Start a new timer. Runs alongside any others. Returns the new timer id."""
    now = _now().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO timers
               (customer, activity, note, started_at, banked_seconds, segment_started_at, state, created_at)
               VALUES (?, ?, ?, ?, 0, ?, 'running', ?)""",
            (customer.strip(), activity.strip(), note.strip(), now, now, now),
        )
        conn.commit()
        return cur.lastrowid


def pause_timer(timer_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if not row or row["state"] != "running":
            return
        conn.execute(
            "UPDATE timers SET banked_seconds = ?, segment_started_at = '', state = 'paused' WHERE id = ?",
            (_timer_worked_seconds(row, _now()), timer_id),
        )
        conn.commit()


def resume_timer(timer_id: int):
    """Resume a parked timer. Other running timers keep running."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if not row or row["state"] == "running":
            return
        conn.execute(
            "UPDATE timers SET segment_started_at = ?, state = 'running' WHERE id = ?",
            (_now().isoformat(), timer_id),
        )
        conn.commit()


def update_timer_meta(timer_id: int, customer: str, activity: str, note: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE timers SET customer = ?, activity = ?, note = ? WHERE id = ?",
            (customer.strip(), activity.strip(), note.strip(), timer_id),
        )
        conn.commit()


def stop_timer(timer_id: int) -> dict | None:
    """Save one timer as an entry and remove it.
    Returns the created entry dict, or None if < 60 s worked / no customer."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if not row:
            return None
        worked = _timer_worked_seconds(row, _now())
        conn.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
        conn.commit()
    if worked < 60 or not row["customer"].strip():
        return None
    start_dt = datetime.fromisoformat(row["started_at"])
    end_iso = (start_dt + timedelta(seconds=worked)).isoformat(timespec="seconds")
    entry_id = add_entry(row["customer"], row["activity"],
                         start_dt.isoformat(timespec="seconds"), end_iso, row["note"])
    return {"id": entry_id, "customer": row["customer"], "activity": row["activity"],
            "note": row["note"], "start_time": start_dt.isoformat(timespec="seconds"),
            "end_time": end_iso, "worked_seconds": worked}


def discard_timer(timer_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
        conn.commit()
