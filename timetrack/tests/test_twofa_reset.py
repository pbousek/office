"""Second factor (TOTP, recovery codes, remembered devices) and e-mail password reset."""
import re
import sqlite3

import pytest

from conftest import PASSWORD, client, login
from office_auth import mail, store, totp


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setenv("OFFICE_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("OFFICE_SMTP_FROM", "office@test")
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def enable_2fa(c) -> tuple[str, list[str]]:
    r = c.get("/account/2fa")
    assert r.status_code == 200 and "<svg" in r.text
    secret = re.search(r'user-select:all">([A-Z2-7]+)<', r.text).group(1)
    assert c.post("/account/2fa/enable", data={"code": "000000"}).status_code == 400
    r = c.post("/account/2fa/enable", data={"code": totp._code(secret, totp.current_step())})
    codes = re.findall(r"[0-9a-f]{8}-[0-9a-f]{8}", r.text)
    assert len(codes) == 10
    return secret, codes


def test_totp_login_flow(admin):
    secret, codes = enable_2fa(admin)
    step = totp.current_step()

    c = client()
    r = c.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/settings"})
    assert r.headers["location"].startswith("/login/2fa")
    assert c.get("/").headers["location"].startswith("/login?")  # pending is no session
    # the step used to enable 2FA cannot be replayed
    assert c.post("/login/2fa", data={"code": totp._code(secret, step)}).status_code == 401
    r = c.post("/login/2fa", data={"code": totp._code(secret, step + 1), "next": "/settings",
                                   "remember": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert "office_trusted" in c.cookies
    assert c.get("/settings").status_code == 200

    # remembered device skips the code
    c.post("/logout")
    r = c.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.headers["location"] == "/"

    # recovery codes work once
    f = client()
    f.post("/login", data={"username": "admin", "password": PASSWORD})
    assert f.post("/login/2fa", data={"code": codes[0]}).status_code == 303
    g = client()
    g.post("/login", data={"username": "admin", "password": PASSWORD})
    assert g.post("/login/2fa", data={"code": codes[0]}).status_code == 401


def test_admin_can_switch_2fa_off(admin):
    enable_2fa(admin)
    r = admin.post("/admin/users/1/2fa-off")
    assert "vypnuto" in r.text
    r = client().post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.headers["location"] == "/"


def test_password_reset_by_mail(admin, outbox):
    admin.post("/admin/users/add", data={"username": "oksi", "password": "docasne123",
                                         "apps": ["timetrack"]})
    r = admin.post("/account/email", data={"email": "aba@example.cz", "password": "spatne"})
    assert r.status_code == 400
    assert admin.post("/account/email",
                      data={"email": "aba@example.cz", "password": PASSWORD}).status_code == 303
    enable_2fa(admin)

    anon = client()
    assert "Zapomenuté heslo" in anon.get("/login").text
    assert "Zapomenuté heslo" in anon.post("/login", data={"username": "x", "password": "y"}).text
    # same answer for unknown users and users without e-mail; no mail sent
    assert anon.post("/password-reset", data={"login": "nikdo"}).status_code == 200
    anon.post("/password-reset", data={"login": "oksi"})
    assert outbox == []

    anon.post("/password-reset", data={"login": "aba@example.cz"})
    assert len(outbox) == 1 and outbox[0][0] == "aba@example.cz"
    link = re.search(r"http://localhost:8731(/password-reset/\S+)", outbox[0][2]).group(1)
    assert anon.get(link).status_code == 200
    assert anon.post(link, data={"new": "noveheslo99", "new2": "noveheslo99"}).status_code == 303
    assert anon.get(link).status_code == 404            # single use
    assert admin.get("/").status_code == 303            # sessions ended
    r = client().post("/login", data={"username": "admin", "password": "noveheslo99"})
    assert r.headers["location"].startswith("/login/2fa")  # reset does not bypass 2FA


def test_reset_requests_throttled(outbox):
    c = client()
    for _ in range(10):
        c.post("/password-reset", data={"login": "x"})
    assert c.post("/password-reset", data={"login": "x"}).status_code == 429


def test_auth_db_from_first_release_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "old-auth.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE
            COLLATE NOCASE, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            can_timetrack INTEGER NOT NULL DEFAULT 0, can_fakturace INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 1,
            api_token_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
    """)
    con.execute("INSERT INTO users (username, password_hash, is_admin, can_timetrack, "
                "can_fakturace, must_change_password, created_at) VALUES (?, ?, 1, 1, 1, 0, '')",
                ("aba", store.hash_password("stareheslo1")))
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", path)
    store.init_db()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(users)")}
    assert {"email", "totp_secret", "totp_enabled", "recovery_codes"} <= cols
    assert login("aba", "stareheslo1").get("/").status_code == 200
