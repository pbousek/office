"""Time entries and timers are per user; the widget offers recent tasks."""
import re
import sqlite3
from datetime import datetime, timedelta

import db
from conftest import login


def add(user_id: int, customer: str, activity: str, note: str, days_ago: int):
    start = (datetime.now() - timedelta(days=days_ago)).replace(hour=9, minute=0, second=0,
                                                                microsecond=0)
    db.add_entry(user_id, customer, activity, start.isoformat(),
                 (start + timedelta(hours=2)).isoformat(), note)
    return start


def month_url(start: datetime) -> str:
    return f"/?year={start.year}&month={start.month}"


def test_entries_are_private(admin):
    start = add(1, "Avantro", "operativa", "migrace", 0)
    admin.post("/admin/users/add", data={"username": "kamos", "password": "docasne123",
                                         "apps": ["timetrack"]})
    kamos = login("kamos", "docasne123", "kamosheslo")

    page = admin.get(month_url(start)).text
    entry_id = int(re.search(r"/entries/(\d+)/edit", page).group(1))
    assert "/edit" not in kamos.get(month_url(start)).text
    assert kamos.get(f"/entries/{entry_id}/edit").status_code == 303
    kamos.post(f"/entries/{entry_id}/delete", data={"year": start.year, "month": start.month})
    assert admin.get(f"/entries/{entry_id}/edit").status_code == 200

    admin.post("/widget/start", data={"customer": "X"})
    assert kamos.get("/widget/state").json()["timers"] == []


def test_entries_without_owner_go_to_first_account(fresh_db, monkeypatch):
    path = fresh_db / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT
                   NOT NULL, activity TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT
                   NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL)""")
    con.execute("INSERT INTO entries (customer, activity, start_time, end_time, created_at) "
                "VALUES ('A', 'x', '2026-06-01T09:00:00', '2026-06-01T10:00:00', '')")
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    owners = sqlite3.connect(path).execute("SELECT user_id FROM entries").fetchall()
    assert owners == [(1,)]


def test_widget_recent_tasks(admin):
    add(1, "Avantro", "operativa", "migrace db2", 1)
    add(1, "Avantro", "operativa", "migrace db2", 2)
    add(1, "DN", "vývoj", "API exportu", 1)
    add(1, "DN", "", "", 3)
    add(1, "Stary", "x", "", 20)
    add(2, "Cizi", "x", "cizi ukol", 1)

    recent = admin.get("/widget/state").json()["recent"]
    keys = [(r["customer"], r["note"]) for r in recent]
    assert keys.count(("Avantro", "migrace db2")) == 1      # merged
    assert ("Stary", "") not in keys                        # older than 14 days
    assert ("Cizi", "cizi ukol") not in keys                # other user's
    assert len(recent) == 3

    state = admin.post("/widget/start", data={"customer": "DN", "activity": "vývoj",
                                              "note": "API exportu"}).json()
    assert any(t["note"] == "API exportu" for t in state["timers"])
