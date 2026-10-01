"""Shared user/session store for TimeTrack and Fakturace (SQLite).

Both apps open the same database (AUTH_DB_PATH), so one account works in both;
per-user flags decide which app the user may enter.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "AUTH_DB_PATH",
    str(Path(__file__).parent.parent / "data" / "auth.db"),
))

APPS = ("timetrack", "fakturace")
SESSION_DAYS = 30
DEFAULT_ADMIN = "admin"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                can_timetrack INTEGER NOT NULL DEFAULT 0,
                can_fakturace INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                api_token_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        # First start: one admin that must change its password on first login.
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            password = os.environ.get("OFFICE_ADMIN_PASSWORD") or DEFAULT_ADMIN
            conn.execute(
                # OR IGNORE: both apps may run this at the same first start.
                """INSERT OR IGNORE INTO users (username, password_hash, is_admin, can_timetrack,
                                                can_fakturace, must_change_password, created_at)
                   VALUES (?, ?, 1, 1, 1, 1, ?)""",
                (DEFAULT_ADMIN, hash_password(password), _now()),
            )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                              n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


# Verified against when the username does not exist, so timing doesn't leak it.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def authenticate(username: str, password: str) -> dict | None:
    user = get_user_by_name(username.strip())
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user["password_hash"]) or not user["active"]:
        return None
    return user


# ---------- Users ----------

def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_name(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


def active_admin_count(exclude_id: int = 0) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1 AND id != ?",
            (exclude_id,),
        ).fetchone()[0]


def create_user(username: str, password: str, is_admin: bool, apps: set[str]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, is_admin, can_timetrack,
                                  can_fakturace, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (username.strip(), hash_password(password), int(is_admin),
             int("timetrack" in apps), int("fakturace" in apps), _now()),
        )
        conn.commit()
        return cur.lastrowid


def update_user(user_id: int, username: str, is_admin: bool, active: bool, apps: set[str]):
    with get_conn() as conn:
        conn.execute(
            """UPDATE users SET username = ?, is_admin = ?, active = ?,
                                can_timetrack = ?, can_fakturace = ?
               WHERE id = ?""",
            (username.strip(), int(is_admin), int(active),
             int("timetrack" in apps), int("fakturace" in apps), user_id),
        )
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()


def set_password(user_id: int, password: str, must_change: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
            (hash_password(password), int(must_change), user_id),
        )
        conn.commit()


def delete_user(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


# ---------- Sessions ----------

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_sha256(token), user_id, _now(), expires),
        )
        conn.commit()
    return token


def user_for_session(token: str) -> dict | None:
    """Return the active user for a session token; extends the session (sliding expiry)."""
    if not token:
        return None
    th = _sha256(token)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS session_expires FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1""",
            (th, _now()),
        ).fetchone()
        if not row:
            return None
        # Bump at most once a day to keep writes rare.
        new_exp = datetime.now() + timedelta(days=SESSION_DAYS)
        if new_exp - datetime.fromisoformat(row["session_expires"]) > timedelta(days=1):
            conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                         (new_exp.isoformat(timespec="seconds"), th))
            conn.commit()
        return dict(row)


def delete_session(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))
        conn.commit()


def delete_user_sessions(user_id: int, keep_token: str = ""):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (user_id, _sha256(keep_token) if keep_token else ""))
        conn.commit()


# ---------- API tokens (scripts: reconcile.py, nudge.sh) ----------

def new_api_token(user_id: int) -> str:
    token = "oft_" + secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("UPDATE users SET api_token_hash = ? WHERE id = ?", (_sha256(token), user_id))
        conn.commit()
    return token


def revoke_api_token(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET api_token_hash = '' WHERE id = ?", (user_id,))
        conn.commit()


def user_for_api_token(token: str) -> dict | None:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE api_token_hash = ? AND active = 1", (_sha256(token),)
        ).fetchone()
        return dict(row) if row else None
