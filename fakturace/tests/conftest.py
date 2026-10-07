"""Test setup: every test gets fresh, empty databases in a temp dir — the real
data in fakturace/data/ and data/auth.db is never opened."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
_boot = Path(tempfile.mkdtemp(prefix="fakturace-tests-"))

# Must happen before the app is imported: importing it initialises the DBs.
os.environ["AUTH_DB_PATH"] = str(_boot / "auth.db")
os.environ["TIMETRACK_DB_PATH"] = str(_boot / "timetrack.db")
for key in [k for k in os.environ if k.startswith("OFFICE_SMTP_")]:
    del os.environ[key]
sys.path[:0] = [str(APP_DIR), str(APP_DIR.parent)]

import db  # noqa: E402

db.DB_PATH = _boot / "fakturace.db"

import app as appmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from office_auth import store, web  # noqa: E402

PASSWORD = "testheslo1"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fakturace.db")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(appmod, "STAMP_PATH", tmp_path / "stamp.png")
    web._failures.clear()
    store.init_db()
    db.init_db()
    return tmp_path


def client() -> TestClient:
    return TestClient(appmod.app, follow_redirects=False)


def login(username: str, password: str, new_password: str | None = None) -> TestClient:
    c = client()
    assert c.post("/login", data={"username": username, "password": password}).status_code == 303
    if new_password:
        r = c.post("/account/password",
                   data={"current": password, "new": new_password, "new2": new_password})
        assert r.status_code == 303
    return c


@pytest.fixture
def admin() -> TestClient:
    return login("admin", "admin", PASSWORD)
