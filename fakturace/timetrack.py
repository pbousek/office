"""Read-only access to TimeTrack SQLite database."""
import os
import sqlite3
from pathlib import Path

TIMETRACK_DB = Path(os.environ.get(
    "TIMETRACK_DB_PATH",
    str(Path(__file__).parent.parent / "timetrack" / "data" / "timetrack.db")
))


def _get_conn():
    if not TIMETRACK_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{TIMETRACK_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def is_available() -> bool:
    return TIMETRACK_DB.exists()


def list_entries(customer: str = None, date_from: str = None, date_to: str = None) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    try:
        query = "SELECT * FROM entries WHERE 1=1"
        params = []
        if customer:
            query += " AND customer = ?"
            params.append(customer)
        if date_from:
            query += " AND start_time >= ?"
            params.append(date_from)
        if date_to:
            query += " AND start_time < ?"
            params.append(date_to + "T23:59:59")
        query += " ORDER BY start_time"
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            e = dict(r)
            start = e["start_time"]
            end = e["end_time"]
            try:
                from datetime import datetime
                delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
                e["hours"] = round(delta.total_seconds() / 3600, 4)
            except Exception:
                e["hours"] = 0
            result.append(e)
        return result
    finally:
        conn.close()


def list_customers() -> list[str]:
    conn = _get_conn()
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT customer FROM entries ORDER BY customer COLLATE NOCASE"
        ).fetchall()
        return [r["customer"] for r in rows]
    finally:
        conn.close()
