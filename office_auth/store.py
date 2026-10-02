"""Shared user/session store for TimeTrack and Fakturace (SQLite).

Both apps open the same database (AUTH_DB_PATH), so one account works in both;
per-user flags decide which app the user may enter.
"""
import hashlib
import hmac
import json
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
TRUSTED_DEVICE_DAYS = 30
PENDING_LOGIN_MINUTES = 10
RESET_MINUTES = 60
RECOVERY_CODES = 10
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

            CREATE TABLE IF NOT EXISTS password_resets (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trusted_devices (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        # Columns added after the first release.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for col, ddl in (
            ("email", "TEXT NOT NULL DEFAULT ''"),
            ("totp_secret", "TEXT NOT NULL DEFAULT ''"),
            ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("totp_last_step", "INTEGER NOT NULL DEFAULT 0"),
            ("recovery_codes", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "pending" not in cols:
            # pending=1: password OK, second factor not yet given.
            conn.execute("ALTER TABLE sessions ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
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
        for table in ("sessions", "password_resets", "trusted_devices"):
            conn.execute(f"DELETE FROM {table} WHERE expires_at < ?", (_now(),))
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


def _expires(**delta) -> str:
    return (datetime.now() + timedelta(**delta)).isoformat(timespec="seconds")


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


def get_user_by_login(login: str) -> dict | None:
    """Look a user up by username or (unique) e-mail address."""
    login = login.strip()
    if not login:
        return None
    user = get_user_by_name(login)
    if user:
        return user
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE",
                            (login,)).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None


def set_email(user_id: int, email: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (email.strip(), user_id))
        conn.commit()


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


def create_user(username: str, password: str, is_admin: bool, apps: set[str],
                email: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, email, password_hash, is_admin, can_timetrack,
                                  can_fakturace, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (username.strip(), email.strip(), hash_password(password), int(is_admin),
             int("timetrack" in apps), int("fakturace" in apps), _now()),
        )
        conn.commit()
        return cur.lastrowid


def update_user(user_id: int, username: str, email: str, is_admin: bool, active: bool,
                apps: set[str]):
    with get_conn() as conn:
        conn.execute(
            """UPDATE users SET username = ?, email = ?, is_admin = ?, active = ?,
                                can_timetrack = ?, can_fakturace = ?
               WHERE id = ?""",
            (username.strip(), email.strip(), int(is_admin), int(active),
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

def create_session(user_id: int, pending: bool = False) -> str:
    """Full session, or a short pending one waiting for the second factor."""
    token = secrets.token_urlsafe(32)
    expires = _expires(minutes=PENDING_LOGIN_MINUTES) if pending else _expires(days=SESSION_DAYS)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, created_at, expires_at, pending)
               VALUES (?, ?, ?, ?, ?)""",
            (_sha256(token), user_id, _now(), expires, int(pending)),
        )
        conn.commit()
    return token


def user_for_pending(token: str) -> dict | None:
    """User behind a pending (password OK, waiting for 2FA) login."""
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.pending = 1 AND s.expires_at > ? AND u.active = 1""",
            (_sha256(token), _now()),
        ).fetchone()
        return dict(row) if row else None


def user_for_session(token: str) -> dict | None:
    """Return the active user for a session token; extends the session (sliding expiry)."""
    if not token:
        return None
    th = _sha256(token)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS session_expires FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.pending = 0 AND s.expires_at > ? AND u.active = 1""",
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


# ---------- Password reset by e-mail ----------

def create_password_reset(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        conn.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_sha256(token), user_id, _expires(minutes=RESET_MINUTES)))
        conn.commit()
    return token


def user_for_reset(token: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM password_resets r JOIN users u ON u.id = r.user_id
               WHERE r.token_hash = ? AND r.expires_at > ? AND u.active = 1""",
            (_sha256(token), _now()),
        ).fetchone()
        return dict(row) if row else None


def consume_reset(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM password_resets WHERE token_hash = ?", (_sha256(token),))
        conn.commit()


# ---------- Second factor (TOTP + recovery codes) ----------

def set_totp_secret(user_id: int, secret: str):
    """Store a not-yet-confirmed secret (totp_enabled stays 0 until a code is verified)."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
                     (secret, user_id))
        conn.commit()


def enable_totp(user_id: int, step: int) -> list[str]:
    """Turn 2FA on; returns fresh recovery codes (shown once, stored hashed)."""
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODES)]
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET totp_enabled = 1, totp_last_step = ?, recovery_codes = ? WHERE id = ?",
            (step, json.dumps([_sha256(c) for c in codes]), user_id),
        )
        conn.commit()
    return codes


def disable_totp(user_id: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0,
                                recovery_codes = '[]' WHERE id = ?""", (user_id,))
        conn.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,))
        conn.commit()


def verify_second_factor(user: dict, code: str) -> bool:
    """Check a TOTP code (no replay of an already used step) or a recovery code
    (consumed on use)."""
    from . import totp

    code = code.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT totp_secret, totp_last_step, recovery_codes FROM users "
                           "WHERE id = ? AND totp_enabled = 1", (user["id"],)).fetchone()
        if not row:
            return False
        step = totp.match_step(row["totp_secret"], code)
        if step is not None:
            if step <= row["totp_last_step"]:
                return False
            conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
            conn.commit()
            return True
        hashes = json.loads(row["recovery_codes"] or "[]")
        h = _sha256(code)
        if h in hashes:
            hashes.remove(h)
            conn.execute("UPDATE users SET recovery_codes = ? WHERE id = ?",
                         (json.dumps(hashes), user["id"]))
            conn.commit()
            return True
    return False


def recovery_codes_left(user: dict) -> int:
    return len(json.loads(user.get("recovery_codes") or "[]"))


# ---------- Trusted devices ("remember me" for the second factor) ----------

def trust_device(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO trusted_devices (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_sha256(token), user_id, _now(), _expires(days=TRUSTED_DEVICE_DAYS)),
        )
        conn.commit()
    return token


def is_trusted_device(user_id: int, token: str) -> bool:
    if not token:
        return False
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM trusted_devices WHERE token_hash = ? AND user_id = ? AND expires_at > ?",
            (_sha256(token), user_id, _now()),
        ).fetchone() is not None


def forget_devices(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,))
        conn.commit()


def trusted_device_count(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM trusted_devices WHERE user_id = ? AND expires_at > ?",
                            (user_id, _now())).fetchone()[0]
