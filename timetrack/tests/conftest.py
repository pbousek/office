"""Test setup: every test gets fresh, empty databases in a temp dir — the real
data in timetrack/data/ and data/auth.db is never opened."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
_boot = Path(tempfile.mkdtemp(prefix="timetrack-tests-"))

# Must happen before the app is imported: importing it initialises the DBs.
os.environ["AUTH_DB_PATH"] = str(_boot / "auth.db")
for key in [k for k in os.environ if k.startswith("OFFICE_SMTP_")]:
    del os.environ[key]
sys.path[:0] = [str(APP_DIR), str(APP_DIR.parent)]

import db  # noqa: E402

db.DB_PATH = _boot / "timetrack.db"

import app as appmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from office_auth import store, web  # noqa: E402

PASSWORD = "testheslo1"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "timetrack.db")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth.db")
    web._failures.clear()
    store.init_db()
    db.init_db()
    return tmp_path


def client() -> TestClient:
    return TestClient(appmod.app, follow_redirects=False)


def login(username: str, password: str, new_password: str | None = None) -> TestClient:
    """Log in; if the account must change its password, change it to new_password."""
    c = client()
    r = c.post("/login", data={"username": username, "password": password})
    assert r.status_code == 303, r.text
    if new_password:
        r = c.post("/account/password",
                   data={"current": password, "new": new_password, "new2": new_password})
        assert r.status_code == 303
    return c


@pytest.fixture
def admin() -> TestClient:
    """Logged-in initial admin (user id 1) with the password changed."""
    return login("admin", "admin", PASSWORD)
